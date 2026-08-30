"""src/rag_chatbot/graph/nodes 노드 단위 테스트 (N10부터 시작 - 브랜치별 분리)."""

from __future__ import annotations

from rag_design.contracts import (
    Chunk,
    EvidenceStatus,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes import calculate_benefit_amount


def _amount_claim(policy_id: str, status: EvidenceStatus, reasons=("근거 문장",)) -> dict:
    return {
        "claim_id": f"{policy_id}-amount",
        "policy_id": policy_id,
        "claim_type": "amount",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": ["chunk-1"],
        "status": status,
        "reasons": list(reasons),
    }


def _verdict(policy_id: str, verdict: str) -> dict:
    return {"policy_id": policy_id, "verdict": verdict, "reasons": []}


def _retrieved_chunk(policy_id: str, metadata: dict) -> RetrievedChunk:
    text = f"{policy_id} 지원금액 안내"
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-chunk-amount",
        doc_id=policy_id,
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("지원금액",),
        ordinal=0,
        citation_locator="지원금액",
        content_hash=compute_content_hash(text),
        metadata=metadata,
    )
    return RetrievedChunk(
        query_id=f"{policy_id}-recheck",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="subsidy",
    )


class FakeStore:
    """ChromaVectorStore.search(...)와 같은 시그니처를 갖는 테스트용 대체 구현."""

    def __init__(self, chunks_by_policy: dict[str, list[RetrievedChunk]]):
        self._chunks_by_policy = chunks_by_policy
        self.calls: list[dict] = []

    def search(self, source_type, query, *, query_id, top_k, search_filter, expected_collection_fingerprint=None):
        doc_id = search_filter.metadata_equals.get("doc_id")
        self.calls.append({"source_type": source_type, "query": query, "doc_id": doc_id})
        return tuple(self._chunks_by_policy.get(doc_id, ()))


def test_structured_amount_metadata_is_used_directly():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족")],
        "claim_plan": [_amount_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({"policy-a": [_retrieved_chunk("policy-a", {"amount": 300000})]})

    result = calculate_benefit_amount(state, store)

    assert result == {
        "benefit_amounts": [
            {
                "policy_id": "policy-a",
                "amount": 300000.0,
                "rule_chunk_id": "policy-a-chunk-amount",
                "calculation_note": "재검색한 chunk metadata의 구조화 금액 필드를 그대로 사용",
            }
        ]
    }
    assert store.calls[0]["doc_id"] == "policy-a"
    assert store.calls[0]["source_type"] is SourceType.SUBSIDY


def test_no_structured_amount_field_yields_none_amount_not_guessed():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족")],
        "claim_plan": [_amount_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({"policy-a": [_retrieved_chunk("policy-a", {"age_start": 65})]})

    result = calculate_benefit_amount(state, store)

    entry = result["benefit_amounts"][0]
    assert entry["amount"] is None
    assert "구조화된 금액 필드가 없어" in entry["calculation_note"]


def test_non_eligible_policy_is_skipped_entirely():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "미확인")],
        "claim_plan": [_amount_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({"policy-a": [_retrieved_chunk("policy-a", {"amount": 100000})]})

    result = calculate_benefit_amount(state, store)

    assert result == {"benefit_amounts": []}
    assert store.calls == []


def test_uncertain_claim_status_skips_recheck_search():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족")],
        "claim_plan": [_amount_claim("policy-a", EvidenceStatus.UNSUPPORTED, reasons=("원문에 없음",))],
    }
    store = FakeStore({})

    result = calculate_benefit_amount(state, store)

    entry = result["benefit_amounts"][0]
    assert entry["amount"] is None
    assert store.calls == []


def test_recheck_search_finds_nothing():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족")],
        "claim_plan": [_amount_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({})

    result = calculate_benefit_amount(state, store)

    entry = result["benefit_amounts"][0]
    assert entry["amount"] is None
    assert "재검색" in entry["calculation_note"]


def test_multiple_eligible_policies_are_not_summed_together():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족"), _verdict("policy-b", "충족")],
        "claim_plan": [
            _amount_claim("policy-a", EvidenceStatus.SUPPORTED),
            _amount_claim("policy-b", EvidenceStatus.SUPPORTED),
        ],
    }
    store = FakeStore(
        {
            "policy-a": [_retrieved_chunk("policy-a", {"amount": 100000})],
            "policy-b": [_retrieved_chunk("policy-b", {"benefit_amount": 200000})],
        }
    )

    result = calculate_benefit_amount(state, store)

    by_policy = {entry["policy_id"]: entry["amount"] for entry in result["benefit_amounts"]}
    assert by_policy == {"policy-a": 100000.0, "policy-b": 200000.0}
