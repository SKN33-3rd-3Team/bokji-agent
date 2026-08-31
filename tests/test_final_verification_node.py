"""src/rag_chatbot/graph/nodes/final_verification.py (N14) 단위 테스트."""

from __future__ import annotations

import pytest

from rag_design.contracts import (
    Chunk,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes import route_final_verification, verify_final_answer


def _chunk(chunk_id: str) -> Chunk:
    text = "본문"
    return Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=chunk_id,
        doc_id="policy-a",
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("h",),
        ordinal=0,
        citation_locator="h",
        content_hash=compute_content_hash(text),
        metadata={},
    )


def _retrieved(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        query_id="q1",
        chunk=_chunk(chunk_id),
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="subsidy",
    )


def test_verify_final_answer_complete_when_all_citations_known() -> None:
    state = {
        "draft_answer": "답변",
        "citations": [
            {"policy_id": "policy-a", "chunk_id": "chunk-1", "source_url": "u", "label": "근거 문서"}
        ],
        "subsidy_chunks": [_retrieved("chunk-1")],
        "law_chunks": [],
        "assembled_result": {"policies": {"policy-a": {}}},
        "node_trace": ["N13"],
    }

    result = verify_final_answer(state)

    assert result["answer_status"] == "complete"
    assert result["final_answer"] == "답변"
    assert result["final_citations"] == state["citations"]
    assert result["node_trace"] == ["N13", "N14"]


def test_verify_final_answer_drops_unknown_citation_and_marks_partial() -> None:
    state = {
        "draft_answer": "답변",
        "citations": [
            {"policy_id": "policy-a", "chunk_id": "chunk-known", "source_url": "u", "label": "근거 문서"},
            {"policy_id": "policy-a", "chunk_id": "chunk-invented", "source_url": "u2", "label": "근거 문서"},
        ],
        "subsidy_chunks": [_retrieved("chunk-known")],
        "law_chunks": [],
        "assembled_result": {"policies": {"policy-a": {}}},
    }

    result = verify_final_answer(state)

    assert result["answer_status"] == "partial"
    assert [c["chunk_id"] for c in result["final_citations"]] == ["chunk-known"]


def test_verify_final_answer_marks_partial_when_policy_has_status_note() -> None:
    state = {
        "draft_answer": "답변",
        "citations": [
            {"policy_id": "policy-a", "chunk_id": "chunk-1", "source_url": "u", "label": "근거 문서"}
        ],
        "subsidy_chunks": [_retrieved("chunk-1")],
        "law_chunks": [],
        "assembled_result": {
            "policies": {"policy-a": {"status_note": "정보 부족: 지원금 계산 결과 없음"}}
        },
    }

    result = verify_final_answer(state)

    assert result["answer_status"] == "partial"


def test_verify_final_answer_abstains_when_no_verified_citations() -> None:
    state = {
        "draft_answer": "답변",
        "citations": [],
        "subsidy_chunks": [],
        "law_chunks": [],
        "assembled_result": {"policies": {"policy-a": {}}},
    }

    result = verify_final_answer(state)

    assert result["answer_status"] == "abstained"
    assert result["final_citations"] == []
    assert result["final_answer"] != "답변"


def test_verify_final_answer_abstains_when_no_policies() -> None:
    state = {
        "draft_answer": "답변",
        "citations": [
            {"policy_id": "policy-a", "chunk_id": "chunk-1", "source_url": "u", "label": "근거 문서"}
        ],
        "subsidy_chunks": [_retrieved("chunk-1")],
        "law_chunks": [],
        "assembled_result": {"policies": {}},
    }

    result = verify_final_answer(state)

    assert result["answer_status"] == "abstained"


def test_route_final_verification_maps_all_statuses() -> None:
    assert route_final_verification({"answer_status": "complete"}) == "terminal_success"
    assert route_final_verification({"answer_status": "partial"}) == "terminal_success"
    assert route_final_verification({"answer_status": "abstained"}) == "terminal_insufficient"


def test_route_final_verification_raises_on_missing_status() -> None:
    with pytest.raises(ValueError):
        route_final_verification({})
