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


def test_generate_answer_uses_validated_llm_summary_when_available() -> None:
    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-a", "summary": "다듬어진 안내문"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert result["draft_answer"].startswith("다듬어진 안내문")
    # 사실 라인(지원자격/지원금액/중복수급)은 검증에 통과한 정책이어도
    # 여전히 템플릿(규칙 기반)에서 나온다 - LLM이 다시 쓰지 않는다.
    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert len(llm.calls) == 1


def test_generate_answer_rejects_summary_with_fabricated_number() -> None:
    """rag_eval.ipynb 실험에서 실측된 위험(원문 없는 숫자로 자릿수 부풀림)을
    막는지 확인한다. 원문 지원금액은 10000.0인데, LLM이 100000이라고 쓰면
    그 정책의 summary는 버려지고 템플릿 문장만 남아야 한다."""

    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-a", '
        '"summary": "지원금 100000원을 받으실 수 있어요"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert "100000" not in result["draft_answer"]
    assert result["draft_answer"] == "\n\n".join(
        [
            "[policy-a]\n- 지원자격: 확인한 조건에서는 결격 없음\n"
            "  근거: 근거 문장\n- 지원금액: 10000.0\n- 중복수급: 미확인"
        ]
    )


def test_generate_answer_ignores_summary_for_unknown_policy_id() -> None:
    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-does-not-exist", '
        '"summary": "지어낸 정책 안내문"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert "지어낸 정책" not in result["draft_answer"]


def test_generate_answer_falls_back_to_template_when_llm_returns_non_json() -> None:
    llm = FakeLLMClient(response="그냥 자유 텍스트, JSON 아님")

    result = generate_answer(_state(), llm_client=llm)

    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert "그냥 자유 텍스트" not in result["draft_answer"]


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
