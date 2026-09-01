"""LangGraph StateGraph 조립 (Issue #25: 노드 조립 + N13·N14 구현 + LLM 연결).

N1~N14 전체 노드를 다이어그램(Issue #25 첨부 그림)의 간선대로 배선한다.
N1~N3(슬롯 파싱 / 적합성 체크·하드 게이트 / 추가 정보 요청)는
``feat/N-N1-N3-node``에서 완성돼 이 브랜치로 들여왔다(PR #21 리뷰 피드백 -
프로필 하드 게이트 확장·만 나이 계산 - 반영판). 진입점은 N1(슬롯 파싱)이다.

세션 격리: 각 사용자는 ``session_id``로 분리되고, 서로 입력한 정보는
공유되지 않는다. N3(추가 정보 요청)가 LangGraph ``interrupt()``로 그래프
실행을 실제로 멈추고 사용자의 다음 발화를 기다려야 하므로(Edge E6: N3에서
재입력 -> N1로 새 user_input을 들고 루프), 이 그래프는 이제
``MemorySaver`` 체크포인터를 쓴다 - 이전 버전(N1~N3 미병합 시절)의 "체크포인터
없이 매 요청마다 새 state를 버린다"는 설계는 더 이상 맞지 않아 이 문단으로
교체한다. 체크포인터의 키는 ``thread_id=session_id``이므로, 서로 다른
session_id는 서로 다른 저장 슬롯을 쓰고, 한 세션 안에서만 인터럽트가
재개된다 - 여러 세션의 대화 상태가 서로 섞일 여지가 없다. 다만 이건
"슬롯 재입력 한 턴을 잠깐 기다리기" 용도이지 여러 턴에 걸친 일반 대화
기억이 아니다(멀티턴 대화 자체는 여전히 Gate 2 범위 밖 -
docs/PROJECT_COMPLIANCE.md "하이브리드 검색, Re-ranking, ... 대화 이력 ...
Baseline이 안정적으로 동작한 뒤 검토"). 또한 ``MemorySaver``는 프로세스
메모리에만 저장되고 디스크에 남지 않는다 - 서버가 재시작되면 재개 대기
중이던 세션은 사라진다(알려진 한계, 후속 작업에서 영속 체크포인터로 교체
검토 필요).

N4(policy_search)는 ``slot_schema.resolve_filter_slots()``를 유일한 프로필
조건 경로로 쓴다. 지역·연령은 기존 Chroma metadata 검색에, 성별·소득·취업·
장애의 명확한 조건은 정부24 raw sidecar 후처리에 반영한다. raw 조건이 없거나
의미가 불명확한 서비스는 후보를 유지한다(fail-open).
"""

from __future__ import annotations

import functools
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from rag_design.vector_store import ChromaVectorStore

from ..llm import LLMClient
from .nodes.answer_generation import generate_answer
from .nodes.benefit_calculator import calculate_benefit_amount
from .nodes.claim_extractor import LLMClaimExtractor, RuleBasedClaimExtractor
from .nodes.claim_plan import plan_claims
from .nodes.document_verification import verify_official_documents
from .nodes.duplicate_benefit import check_duplicate_benefit
from .nodes.eligibility_verdict import determine_eligibility
from .nodes.evidence_gate import evaluate_evidence, route_evidence_gate
from .nodes.final_verification import route_final_verification, verify_final_answer
from .nodes.general_law_reference_search import search_general_law_references
from .nodes.law_source_resolver import VectorStoreLawSourceResolver
from .nodes.policy_search import DEFAULT_TOP_K, MAX_TOP_K, MIN_TOP_K, search_policies
from .nodes.request_missing_slots import request_missing_slot_input
from .nodes.result_assembly import assemble_result
from .nodes.slot_completeness_gate import (
    check_slot_completeness,
    needs_general_law_reference,
    route_after_slot_completeness,
)
from .nodes.slot_parser import parse_slots
from .nodes.targeted_law_search import search_targeted_laws
from ..timing import timed_node
from .policy_conditions import SupportConditionsIndex
from .state import GraphState

_SlotGateRoute = Literal["sufficient", "general_law", "request_input"]


def _route_after_slot_completeness_gate(state: GraphState) -> _SlotGateRoute:
    """N2 이후 3갈래 분기(E3/E4/E5)를 위해 두 판정 함수를 하나로 합친다.

    slot_completeness_gate.py는 "충분/부족"(route_after_slot_completeness)과
    "지역이 부족해서 N2a가 필요한가"(needs_general_law_reference)를 각각
    별개 헬퍼로 노출한다 - 그래프 조립 담당(이 파일)이 실제 conditional edge
    라우팅 키로 합치는 몫을 맡긴다(두 파일의 모듈 docstring이 명시).
    """

    if route_after_slot_completeness(state) == "sufficient":
        return "sufficient"  # E3: 정책 검색(N4)으로.
    if needs_general_law_reference(state):
        return "general_law"  # E4: 지역이 부족 -> N2a 먼저.
    return "request_input"  # E4: 지역 외 프로필 슬롯 부족 -> 바로 N3.


def _await_missing_slot_input(state: GraphState) -> dict:
    """N3(request_missing_slot_input)을 감싸 실제 interrupt/resume을 구현한다.

    request_missing_slots.py의 모듈 docstring이 "실제 LangGraph
    interrupt/checkpointer 연결은 그래프 조립 단계(후속 작업)의 책임"이라고
    명시한 부분이 이 함수다.

    LangGraph interrupt의 표준 동작: ``interrupt()``를 호출하면 그래프
    실행이 멈추고 ``graph.invoke()``가 ``"__interrupt__"`` 키를 포함한
    결과를 즉시 돌려준다. 이후 ``graph.invoke(Command(resume=답변), config)``
    로 재개하면 **이 노드 함수 전체가 처음부터 다시 실행**되고, 이번에는
    ``interrupt()`` 호출 지점이 멈추지 않고 재개 값을 그대로 반환한다. 이
    함수가 멈추기 전에 하는 일(``request_missing_slot_input`` 호출)은 이
    노드에 들어올 때의 ``state``만 읽는 순수 계산이라 두 번 실행돼도
    안전하다 - state는 재개 시점까지 바뀌지 않으므로 매번 같은
    ``followup_question``/``slot_ask_counts`` 증가분을 계산할 뿐, 이중으로
    올라가지 않는다.
    """

    update = request_missing_slot_input(state)
    resumed_user_input = interrupt(update["followup_question"])
    # 여기 도달했다는 것은 재개됐다는 뜻이다. 다음 노드(N1, Edge E6)가 새
    # 발화를 파싱할 수 있도록 user_input을 갱신하고, 더 이상 입력 대기
    # 상태가 아니므로 needs_input을 내린다.
    return {**update, "needs_input": False, "user_input": resumed_user_input}


def _abstain_insufficient_evidence(state: GraphState) -> dict:
    """N7이 evidence_gate_verdict="fail"로 끝낸 경우(다이어그램 E14)의
    종착 노드.

    N13/N14를 거치지 않는다 - N7이 이미 "abstain"으로 확정했으므로(N7의
    evaluate_evidence가 계산한 state["abstention_decision"]), 여기서 답변을
    새로 만들지 않고 고정 안내 문구만 낸다. N14의 abstain 경로와 같은 문구를
    쓴다(final_verification.py의 ``_ABSTAIN_MESSAGE``와 의도적으로 동일 -
    사용자 입장에서는 "N7에서 막혔는지 N14에서 막혔는지" 구분할 필요가 없다).
    """
    node_trace = list(state.get("node_trace", []))
    node_trace.append("N7-abstain")
    return {
        "final_answer": (
            "죄송합니다. 확인된 근거가 부족해 답변을 제공할 수 없습니다. "
            "관련 기관의 공식 안내를 확인해 주세요."
        ),
        "final_citations": [],
        "answer_status": "abstained",
        "node_trace": node_trace,
    }


def build_graph(
    store: ChromaVectorStore,
    *,
    llm_client: LLMClient | None = None,
    support_conditions: SupportConditionsIndex | None = None,
) -> Any:
    """N1~N14를 다이어그램 간선대로 배선한 컴파일된 LangGraph 그래프를 만든다.

    store: N4/N5(법령명 resolver)/N6(재시도)/N8/N9/N10/N11/N12가 공유하는
    ChromaVectorStore.
    임베딩 모델 재로딩을 피하려고 그래프 조립 시 한 번만 만들어 여기로
    주입한다(각 노드 docstring에 이미 명시된 관례).

    llm_client: N1(슬롯 추출)/N5(claim 추출)/N9(위반 사유 자연어화)/N10(금액 추출)/
    N13(답변 생성)이 선택적으로 쓰는 LLM 클라이언트. None이면(기본값) 전부
    규칙 기반 결과로만 동작한다 - RunPod 엔드포인트가 아직 없어도(2026-08-31
    기준, docs/RUNPOD_SETUP_DRAFT.md) 그래프 전체가 예외 없이 끝까지
    실행된다. 테스트에서는 ``src.rag_chatbot.llm.FakeLLMClient`` /
    ``FailingLLMClient``로 대체할 수 있다.

    support_conditions: N4가 semantic 후보를 후처리할 때 쓰는 정부24 raw
    지원조건 index. None이면 모든 후보를 유지한다.

    반환된 그래프는 ``MemorySaver`` 체크포인터로 컴파일되므로, N3에서
    인터럽트가 걸린 세션을 재개하려면(``resume_graph``) 첫 호출
    (``run_graph``)과 같은 컴파일된 그래프 인스턴스를 계속 재사용해야 한다
    - 체크포인터가 그래프 객체에 딸려 있어서, 매번 ``build_graph``를 새로
    부르면 이전 인터럽트 상태를 잃는다.
    """

    extractor = (
        LLMClaimExtractor(llm_client)
        if llm_client is not None
        else RuleBasedClaimExtractor()
    )
    law_resolver = VectorStoreLawSourceResolver(store)

    graph: StateGraph = StateGraph(GraphState)

    # --- N1~N3: 슬롯 파싱 / 적합성 체크 / 추가 정보 요청 -------------------
    graph.add_node("slot_parser", timed_node("slot_parser", functools.partial(parse_slots, llm_client=llm_client)))
    graph.add_node("slot_completeness_gate", timed_node("slot_completeness_gate", check_slot_completeness))
    graph.add_node("general_law_reference_search", timed_node("general_law_reference_search", search_general_law_references))
    graph.add_node("request_missing_slots", timed_node("request_missing_slots", _await_missing_slot_input))

    # --- N4~N14: 기존 정책 검색 ~ 최종 검증 --------------------------------
    graph.add_node(
        "policy_search",
        timed_node(
            "policy_search",
            functools.partial(
                search_policies,
                store=store,
                support_conditions=support_conditions,
            ),
        ),
    )
    graph.add_node(
        "claim_plan",
        timed_node(
            "claim_plan",
            functools.partial(
                plan_claims,
                extractor=extractor,
                law_resolver=law_resolver,
            ),
        ),
    )
    graph.add_node("document_verification", timed_node("document_verification", functools.partial(verify_official_documents, store=store))
    )
    graph.add_node("evidence_gate", timed_node("evidence_gate", evaluate_evidence))
    graph.add_node("targeted_law_search", timed_node("targeted_law_search", functools.partial(search_targeted_laws, search=store.search)))
    graph.add_node("eligibility_verdict", timed_node("eligibility_verdict", functools.partial(determine_eligibility, store=store, llm_client=llm_client),))
    graph.add_node("benefit_calculator", timed_node("benefit_calculator", functools.partial(calculate_benefit_amount, store=store, llm_client=llm_client),))
    graph.add_node("duplicate_benefit", timed_node("duplicate_benefit", functools.partial(check_duplicate_benefit, store=store)))
    graph.add_node("result_assembly", timed_node("result_assembly", functools.partial(assemble_result, store=store)))
    graph.add_node("answer_generation", timed_node("answer_generation", functools.partial(generate_answer, llm_client=llm_client)))
    graph.add_node("final_verification", timed_node("final_verification", verify_final_answer))
    graph.add_node("abstain_insufficient_evidence", timed_node("abstain_insufficient_evidence", _abstain_insufficient_evidence))

    graph.set_entry_point("slot_parser")  # E1

    graph.add_edge("slot_parser", "slot_completeness_gate")  # E2

    graph.add_conditional_edges(
        "slot_completeness_gate",
        _route_after_slot_completeness_gate,
        {
            "sufficient": "policy_search",  # E3
            "general_law": "general_law_reference_search",  # E4 (지역 부족)
            "request_input": "request_missing_slots",  # E4 (프로필 슬롯 부족)
        },
    )
    graph.add_edge("general_law_reference_search", "request_missing_slots")  # E5
    graph.add_edge("request_missing_slots", "slot_parser")  # E6 재입력 -> N1

    # N5(claim_plan)는 현재 모든 claim의 doc_check_required를 True로 고정하고
    # (claim_plan.py "판정 기준 미정이므로 보수적으로 항상 True로 둔다"),
    # N6(document_verification)가 그중 doc_check_required=True인 claim만
    # 내부에서 골라 검증하고 나머지는 그대로 통과시킨다. 그래서 그래프
    # 수준에서는 E8/E9를 조건 분기로 나누지 않고 N5 -> N6 -> N7로 한 줄로
    # 잇는다 - E9(문서 대조 불필요)는 N6 함수 내부에서 이미 처리된다.
    graph.add_edge("policy_search", "claim_plan")  # E7 subsidy_chunks
    graph.add_edge("claim_plan", "document_verification")  # E8 (내부적으로 E9도 처리)
    graph.add_edge("document_verification", "evidence_gate")  # E10

    graph.add_conditional_edges(
        "evidence_gate",
        route_evidence_gate,
        {
            "document_verification": "document_verification",  # E11 근거 부족 재검증
            "targeted_law_search": "targeted_law_search",  # E12 법령 근거 부족
            "eligibility_verdict": "eligibility_verdict",  # E15 검증 통과
            "terminal": "abstain_insufficient_evidence",  # E14 검증 실패
        },
    )
    graph.add_edge("targeted_law_search", "evidence_gate")  # E13 재검증

    graph.add_edge("eligibility_verdict", "benefit_calculator")  # E16
    graph.add_edge("eligibility_verdict", "duplicate_benefit")  # E17
    # result_assembly는 benefit_calculator/duplicate_benefit 두 선행 노드가
    # 모두 끝난 뒤 한 번만 실행된다(LangGraph의 기본 join 동작 - 두 노드가
    # 같은 superstep에서 병렬 실행되고, 두 incoming edge가 모두 해소된 다음
    # superstep에 result_assembly가 실행된다).
    graph.add_edge("benefit_calculator", "result_assembly")  # E18
    graph.add_edge("duplicate_benefit", "result_assembly")  # E19

    graph.add_edge("result_assembly", "answer_generation")  # E20 assembled_result
    graph.add_edge("answer_generation", "final_verification")  # E21 draft_answer, citations

    graph.add_conditional_edges(
        "final_verification",
        route_final_verification,
        # complete/partial 둘 다 다이어그램의 "완료 / 부분 응답" 종착지이고,
        # abstained는 "확인 불가 / 부분 응답" 종착지다 - verify_final_answer가
        # 이미 최종 answer_status/final_answer를 다 채워뒀으므로 두 경로 모두
        # 추가 노드 없이 END로 끝낸다.
        {"terminal_success": END, "terminal_insufficient": END},
    )
    graph.add_edge("abstain_insufficient_evidence", END)

    return graph.compile(checkpointer=MemorySaver())


def run_graph(
    graph: Any,
    *,
    user_input: str,
    session_id: str,
    slots: dict | None = None,
    top_k: int = DEFAULT_TOP_K,
    as_of=None,
    safety_blocked: bool = False,
) -> dict:
    """session_id별 새 대화를 시작한다(첫 턴, Edge E1).

    user_input: 이번 턴 사용자 발화. N1(slot_parser)이 여기서 슬롯을 뽑는다.
    session_id: 사용자별 요청 격리 키. GraphState["query_id"]로 흘려보내
    vectorDB 검색 로그(RetrievedChunk.query_id)에 남고, 동시에 LangGraph
    체크포인터의 ``thread_id``로도 쓰인다 - 서로 다른 session_id는 서로
    다른 저장 슬롯이라 대화 상태가 섞이지 않는다(파일 상단 "세션 격리" 참고).
    slots: 이미 알고 있는 슬롯이 있으면(예: 이전 채널에서 수집한 프로필)
    초기값으로 미리 채워 넣는다. 생략하면 빈 SlotState로 시작해 N1이
    user_input만으로 채운다.
    top_k: N4 정책 검색에서 반환할 후보 수. 프론트엔드의 "정책 후보 수"
    설정값을 전달하며 1~20 범위만 허용한다.
    safety_blocked: N7(evidence_gate.evaluate_evidence)이 요구하는 안전
    신호. 아직 이 값을 실제로 계산하는 안전 필터 노드가 없어(N1~N3는 슬롯
    파싱/게이트일 뿐 안전성 검사가 아니다), 기본값 False는 "안전하다고
    확인됐다"가 아니라 "아직 아무도 검사하지 않았다"는 뜻이다 - 실제 서비스
    연결 전에 반드시 재검토해야 한다(알려진 한계, 숨기지 않음).

    반환값에 ``"__interrupt__"`` 키가 있으면 N3가 추가 정보를 요청하며
    멈춘 것이다 - 그 값(리스트의 첫 Interrupt 객체 ``.value``)이
    사용자에게 보여줄 질문이고, 사용자의 답변은 ``resume_graph()``로
    넘긴다.
    """
    from datetime import date as _date

    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise ValueError("top_k must be an integer")
    if not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}")
    if as_of is not None and type(as_of) is not _date:
        raise ValueError("as_of must be a date")

    initial_state: GraphState = {
        "query_id": session_id,
        "policy_top_k": top_k,
        "as_of": as_of if as_of is not None else _date.today(),
        "user_input": user_input,
        "slots": dict(slots) if slots else {},
        "slot_ask_counts": {},
        "node_trace": [],
        "safety_blocked": safety_blocked,
    }
    config = {"configurable": {"thread_id": session_id}}
    return graph.invoke(initial_state, config=config)


def resume_graph(graph: Any, *, session_id: str, user_input: str) -> dict:
    """N3(request_missing_slots)에서 멈춘 세션을 재개한다(Edge E6).

    ``run_graph()`` (또는 이전 ``resume_graph()``) 결과에
    ``"__interrupt__"``가 있었던 session_id에 대해서만 호출할 수 있다 -
    체크포인터가 해당 ``thread_id``로 저장해 둔 이전 진행 상태가 없으면
    LangGraph가 에러를 낸다. 재개 시 그래프는 멈췄던 ``request_missing_slots``
    노드부터 이어서 실행되고(``_await_missing_slot_input``의
    ``interrupt()`` 호출이 이 ``user_input``을 그대로 돌려받는다), 이후
    Edge E6을 따라 N1(slot_parser)로 이동해 새 발화를 기존 슬롯에 병합한다.
    """
    config = {"configurable": {"thread_id": session_id}}
    return graph.invoke(Command(resume=user_input), config=config)


__all__ = ["build_graph", "run_graph", "resume_graph"]
