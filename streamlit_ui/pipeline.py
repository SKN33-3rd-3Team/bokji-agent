"""그래프 배선: N1 → N12 를 한 곳에 모은다.

프로젝트 규칙(`docs/PROJECT_COMPLIANCE.md`)의 "그래프 배선은 Streamlit 으로
통일" 방침에 따라 LangGraph graph builder 패키지를 따로 두지 않고 노드
호출/분기를 ``run_pipeline()`` 한 곳에 모은다. 나중에 별도 패키지로 옮기더라도
노드 시그니처는 그대로 쓴다.

- N13(답변 생성)·N14(최종 검증) 노드는 아직 없다. 지금은 N12의
  ``assembled_result`` 를 규칙 기반으로 렌더링한다.  # TODO(N13/N14)
- LLM 연동(RunPod)·LangChain 배선은 추후. 지금은 ``llm_client=None`` 으로
  규칙 기반 판정만 돈다(노드가 그렇게 동작하도록 이미 설계됨).
- 실제 LLM 기반 ClaimExtractor / LawSourceResolver 도 추후. 지금은 원문
  발췌 기반 ``RuleBasedClaimExtractor`` 를 주입한다.  # TODO(N5 LLM)
"""

from __future__ import annotations

import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from rag_chatbot.graph.nodes import (
    assemble_result,               # N12
    calculate_benefit_amount,      # N10
    check_duplicate_benefit,       # N11
    check_slot_completeness,       # N2
    determine_eligibility,         # N9
    evaluate_evidence,             # N7
    needs_general_law_reference,   # N2 분기 헬퍼
    parse_slots,                   # N1
    plan_claims,                   # N5
    request_missing_slot_input,    # N3
    route_after_slot_completeness,  # N2 분기 헬퍼
    route_evidence_gate,           # N7 분기 헬퍼
    search_general_law_references,  # N2a
    search_policies,               # N4
    search_targeted_laws,          # N8
    verify_official_documents,     # N6
)
from rag_design.vector_store import ChromaVectorStore

from .claim_extractor import RuleBasedClaimExtractor
from .constants import DEFAULT_TOP_K, MAX_EVIDENCE_LOOPS


@dataclass
class PipelineResult:
    kind: str  # needs_input | answer | abstain | no_candidates | error
    state: dict = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)
    followup_question: str | None = None
    general_law_references: list = field(default_factory=list)
    abstention_reason: str | None = None
    error: str | None = None
    elapsed_sec: float = 0.0
    min_benefit: int = 0   # 사이드바 "최소 지원금(월)" 값. 표시/강조에만 사용.


def _run_evidence_loop(
    state: dict, store: ChromaVectorStore, trace: list[str]
) -> str:
    """N7 ↔ N6/N8 재검색 루프. 최종 라우팅 키("pass" | "fail")를 반환한다."""

    for _ in range(MAX_EVIDENCE_LOOPS):
        state.update(evaluate_evidence(state))  # N7
        trace.append(f"N7({state['evidence_gate_verdict']})")
        route = route_evidence_gate(state)

        if route == "eligibility_verdict":  # verdict == pass
            return "pass"
        if route == "terminal":  # verdict == fail
            return "fail"

        if route == "document_verification":  # insufficient_document
            retry = state.get("doc_retry_count", 1)
            missing = set(state.get("missing_document_claim_ids", []))
            for claim in state.get("claim_plan", []):
                if claim.get("claim_id") in missing:
                    claim["doc_retry_count"] = retry
            state.update(verify_official_documents(state, store=store))  # N6 재검색
            trace.append("N6*")
            continue

        if route == "targeted_law_search":  # insufficient_law
            try:
                state.update(search_targeted_laws(state, search=store.search))  # N8
                trace.append("N8")
            except Exception as exc:  # noqa: BLE001
                # 현재 ClaimExtractor 는 law_check_required=False 만 내므로
                # 이 경로는 사실상 도달하지 않는다. 방어적으로 종료.
                state["_law_search_error"] = f"{type(exc).__name__}: {exc}"
                trace.append("N8(error)")
                return "fail"
            continue

        return "fail"

    trace.append("loop-budget-exhausted")
    return "fail"


def run_pipeline(
    *,
    user_input: str,
    slots: dict,
    slot_ask_counts: dict,
    as_of: date,
    store: ChromaVectorStore,
    top_k: int = DEFAULT_TOP_K,
    extra_interests: list[str] | None = None,
    min_benefit: int = 0,
    on_step: Callable[[str], None] | None = None,
) -> PipelineResult:
    """그래프를 한 턴 실행하고 소요 시간을 기록한다.

    min_benefit(최소 지원금)은 그래프 판정에는 관여하지 않고, 결과 화면에서
    금액이 확인된 제도를 기준선과 비교해 강조하는 데만 쓴다(현재 샘플 데이터에
    구조화 금액이 없어 실질 필터로는 아직 동작하지 않음).

    on_step(선택)은 단계가 넘어갈 때마다 사람이 읽는 한글 문구로 호출된다.
    Streamlit ``st.status`` 진행 표시에 흘려 넣는 용도라 그래프 로직과 무관하다.
    """

    started = time.perf_counter()
    result = _run_pipeline_inner(
        user_input=user_input,
        slots=slots,
        slot_ask_counts=slot_ask_counts,
        as_of=as_of,
        store=store,
        top_k=top_k,
        extra_interests=extra_interests or [],
        on_step=on_step,
    )
    result.elapsed_sec = time.perf_counter() - started
    result.min_benefit = min_benefit
    return result


def _run_pipeline_inner(
    *,
    user_input: str,
    slots: dict,
    slot_ask_counts: dict,
    as_of: date,
    store: ChromaVectorStore,
    top_k: int,
    extra_interests: list[str],
    on_step: Callable[[str], None] | None = None,
) -> PipelineResult:
    step = on_step or (lambda _msg: None)
    trace: list[str] = []
    state: dict = {
        "query_id": f"q-{uuid.uuid4().hex[:12]}",
        "user_input": user_input,
        "as_of": as_of,
        "slots": dict(slots),
        "slot_ask_counts": dict(slot_ask_counts),
        "safety_blocked": False,   # 안전 판정 노드는 아직 없음. 명시적 False.
        "law_chunks": [],
    }

    try:
        step("입력하신 조건을 분석하고 있어요")
        # N1 슬롯 파싱
        state.update(parse_slots(state))
        trace.append("N1")

        # 검색 튜닝: 사이드바에서 고른 관심 분야를 slots.interests 에 합친다.
        # (N2 게이트는 interests 를 검사하지 않고, N2a/N4 검색 쿼리에만 쓰인다.)
        if extra_interests:
            merged = dict(state.get("slots", {}))
            interests = list(merged.get("interests", []))
            for item in extra_interests:
                if item not in interests:
                    interests.append(item)
            merged["interests"] = interests
            state["slots"] = merged

        # N2 적합성 게이트
        state.update(check_slot_completeness(state))
        trace.append("N2")

        if route_after_slot_completeness(state) == "insufficient":
            step("추가로 확인할 정보를 정리하고 있어요")
            # N2a 지역이 빠졌을 때만 일반 법령 참고 검색
            if needs_general_law_reference(state):
                state.update(search_general_law_references(state))
                trace.append("N2a")
            # N3 되묻기
            state.update(request_missing_slot_input(state))
            trace.append("N3")
            return PipelineResult(
                kind="needs_input",
                state=state,
                trace=trace,
                followup_question=state.get("followup_question"),
                general_law_references=state.get("general_law_references", []),
            )

        # N4 정책 검색
        step("받을 수 있는 지원 제도를 검색하고 있어요")
        state.update(search_policies(state, store, top_k=top_k))
        trace.append(f"N4({len(state.get('subsidy_chunks', []))} chunks)")
        if not state.get("subsidy_chunks"):
            return PipelineResult(kind="no_candidates", state=state, trace=trace)

        # N5 claim plan
        step("제도별 근거 문서를 확인하고 있어요")
        extractor = RuleBasedClaimExtractor()
        state.update(plan_claims(state, extractor, law_resolver=None))
        trace.append(f"N5({len(state.get('claim_plan', []))} claims)")
        if not state.get("claim_plan"):
            return PipelineResult(kind="no_candidates", state=state, trace=trace)

        # N6 공식 문서 확인
        state.update(verify_official_documents(state, store=store))
        trace.append("N6")

        # N7 ↔ N6/N8 루프
        step("근거가 충분한지 검증하고 있어요")
        outcome = _run_evidence_loop(state, store, trace)
        if outcome != "pass":
            decision = state.get("abstention_decision")
            reason = getattr(getattr(decision, "reason", None), "value", None)
            return PipelineResult(
                kind="abstain",
                state=state,
                trace=trace,
                abstention_reason=reason,
            )

        # ── 배선 계층 보정: N9~N12 재검색 필터 ↔ policy_id 규약 불일치 ──
        # N9~N12는 정책 문서를 재검색할 때 metadata_equals={"doc_id": policy_id}
        # 를 쓴다. 그런데 N5가 채우는 claim.policy_id 는 (N7 리뷰 피드백에 따라)
        # bare source_id 라서, 그대로 두면 재검색이 항상 비어 모든 판정이
        # "미확인"으로만 나온다. N7 provenance 검증(source_id == policy_id)은
        # 이미 통과한 지점이므로, 이 구간에서만 claim.policy_id 를 실제 doc_id
        # 로 매핑해 재검색이 정책 문서를 찾도록 한다. 근본 수정은 노드 쪽
        # 필터 키를 source_id 로 바꾸는 것이지만 노드 파일은 이번 범위 밖이다.
        _sid_to_docid = {
            r.chunk.metadata.get("source_id"): r.chunk.doc_id
            for r in state.get("subsidy_chunks", [])
        }
        state["claim_plan"] = [
            {
                **claim,
                "policy_id": _sid_to_docid.get(
                    claim["policy_id"], claim["policy_id"]
                ),
            }
            for claim in state.get("claim_plan", [])
        ]

        # N9 자격 판정 → N10 지원금 → N11 중복수급 → N12 조립
        step("자격·지원금·중복수급을 판정하고 있어요")
        state.update(determine_eligibility(state, store))        # llm_client=None
        trace.append("N9")
        state.update(calculate_benefit_amount(state, store))     # llm_client=None
        trace.append("N10")
        state.update(check_duplicate_benefit(state, store))
        trace.append("N11")
        state.update(assemble_result(state, store))
        trace.append("N12")

        return PipelineResult(kind="answer", state=state, trace=trace)

    except Exception as exc:  # noqa: BLE001
        return PipelineResult(
            kind="error",
            state=state,
            trace=trace,
            error="".join(traceback.format_exception(exc)),
        )
