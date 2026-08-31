"""src/rag_chatbot/graph/builder.py (Issue #25: graph builder 조립) 구조 테스트.

N4~N14 전체를 실제 chromadb + 실제 정책 데이터로 굴리는 end-to-end 실행
테스트는 N7 evidence_gate의 엄격한 입력 계약(canonical date, 안전 신호,
claim 근거 일치 등) 때문에 fixture 준비 비용이 커서 이번 범위에는 포함하지
않았다 - scripts/manual_test_chain.py가 N9~N14를 실제 데이터로 체이닝해서
수동으로 확인하는 용도를 대신한다. 여기서는 그래프가 다이어그램대로
컴파일되는지(모든 노드가 등록됐는지)와, LLM 클라이언트 유무에 관계없이
조립 자체가 항상 성공하는지만 검증한다.

N1~N3(슬롯 파싱/게이트/추가 정보 요청)는 다르다 - N1·N2·N2a는 규칙 기반
placeholder라 외부 의존성(LLM, 실제 문서 검색) 없이도 결정적으로 동작하고,
N3의 interrupt/resume은 MemorySaver 체크포인터 덕분에 실제 그래프
invoke()만으로 끝까지 검증할 수 있다. 그래서 N1~N3 구간은 구조 테스트뿐
아니라 실제 run_graph()/resume_graph() 호출까지 포함한다.
"""

from __future__ import annotations

import pathlib
import tempfile

from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig
from src.rag_chatbot.graph import build_graph, resume_graph, run_graph
from src.rag_chatbot.llm import FakeLLMClient

_EXPECTED_NODES = {
    "slot_parser",  # N1
    "slot_completeness_gate",  # N2
    "general_law_reference_search",  # N2a
    "request_missing_slots",  # N3
    "policy_search",  # N4
    "claim_plan",  # N5
    "document_verification",  # N6
    "evidence_gate",  # N7
    "targeted_law_search",  # N8
    "eligibility_verdict",  # N9
    "benefit_calculator",  # N10
    "duplicate_benefit",  # N11
    "result_assembly",  # N12
    "answer_generation",  # N13
    "final_verification",  # N14
    "abstain_insufficient_evidence",  # N7 fail 경로(E14) 종착 노드
}


def _store(name: str = "graph_builder_test") -> ChromaVectorStore:
    temporary_directory = tempfile.mkdtemp(prefix=f"{name}_")
    return ChromaVectorStore(
        HashEmbeddingProvider(32),
        VectorStoreConfig(
            persist_directory=pathlib.Path(temporary_directory),
            collection_prefix=name,
        ),
    )


def test_build_graph_registers_all_n1_to_n14_nodes() -> None:
    graph = build_graph(_store())

    node_names = set(graph.get_graph().nodes.keys())

    assert _EXPECTED_NODES.issubset(node_names)


def test_build_graph_succeeds_without_llm_client() -> None:
    # llm_client=None(기본값)이어도 조립 자체는 항상 성공해야 한다 - RunPod
    # 엔드포인트가 없어도(docs/RUNPOD_SETUP_DRAFT.md) 그래프가 만들어져야
    # 규칙 기반 실행이 가능하다.
    build_graph(_store())


def test_build_graph_succeeds_with_fake_llm_client() -> None:
    build_graph(_store(), llm_client=FakeLLMClient(response="{}"))


def test_entry_point_is_slot_parser() -> None:
    # E1: Input -> N1. N1~N3 병합 전에는 진입점이 N4(policy_search)였는데,
    # 이번 조립으로 N1로 옮겨졌다 - 회귀하면 사용자가 아무것도 안 물어보고
    # 바로 검색부터 들어가 하드 게이트가 통째로 우회된다.
    graph = build_graph(_store())

    start_edges = [edge for edge in graph.get_graph().edges if edge.source == "__start__"]

    assert len(start_edges) == 1
    assert start_edges[0].target == "slot_parser"


def test_slot_completeness_gate_conditional_edges_cover_all_routes() -> None:
    graph = build_graph(_store())

    edges = graph.get_graph().edges
    sources = {edge.source for edge in edges if edge.source == "slot_completeness_gate"}
    targets = {edge.target for edge in edges if edge.source == "slot_completeness_gate"}

    assert sources == {"slot_completeness_gate"}
    # E3(충분) / E4(지역 부족 -> N2a) / E4(프로필 슬롯 부족 -> N3) 세 갈래.
    assert targets == {
        "policy_search",
        "general_law_reference_search",
        "request_missing_slots",
    }


def test_general_law_reference_search_leads_to_request_missing_slots() -> None:
    # E5: N2a가 채운 general_law_references를 들고 N3로 넘어간다.
    graph = build_graph(_store())

    edges = graph.get_graph().edges
    targets = {
        edge.target for edge in edges if edge.source == "general_law_reference_search"
    }

    assert targets == {"request_missing_slots"}


def test_request_missing_slots_loops_back_to_slot_parser() -> None:
    # E6: 재입력은 N3가 아니라 N1로 라우팅한다(과거 설계와 다른 점,
    # request_missing_slots.py 모듈 docstring 참고).
    graph = build_graph(_store())

    edges = graph.get_graph().edges
    targets = {edge.target for edge in edges if edge.source == "request_missing_slots"}

    assert targets == {"slot_parser"}


def test_evidence_gate_conditional_edges_cover_all_verdicts() -> None:
    graph = build_graph(_store())

    edges = graph.get_graph().edges
    sources_from_evidence_gate = {edge.source for edge in edges if edge.source == "evidence_gate"}
    targets_from_evidence_gate = {edge.target for edge in edges if edge.source == "evidence_gate"}

    assert sources_from_evidence_gate == {"evidence_gate"}
    # E11/E12/E15/E14 네 갈래가 전부 배선돼 있어야 한다.
    assert targets_from_evidence_gate == {
        "document_verification",
        "targeted_law_search",
        "eligibility_verdict",
        "abstain_insufficient_evidence",
    }


def test_run_graph_interrupts_when_hard_gate_slots_are_missing() -> None:
    # 아무 정보도 없는 첫 턴이면 지역부터 하드 게이트 슬롯이 전부 비어
    # 있으므로, N2 -> N3까지 가서 실제로 멈춰야 한다(E2/E4/E5).
    graph = build_graph(_store("interrupt_test"))

    result = run_graph(graph, user_input="", session_id="session-interrupt-a")

    assert "__interrupt__" in result
    assert isinstance(result["__interrupt__"][0].value, str)
    assert result["__interrupt__"][0].value  # 빈 질문이면 안 됨
    assert "region" in result.get("missing_slots", [])


def test_run_graph_sessions_are_isolated() -> None:
    # 서로 다른 session_id는 서로 다른 checkpointer thread_id를 쓰므로,
    # 한 세션의 진행 상태가 다른 세션에 보이면 안 된다.
    graph = build_graph(_store("isolation_test"))

    result_a = run_graph(graph, user_input="", session_id="session-iso-a")
    result_b = run_graph(graph, user_input="", session_id="session-iso-b")

    assert "__interrupt__" in result_a
    assert "__interrupt__" in result_b
    assert result_a["query_id"] == "session-iso-a"
    assert result_b["query_id"] == "session-iso-b"


def test_resume_graph_continues_slot_filling_after_interrupt() -> None:
    # E6: N3에서 멈춘 세션에 사용자의 다음 발화를 넣어 재개하면, N1로 돌아가
    # 새로 파싱한 슬롯이 기존 슬롯과 합쳐져 missing_slots가 줄어들어야 한다.
    graph = build_graph(_store("resume_test"))
    first = run_graph(graph, user_input="", session_id="session-resume-a")
    assert "__interrupt__" in first
    assert "region" in first["missing_slots"]

    resumed = resume_graph(graph, session_id="session-resume-a", user_input="서울특별시")

    assert resumed.get("slots", {}).get("region_scope") == "regional"
    assert "region" not in resumed.get("missing_slots", [])
