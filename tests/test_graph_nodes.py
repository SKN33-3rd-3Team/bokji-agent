"""src/rag_chatbot/graph/nodes 노드 단위 테스트 (N12부터 시작 - 브랜치별 분리).

FakeStore - 빠른 규칙 검증용. 실제 chromadb로 검증하는 통합 테스트는
tests/test_n12_realchroma.py.
"""

from __future__ import annotations

from rag_design.contracts import (
    Chunk,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes import assemble_result


def _eligibility(policy_id: str, verdict: str) -> dict:
    return {"policy_id": policy_id, "verdict": verdict, "reasons": ["근거"]}


def _amount(policy_id: str, amount: float | None) -> dict:
    return {
        "policy_id": policy_id,
        "amount": amount,
        "rule_chunk_id": f"{policy_id}-chunk",
        "calculation_note": "테스트용",
    }


def _duplicate(policy_id: str, status: str) -> dict:
    return {
        "policy_id": policy_id,
        "status": status,
        "conflicts_with": [],
        "condition_note": "테스트용",
    }


def _legal_basis_chunk(policy_id: str, law_text: str) -> RetrievedChunk:
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-legal-basis",
        doc_id=policy_id,
        source_type=SourceType.SUBSIDY,
        text=law_text,
        heading_path=("근거법령",),
        ordinal=0,
        citation_locator="근거법령",
        content_hash=compute_content_hash(law_text),
        metadata={"section_type": "legal_basis"},
    )
    return RetrievedChunk(
        query_id="test-legal-basis",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="subsidy",
    )


def _law_chunk(law_name: str, source_url: str) -> RetrievedChunk:
    text = f"{law_name}\n기본정보"
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"law-{law_name}",
        doc_id=f"law:{law_name}",
        source_type=SourceType.LAW,
        text=text,
        heading_path=("기본정보",),
        ordinal=0,
        citation_locator="기본정보",
        content_hash=compute_content_hash(text),
        metadata={"law_name": law_name, "source_url": source_url, "source_name": "국가법령정보센터"},
    )
    return RetrievedChunk(
        query_id="test-law",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="law",
    )


class FakeStore:
    """rag_design.vector_store.ChromaVectorStore와 같은 search(...) 시그니처를
    갖는 테스트용 대체 구현. chromadb 없이 N12의 법령 검색 규칙만 검증한다.
    """

    def __init__(
        self,
        legal_basis_by_policy: dict[str, RetrievedChunk] | None = None,
        law_by_name: dict[str, RetrievedChunk] | None = None,
    ):
        self._legal_basis_by_policy = legal_basis_by_policy or {}
        self._law_by_name = law_by_name or {}
        self.calls: list[dict] = []

    def search(self, source_type, query, *, query_id, top_k, search_filter, expected_collection_fingerprint=None):
        self.calls.append({"source_type": source_type, "query": query, "query_id": query_id})
        if source_type is SourceType.SUBSIDY:
            doc_id = search_filter.metadata_equals.get("doc_id")
            chunk = self._legal_basis_by_policy.get(doc_id)
        elif source_type is SourceType.LAW:
            law_name = search_filter.metadata_equals.get("law_name")
            chunk = self._law_by_name.get(law_name)
        else:
            chunk = None
        return (chunk,) if chunk is not None else ()


def test_complete_policy_is_assembled_with_all_parts():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [_amount("policy-a", 300000)],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state, FakeStore())

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["eligibility"]["verdict"] == "충족"
    assert entry["benefit_amount"]["amount"] == 300000
    assert entry["duplicate"]["status"] == "미확인"
    assert "status_note" not in entry
    assert result["node_trace"] == ["N12"]


def test_계산_성공한_정책은_법령_검색을_하지_않는다():
    store = FakeStore()
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [_amount("policy-a", 300000)],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert "related_law" not in entry
    assert store.calls == []  # 계산이 됐으니 법령 검색 자체를 안 함


def test_충족_without_amount_result_marks_정보_부족_and_searches_law():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state, FakeStore())

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["benefit_amount"] is None
    assert "정보 부족" in entry["status_note"]
    assert entry["related_law"] == []  # 근거법령을 못 찾았으니 빈 리스트 (숨기지 않음)


def test_amount가_None이면_계산_실패로_보고_법령을_찾는다():
    store = FakeStore(
        legal_basis_by_policy={"policy-a": _legal_basis_chunk("policy-a", "영유아보육법(제34조)")},
        law_by_name={"영유아보육법": _law_chunk("영유아보육법", "https://www.law.go.kr/example")},
    )
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [_amount("policy-a", None)],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["benefit_amount"]["amount"] is None
    assert entry["related_law"] == [
        {
            "law_name": "영유아보육법",
            "source_url": "https://www.law.go.kr/example",
            "source_name": "국가법령정보센터",
        }
    ]


def test_여러_법령이_구분자로_이어져_있으면_각각_찾아서_전부_담는다():
    store = FakeStore(
        legal_basis_by_policy={
            "policy-a": _legal_basis_chunk(
                "policy-a", "유아교육법(제24조)||영유아보육법(제34조)"
            )
        },
        law_by_name={
            "영유아보육법": _law_chunk("영유아보육법", "https://www.law.go.kr/a"),
            # "유아교육법"은 아직 LAW 컬렉션에 없다고 가정 - 없는 건 없는 대로 둔다.
        },
    )
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [_amount("policy-a", None)],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert len(entry["related_law"]) == 1
    assert entry["related_law"][0]["law_name"] == "영유아보육법"


def test_미충족_policy_has_no_benefit_amount_key():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "미충족")],
        "benefit_amounts": [],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state, FakeStore())

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert "benefit_amount" not in entry  # 미충족 정책엔 금액 계산 자체를 안 함
    assert "related_law" not in entry  # 미충족이면 법령 검색도 안 함


def test_missing_duplicate_verdict_marks_정보_부족():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [_amount("policy-a", 100000)],
        "duplicate_verdicts": [],
    }

    result = assemble_result(state, FakeStore())

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["duplicate"] is None
    assert "정보 부족" in entry["status_note"]


def test_multiple_policies_amounts_are_never_summed():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족"), _eligibility("policy-b", "충족")],
        "benefit_amounts": [_amount("policy-a", 100000), _amount("policy-b", 200000)],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인"), _duplicate("policy-b", "미확인")],
    }

    result = assemble_result(state, FakeStore())

    policies = result["assembled_result"]["policies"]
    assert policies["policy-a"]["benefit_amount"]["amount"] == 100000
    assert policies["policy-b"]["benefit_amount"]["amount"] == 200000
    assert "total" not in result["assembled_result"]  # 합산 필드 자체가 없어야 함


def test_node_trace_appends_to_existing_trace():
    state = {
        "eligibility_verdicts": [],
        "benefit_amounts": [],
        "duplicate_verdicts": [],
        "node_trace": ["N1", "N4", "N9"],
    }

    result = assemble_result(state, FakeStore())

    assert result["node_trace"] == ["N1", "N4", "N9", "N12"]
