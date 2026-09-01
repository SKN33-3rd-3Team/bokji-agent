"""src/rag_chatbot/graph/nodes/result_assembly.py (N12) 단위 테스트.

이 파일은 원래 없었다 - N9(eligibility_verdict)/N10(benefit_calculator)/
N11(duplicate_benefit)에는 vectorDB 재검색을 검증하는 테스트가 있었지만,
N12의 _find_related_law()(관련 법령 재검색)는 아무 테스트도 없이 방치돼
있었다. 그 결과 이 함수도 다른 세 노드와 똑같이
``metadata_equals={"doc_id": policy_id, ...}``로 잘못 필터링하는 버그를
갖고 있었는데, 아무도 잡아내지 못했다 (2026-08-31 수정).

FakeStore는 tests/test_graph_nodes.py와 똑같은 원칙을 따른다: chunk의
``doc_id``를 policy_id와 일부러 다르게 만들어서, source_id가 아니라
doc_id로 필터링하는 회귀가 생기면 이 테스트가 반드시 실패하도록 한다.
"""

from __future__ import annotations

from rag_design.contracts import Chunk, RetrievedChunk, SCHEMA_VERSION, SourceType, compute_content_hash
from src.rag_chatbot.graph.nodes.result_assembly import _find_related_law, assemble_result


def _subsidy_chunk(policy_id: str, section_type: str, text: str) -> RetrievedChunk:
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-{section_type}-chunk-1",
        # 실제 프로덕션처럼 doc_id != source_id(=policy_id)인 상황을 재현한다.
        doc_id=f"subsidy:{policy_id}:v1",
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("근거법령",),
        ordinal=0,
        citation_locator="근거법령",
        content_hash=compute_content_hash(text),
        metadata={"source_id": policy_id, "section_type": section_type},
    )
    return RetrievedChunk(
        query_id="test", chunk=chunk, rank=1, score=0.1,
        score_type="cosine_distance", retriever_version="test:fixture", index_name="subsidy",
    )


def _law_chunk(law_name: str, source_url: str) -> RetrievedChunk:
    text = f"{law_name}\n{law_name}(제1조) 본문..."
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{law_name}-chunk-1",
        doc_id=f"law:{law_name}",
        source_type=SourceType.LAW,
        text=text,
        heading_path=(law_name,),
        ordinal=0,
        citation_locator=law_name,
        content_hash=compute_content_hash(text),
        metadata={"law_name": law_name, "source_url": source_url, "source_name": "국가법령정보센터"},
    )
    return RetrievedChunk(
        query_id="test", chunk=chunk, rank=1, score=0.1,
        score_type="cosine_distance", retriever_version="test:fixture", index_name="law",
    )


class FakeStore:
    """source_id(=policy_id)와 law_name으로만 찾을 수 있는 최소 대체 구현.

    실제 chromadb의 exact-match 필터를 흉내낸다: 요청한 metadata_equals가
    저장된 chunk의 metadata와 정확히 일치하지 않으면(예: doc_id로 잘못
    필터링해서 아무 chunk의 metadata에도 그런 doc_id 값이 없으면) 빈 결과를
    돌려준다 - 진짜 chromadb처럼.
    """

    def __init__(self, subsidy_chunks: dict[str, RetrievedChunk], law_chunks: dict[str, RetrievedChunk]):
        self._subsidy_chunks = subsidy_chunks  # key: f"{source_id}:{section_type}"
        self._law_chunks = law_chunks  # key: law_name
        self.calls: list[dict] = []

    def search(self, source_type, query, *, query_id, top_k, search_filter):
        me = search_filter.metadata_equals
        self.calls.append({"source_type": source_type, "metadata_equals": dict(me)})
        if source_type is SourceType.SUBSIDY:
            key = f"{me.get('source_id')}:{me.get('section_type')}"
            hit = self._subsidy_chunks.get(key)
        else:
            hit = self._law_chunks.get(me.get("law_name"))
        return (hit,) if hit else ()


def test_find_related_law_uses_source_id_not_doc_id():
    """N12가 doc_id가 아니라 source_id로 근거법령 chunk를 재검색하는지 확인
    한다 - 이 부분이 doc_id로 잘못 필터링돼 있으면 legal_basis_chunks가 항상
    비어 related_law가 []로만 나온다(2026-08-31 이전 실제 프로덕션 버그).
    """
    store = FakeStore(
        subsidy_chunks={
            "policy-a:legal_basis": _subsidy_chunk(
                "policy-a", "legal_basis", "영유아보육료 지원\n근거법령\n\n유아교육법(제24조)||영유아보육법(제34조)"
            ),
        },
        law_chunks={
            "유아교육법": _law_chunk("유아교육법", "https://law.go.kr/유아교육법"),
            "영유아보육법": _law_chunk("영유아보육법", "https://law.go.kr/영유아보육법"),
        },
    )

    related = _find_related_law("policy-a", store, "n12")

    assert related == [
        {"law_name": "유아교육법", "source_url": "https://law.go.kr/유아교육법", "source_name": "국가법령정보센터"},
        {"law_name": "영유아보육법", "source_url": "https://law.go.kr/영유아보육법", "source_name": "국가법령정보센터"},
    ]
    # 재검색이 source_id로 나갔는지(= doc_id가 아니라) 직접 확인
    subsidy_call = next(c for c in store.calls if c["source_type"] is SourceType.SUBSIDY)
    assert subsidy_call["metadata_equals"]["source_id"] == "policy-a"
    assert "doc_id" not in subsidy_call["metadata_equals"]


def test_find_related_law_returns_empty_when_no_legal_basis_chunk_found():
    store = FakeStore(subsidy_chunks={}, law_chunks={})
    assert _find_related_law("policy-missing", store, "n12") == []


def test_assemble_result_fills_related_law_when_amount_missing():
    store = FakeStore(
        subsidy_chunks={
            "policy-a:legal_basis": _subsidy_chunk("policy-a", "legal_basis", "제목\n근거법령\n\n유아교육법(제24조)"),
        },
        law_chunks={"유아교육법": _law_chunk("유아교육법", "https://law.go.kr/유아교육법")},
    )
    state = {
        "query_id": "n12",
        "eligibility_verdicts": [{"policy_id": "policy-a", "verdict": "충족", "reasons": ["근거 문장"]}],
        "benefit_amounts": [],
        "duplicate_verdicts": [{"policy_id": "policy-a", "status": "가능", "conflicts_with": [], "condition_note": None}],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["benefit_amount"] is None
    assert entry["status_note"] == "정보 부족: 지원금 계산 결과 없음"
    assert entry["related_law"] == [
        {"law_name": "유아교육법", "source_url": "https://law.go.kr/유아교육법", "source_name": "국가법령정보센터"}
    ]
    assert result["node_trace"] == ["N12"]


def test_assemble_result_skips_related_law_lookup_when_not_eligible():
    """자격 미충족/미확인 정책은 애초에 금액 계산을 시도하지 않으므로
    related_law 재검색도 하지 않는다(불필요한 vectorDB 호출 방지 확인)."""
    store = FakeStore(subsidy_chunks={}, law_chunks={})
    state = {
        "query_id": "n12",
        "eligibility_verdicts": [{"policy_id": "policy-b", "verdict": "미확인", "reasons": ["재검색 실패"]}],
        "benefit_amounts": [],
        "duplicate_verdicts": [],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-b"]
    assert "related_law" not in entry
    assert store.calls == []
