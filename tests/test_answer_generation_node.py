"""src/rag_chatbot/graph/nodes/answer_generation.py (N13) 단위 테스트."""

from __future__ import annotations

from rag_design.contracts import (
    Chunk,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes import generate_answer
from src.rag_chatbot.llm import FailingLLMClient, FakeLLMClient


def _chunk(chunk_id: str, doc_id: str, text: str, source_url: str | None) -> Chunk:
    return Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=chunk_id,
        doc_id=doc_id,
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("지원자격",),
        ordinal=0,
        citation_locator="지원자격",
        content_hash=compute_content_hash(text),
        metadata={"source_url": source_url},
    )


def _retrieved(chunk: Chunk) -> RetrievedChunk:
    return RetrievedChunk(
        query_id="q1",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="subsidy",
    )


def _state() -> dict:
    chunk = _chunk("policy-a-chunk-1", "policy-a", "정책 안내", "https://example.gov.kr/a")
    return {
        "assembled_result": {
            "policies": {
                "policy-a": {
                    "eligibility": {
                        "policy_id": "policy-a",
                        "verdict": "충족",
                        "reasons": ["근거 문장"],
                    },
                    "benefit_amount": {"policy_id": "policy-a", "amount": 10000.0},
                    "duplicate": {"policy_id": "policy-a", "status": "미확인"},
                }
            }
        },
        "claim_plan": [
            {
                "claim_id": "c1",
                "policy_id": "policy-a",
                "claim_type": "eligibility",
                "evidence_chunk_ids": ["policy-a-chunk-1"],
            }
        ],
        "subsidy_chunks": [_retrieved(chunk)],
        "law_chunks": [],
        "node_trace": ["N12"],
    }


def test_generate_answer_without_llm_uses_template() -> None:
    result = generate_answer(_state())

    assert "policy-a" in result["draft_answer"]
    # "지원 가능"은 과대 주장이라 "확인한 조건에서는 결격 없음"으로 바꿨다.
    # N9가 대조하는 건 문서 metadata의 연령 기준뿐이라, 나머지 조건은
    # 확인조차 못 한 상태에서 "지원 가능"이라고 말하면 안 된다.
    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert "지원 가능" not in result["draft_answer"]
    assert result["citations"] == [
        {
            "policy_id": "policy-a",
            "chunk_id": "policy-a-chunk-1",
            "source_url": "https://example.gov.kr/a",
            "label": "근거 문서",
        }
    ]
    assert result["node_trace"] == ["N12", "N13"]


def test_generate_answer_uses_llm_output_when_available() -> None:
    llm = FakeLLMClient(response="다듬어진 안내문")

    result = generate_answer(_state(), llm_client=llm)

    assert result["draft_answer"] == "다듬어진 안내문"
    assert len(llm.calls) == 1


def test_generate_answer_falls_back_to_template_when_llm_fails() -> None:
    result = generate_answer(_state(), llm_client=FailingLLMClient())

    assert "policy-a" in result["draft_answer"]
    # "지원 가능"은 과대 주장이라 "확인한 조건에서는 결격 없음"으로 바꿨다.
    # N9가 대조하는 건 문서 metadata의 연령 기준뿐이라, 나머지 조건은
    # 확인조차 못 한 상태에서 "지원 가능"이라고 말하면 안 된다.
    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert "지원 가능" not in result["draft_answer"]


def test_generate_answer_with_no_policies_returns_placeholder() -> None:
    result = generate_answer(
        {"assembled_result": {}, "claim_plan": [], "subsidy_chunks": [], "law_chunks": []}
    )

    assert result["draft_answer"] == "확인된 복지 제도 정보가 없습니다."
    assert result["citations"] == []


def test_generate_answer_drops_citation_without_source_url() -> None:
    state = _state()
    state["subsidy_chunks"] = [
        _retrieved(_chunk("policy-a-chunk-1", "policy-a", "정책 안내", None))
    ]

    result = generate_answer(state)

    assert result["citations"] == []


def test_generate_answer_ignores_citations_for_claims_of_other_policies() -> None:
    state = _state()
    state["claim_plan"].append(
        {
            "claim_id": "c2",
            "policy_id": "policy-other",
            "claim_type": "eligibility",
            "evidence_chunk_ids": ["policy-a-chunk-1"],
        }
    )

    result = generate_answer(state)

    assert [c["policy_id"] for c in result["citations"]] == ["policy-a"]
