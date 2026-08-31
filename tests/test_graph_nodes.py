"""src/rag_chatbot/graph/nodes 노드 단위 테스트 (FakeStore - 빠른 규칙 검증용).

실제 chromadb로 검증하는 통합 테스트는 tests/test_graph_nodes_realchroma.py.
"""

from __future__ import annotations

from rag_design.contracts import (
    Chunk,
    EvidenceStatus,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes import determine_eligibility


def _claim(policy_id: str, status: EvidenceStatus, reasons=("근거 문장",)) -> dict:
    return {
        "claim_id": f"{policy_id}-eligibility",
        "policy_id": policy_id,
        "claim_type": "eligibility",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": ["chunk-1"],
        "status": status,
        "reasons": list(reasons),
    }


def _retrieved_chunk(policy_id: str, metadata: dict) -> RetrievedChunk:
    text = f"{policy_id} 지원자격 안내"
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-chunk-1",
        doc_id=policy_id,
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("지원자격",),
        ordinal=0,
        citation_locator="지원자격",
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
    """rag_design.vector_store.ChromaVectorStore와 같은 search(...) 시그니처를
    갖는 테스트용 대체 구현. chromadb 없이 N9의 판정 규칙만 검증한다.
    """

    def __init__(self, chunks_by_policy: dict[str, list[RetrievedChunk]]):
        self._chunks_by_policy = chunks_by_policy
        self.calls: list[dict] = []

    def search(self, source_type, query, *, query_id, top_k, search_filter, expected_collection_fingerprint=None):
        self.calls.append(
            {
                "source_type": source_type,
                "query": query,
                "query_id": query_id,
                "top_k": top_k,
                "doc_id": search_filter.metadata_equals.get("doc_id"),
            }
        )
        doc_id = search_filter.metadata_equals.get("doc_id")
        return tuple(self._chunks_by_policy.get(doc_id, ()))


def test_supported_claim_and_matching_age_yields_충족():
    state = {
        "slots": {"age": 70},
        "claim_plan": [_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore(
        {"policy-a": [_retrieved_chunk("policy-a", {"age_start": 65, "age_end": None})]}
    )

    result = determine_eligibility(state, store)

    assert result == {
        "eligibility_verdicts": [
            {"policy_id": "policy-a", "verdict": "충족", "reasons": ["근거 문장"]}
        ]
    }
    assert store.calls[0]["doc_id"] == "policy-a"
    assert store.calls[0]["source_type"] is SourceType.SUBSIDY


def test_supported_claim_but_age_below_range_yields_미충족():
    state = {
        "slots": {"age": 40},
        "claim_plan": [_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore(
        {"policy-a": [_retrieved_chunk("policy-a", {"age_start": 65, "age_end": None})]}
    )

    result = determine_eligibility(state, store)

    verdict = result["eligibility_verdicts"][0]
    assert verdict["verdict"] == "미충족"
    assert "연령 조건 미충족" in verdict["reasons"][0]


def test_supported_claim_but_age_above_range_yields_미충족():
    state = {
        "slots": {"age": 10},
        "claim_plan": [_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore(
        {"policy-a": [_retrieved_chunk("policy-a", {"age_start": None, "age_end": 6})]}
    )

    result = determine_eligibility(state, store)

    assert result["eligibility_verdicts"][0]["verdict"] == "미충족"


def test_unsupported_claim_yields_미확인_without_recheck_search():
    state = {
        "slots": {"age": 70},
        "claim_plan": [_claim("policy-a", EvidenceStatus.UNSUPPORTED, reasons=("원문에 없음",))],
    }
    store = FakeStore({})

    result = determine_eligibility(state, store)

    verdict = result["eligibility_verdicts"][0]
    assert verdict["verdict"] == "미확인"
    assert verdict["reasons"] == ["원문에 없음"]
    assert store.calls == []  # 근거가 불확실하면 재검색까지 가지 않는다


def test_conflict_claim_yields_미확인():
    state = {
        "slots": {},
        "claim_plan": [_claim("policy-a", EvidenceStatus.CONFLICT, reasons=("상충되는 문서 발견",))],
    }
    store = FakeStore({})

    result = determine_eligibility(state, store)

    assert result["eligibility_verdicts"][0]["verdict"] == "미확인"


def test_recheck_search_finds_nothing_yields_미확인():
    state = {
        "slots": {"age": 70},
        "claim_plan": [_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore({})  # 재검색 결과 없음

    result = determine_eligibility(state, store)

    verdict = result["eligibility_verdicts"][0]
    assert verdict["verdict"] == "미확인"
    assert "재검색" in verdict["reasons"][0]


def test_no_age_slot_or_no_age_metadata_defaults_to_충족():
    state = {
        "slots": {},  # age 없음
        "claim_plan": [_claim("policy-a", EvidenceStatus.SUPPORTED)],
    }
    store = FakeStore(
        {"policy-a": [_retrieved_chunk("policy-a", {"age_start": 65, "age_end": None})]}
    )

    result = determine_eligibility(state, store)

    assert result["eligibility_verdicts"][0]["verdict"] == "충족"


def test_no_eligibility_claims_for_policy_yields_empty_list():
    state = {
        "slots": {},
        "claim_plan": [
            {
                "claim_id": "policy-a-amount",
                "policy_id": "policy-a",
                "claim_type": "amount",
                "doc_check_required": True,
                "law_check_required": False,
                "evidence_chunk_ids": [],
                "status": EvidenceStatus.SUPPORTED,
                "reasons": ["금액 근거"],
            }
        ],
    }
    store = FakeStore({})

    result = determine_eligibility(state, store)

    assert result == {"eligibility_verdicts": []}


def test_multiple_policies_are_judged_independently():
    state = {
        "slots": {"age": 70},
        "claim_plan": [
            _claim("policy-a", EvidenceStatus.SUPPORTED),
            _claim("policy-b", EvidenceStatus.PARTIAL, reasons=("일부만 확인됨",)),
        ],
    }
    store = FakeStore(
        {"policy-a": [_retrieved_chunk("policy-a", {"age_start": 65, "age_end": None})]}
    )

    result = determine_eligibility(state, store)

    by_policy = {v["policy_id"]: v["verdict"] for v in result["eligibility_verdicts"]}
    assert by_policy == {"policy-a": "충족", "policy-b": "미확인"}
    assert [c["doc_id"] for c in store.calls] == ["policy-a"]  # policy-b는 재검색 안 함
