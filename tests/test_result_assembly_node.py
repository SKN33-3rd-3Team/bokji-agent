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
    related_law 재검색도 하지 않는다(불필요한 vectorDB 호출 방지 확인).

    아울러 benefit_amount는 None으로, status_note는 "자격이 아직 확인되지
    않았다"는 사실 그대로를 남겨야 한다(2026-09-11 수정 전에는 이 경로에서
    status_note를 전혀 안 남겨서, duplicate_verdicts가 비어있을 때만 우연히
    duplicate 판정 문구("정보 부족: 중복수급 판정 결과 없음")가 대신 채워지는
    바람에 아무도 눈치채지 못했다 - 아래
    test_assemble_result_leaves_no_silent_blank_status_note_when_not_eligible가
    duplicate가 있는 실제 사례를 재현한다)."""
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
    assert entry["benefit_amount"] is None
    assert entry["status_note"] == "자격 여부가 아직 확인되지 않아 지원금을 계산하지 않음"


def test_assemble_result_leaves_no_silent_blank_status_note_when_not_eligible():
    """실사용 중 확인된 버그(2026-09-11): "지역 조건 추가 확인 필요"로 자격
    판정이 충족에서 미확인으로 내려간 정책은 N10이 애초에 금액을 계산하지
    않는데, 그 정책의 duplicate_verdicts 항목이 존재하면(=None이 아니면)
    status_note를 채우는 두 분기(충족 분기, duplicate가 None일 때의 설명
    기본값)가 모두 실행되지 않아 status_note가 아예 안 남았다. 화면에는
    "지원금액 확인 필요"만 뜨고 왜 그런지 설명이 전혀 없었다 - 자격 근거에
    금액처럼 보이는 숫자가 있어도 이유를 알 수 없었던 사례.
    """
    store = FakeStore(subsidy_chunks={}, law_chunks={})
    state = {
        "query_id": "n12",
        "eligibility_verdicts": [
            {
                "policy_id": "policy-region",
                "verdict": "미확인",
                "reasons": ["지역 조건 추가 확인 필요"],
            }
        ],
        "benefit_amounts": [],
        "duplicate_verdicts": [
            {
                "policy_id": "policy-region",
                "status": "미확인",
                "conflicts_with": [],
                "condition_note": None,
            }
        ],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-region"]
    assert entry["benefit_amount"] is None
    assert entry["status_note"] == "자격 여부가 아직 확인되지 않아 지원금을 계산하지 않음"


def test_assemble_result_explains_amount_gap_for_verdict_not_met():
    """자격 미충족(위반 사실 확정)일 때는 미확인과는 다른, 정확한 문구를
    남긴다 - "아직 확인 안 됨"이 아니라 "충족하지 않는 것으로 판정됨"이다."""
    store = FakeStore(subsidy_chunks={}, law_chunks={})
    state = {
        "query_id": "n12",
        "eligibility_verdicts": [
            {"policy_id": "policy-d", "verdict": "미충족", "reasons": ["나이 조건 불충족"]}
        ],
        "benefit_amounts": [],
        "duplicate_verdicts": [
            {"policy_id": "policy-d", "status": "가능", "conflicts_with": [], "condition_note": None}
        ],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-d"]
    assert entry["benefit_amount"] is None
    assert entry["status_note"] == "자격 조건을 충족하지 않는 것으로 판정되어 지원금을 계산하지 않음"


def test_assemble_result_surfaces_calculation_note_when_amount_entry_exists_but_unresolved():
    """benefit_amounts에 항목은 있지만(N10이 시도는 함) amount가 None인 경우
    (2026-09-08 추가: 조건부 규칙에 필요한 슬롯을 아직 몰라서 needs_more_info로
    남은 경우가 대표적)도 status_note가 채워져야 한다 - 그래야 N14가 이 정책을
    "완료"가 아니라 "부분 응답"으로 판정하고, 화면에도 구체적 사유가 뜬다.
    이 항목이 없으면(예전 동작) status_note가 비어서 N14가 잘못 "완료"로
    판정하고, 사용자에게는 사유 없이 "지원금액 계산 불가"만 보였다.
    """
    store = FakeStore(subsidy_chunks={}, law_chunks={})
    state = {
        "query_id": "n12",
        "eligibility_verdicts": [{"policy_id": "policy-c", "verdict": "충족", "reasons": ["근거 문장"]}],
        "benefit_amounts": [
            {
                "policy_id": "policy-c",
                "amount": None,
                "rule_chunk_id": "chunk-1",
                "calculation_note": "정확한 금액 계산에 'marital_status' 확인이 필요함",
                "needs_more_info": True,
                "missing_calc_fields": ["marital_status"],
            }
        ],
        "duplicate_verdicts": [],
    }

    result = assemble_result(state, store)

    entry = result["assembled_result"]["policies"]["policy-c"]
    assert entry["benefit_amount"]["needs_more_info"] is True
    assert entry["status_note"] == "정확한 금액 계산에 'marital_status' 확인이 필요함"
