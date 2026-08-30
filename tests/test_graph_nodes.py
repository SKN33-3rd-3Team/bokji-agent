"""src/rag_chatbot/graph/nodes 노드 단위 테스트 (N11부터 시작 - 브랜치별 분리)."""

from __future__ import annotations

from rag_design.contracts import (
    Chunk,
    EvidenceStatus,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes import check_duplicate_benefit


def _dup_claim(policy_id: str, status: EvidenceStatus, reasons=("근거 문장",)) -> dict:
    return {
        "claim_id": f"{policy_id}-duplicate",
        "policy_id": policy_id,
        "claim_type": "duplicate",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": ["chunk-1"],
        "status": status,
        "reasons": list(reasons),
    }


def _verdict(policy_id: str, verdict: str) -> dict:
    return {"policy_id": policy_id, "verdict": verdict, "reasons": []}


def _retrieved_chunk(policy_id: str, metadata: dict) -> RetrievedChunk:
    text = f"{policy_id} 중복수급 안내"
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-chunk-dup",
        doc_id=policy_id,
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("중복수급",),
        ordinal=0,
        citation_locator="중복수급",
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
    def __init__(self, chunks_by_policy: dict[str, list[RetrievedChunk]]):
        self._chunks_by_policy = chunks_by_policy
        self.calls: list[dict] = []

    def search(self, source_type, query, *, query_id, top_k, search_filter, expected_collection_fingerprint=None):
        doc_id = search_filter.metadata_equals.get("doc_id")
        self.calls.append({"source_type": source_type, "query": query, "doc_id": doc_id})
        return tuple(self._chunks_by_policy.get(doc_id, ()))


def test_explicit_mutual_exclusion_with_eligible_policy_yields_불가():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족"), _verdict("policy-b", "충족")],
        "claim_plan": [_dup_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore(
        {"policy-a": [_retrieved_chunk("policy-a", {"mutually_exclusive_with": ["policy-b"]})]}
    )

    result = check_duplicate_benefit(state, store)

    verdict = result["duplicate_verdicts"][0]
    assert verdict["status"] == "불가"
    assert verdict["conflicts_with"] == ["policy-b"]


def test_no_exclusion_metadata_defaults_to_미확인_not_가능():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족")],
        "claim_plan": [_dup_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({"policy-a": [_retrieved_chunk("policy-a", {})]})

    result = check_duplicate_benefit(state, store)

    verdict = result["duplicate_verdicts"][0]
    assert verdict["status"] == "미확인"
    assert "mutually_exclusive_with" in verdict["condition_note"]


def test_exclusion_lists_non_eligible_policy_still_미확인():
    # 상대 정책이 eligible(충족) 집합에 없으면 실제 충돌로 확정하지 않는다.
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족"), _verdict("policy-b", "미충족")],
        "claim_plan": [_dup_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore(
        {"policy-a": [_retrieved_chunk("policy-a", {"mutually_exclusive_with": ["policy-b"]})]}
    )

    result = check_duplicate_benefit(state, store)

    assert result["duplicate_verdicts"][0]["status"] == "미확인"


def test_uncertain_claim_status_skips_recheck_search():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족")],
        "claim_plan": [_dup_claim("policy-a", EvidenceStatus.CONFLICT, reasons=("상충 발견",))],
    }
    store = FakeStore({})

    result = check_duplicate_benefit(state, store)

    assert result["duplicate_verdicts"][0]["status"] == "미확인"
    assert store.calls == []


def test_recheck_search_finds_nothing():
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "충족")],
        "claim_plan": [_dup_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({})

    result = check_duplicate_benefit(state, store)

    verdict = result["duplicate_verdicts"][0]
    assert verdict["status"] == "미확인"
    assert "재검색" in verdict["condition_note"]


def test_processes_all_verdicts_not_only_충족():
    # E17: N11은 미확인/미충족 정책도 판정 대상에 포함한다 (N10과 다른 지점).
    state = {
        "eligibility_verdicts": [_verdict("policy-a", "미확인")],
        "claim_plan": [_dup_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({"policy-a": [_retrieved_chunk("policy-a", {})]})

    result = check_duplicate_benefit(state, store)

    assert len(result["duplicate_verdicts"]) == 1
    assert store.calls[0]["doc_id"] == "policy-a"
