"""N11(duplicate_benefit) 중복수급 조항 분류·상대 매칭 단위 테스트.

원천 문서의 "중복" 표현은 한 가지가 아니다. 247개 조항을 전수 분류하면
other 164건(66%) / household 65건(26%) / header 18건(7%)이다. 셋을 한
덩어리로 "중복수급 제한 조항이 있습니다"로 보여주면, 신청 횟수 제한일 뿐인
household를 사용자가 "다른 제도와 못 받는다"로 읽는다. 이 파일은 그 구분이
실제 원문에서 유지되는지 고정한다 - 아래 문장들은 전부
data/processed/subsidy_documents.jsonl 에서 그대로 가져온 실제 조항이다.
"""

from __future__ import annotations

import pytest

from src.rag_chatbot.graph.nodes.duplicate_benefit import (
    CLAUSE_KIND_HEADER,
    CLAUSE_KIND_HOUSEHOLD,
    CLAUSE_KIND_OTHER,
    classify_clause,
    find_named_conflicts,
)


@pytest.mark.parametrize(
    "clause",
    [
        "○ 에너지바우처와 중복지원 불가",
        "ㆍ참전명예수당과 중복지급 안됨",
        "※ 주소전입 학생 지원사업과 중복 지원 불가",
        "※ 중앙부처 및 타 지방자치단체 등 타 공공기관에서 교통비를 지원 받은 경우, 중복지원 불가",
        "※ 단, 소득연계형 국가장학금 중복수혜 불가",
        "※ 보건복지부 여성장애인 출산비용지원 사업과 중복불가하며 차액은 지원가능",
        # 가구 표현("1가구당")과 상대 제도명이 같이 나오는 문장. 뜻은
        # "이 사업들을 이미 지원받은 가구는 못 받는다"이므로 other가 맞다.
        # 처음에 household로 라벨링했다가 테스트가 잡아냈다.
        "농촌주택 개량사업, 귀농귀촌팀 소관 주거·생활 부분 지원사업 기지원가구(1가구당 중복지원 불가)",
    ],
)
def test_other_program_clauses(clause: str) -> None:
    assert classify_clause(clause) == CLAUSE_KIND_OTHER


@pytest.mark.parametrize(
    "clause",
    [
        "- 세대분리의 경우에도 동일 주소지 내 중복지원 불가",
        "○ 과제신청 대상: 한농대 졸업생 (단, 기수혜자 중복 수혜 불가)",
        "※ 아동의 부모가 모두 장애인인 경우 중복 지원 불가",
    ],
)
def test_household_or_reapplication_clauses(clause: str) -> None:
    """"1가구 1회", "기수혜자 제외"는 다른 제도와의 중복이 아니다."""

    assert classify_clause(clause) == CLAUSE_KIND_HOUSEHOLD


@pytest.mark.parametrize("clause", ["※ 중복수혜불가 조건", "○ 중복수혜불가 조건", "※ 중복수혜 불가"])
def test_header_only_clauses(clause: str) -> None:
    """조건 내용 없이 제목만 있는 조항. 사용자에게 알려줄 정보가 없다."""

    assert classify_clause(clause) == CLAUSE_KIND_HEADER


def test_named_conflict_is_found_within_the_same_answer() -> None:
    """실제 원문에서 확인된 쌍 - 유아학비/영유아보육료는 함께 받을 수 없다."""

    titles = {
        "p-a": "영유아보육료 지원",
        "p-b": "가정양육수당 지원",
        "p-c": "청년 월세 지원",
    }
    clause = "○ 중복불가서비스 : 유아학비(누리과정) 지원, 영유아보육료 지원"

    found = find_named_conflicts([clause], titles, self_policy_id="p-b")

    assert set(found) == {"p-a"}
    assert found["p-a"] == clause


def test_self_is_never_reported_as_its_own_conflict() -> None:
    titles = {"p-a": "영유아보육료 지원"}
    clause = "영유아보육료 지원과 중복 불가"

    assert find_named_conflicts([clause], titles, self_policy_id="p-a") == {}


def test_short_titles_do_not_match_by_accident() -> None:
    """"지원", "수당" 같은 두세 글자 제목이 우연히 걸리면 오답이 쏟아진다."""

    titles = {"p-a": "수당", "p-b": "지원"}
    clause = "타 사업과 중복 지원 불가"

    assert find_named_conflicts([clause], titles, self_policy_id="p-z") == {}


def test_only_policies_present_in_this_answer_are_matched() -> None:
    """전체 10,968건과 대조하지 않는다.

    전체 제목 사전과 맞추면 지자체마다 같은 이름을 쓰는 정책("보훈명예수당")이
    엉뚱하게 걸린다(실측: 전체 대조 시 관계쌍 177건 중 133건이 다른 지역).
    이번 답변에 함께 나온 정책끼리만 보면 지역이 이미 맞춰져 있다.
    """

    titles = {"p-a": "가평보훈명예수당"}
    clause = "- 동작구 보훈예우수당과 중복지급 불가"

    assert find_named_conflicts([clause], titles, self_policy_id="p-z") == {}


# ── 노드 단위: subsidy_full_chunks 를 쓰는 경로 ──────────────────────

from rag_design.contracts import (  # noqa: E402
    SCHEMA_VERSION,
    Chunk,
    RetrievedChunk,
    SourceType,
    compute_content_hash,
)

from src.rag_chatbot.graph.nodes.duplicate_benefit import check_duplicate_benefit  # noqa: E402


def _chunk(policy_id: str, title: str, section_type: str, body: str) -> RetrievedChunk:
    # chunking.py 가 붙이는 prefix 형태를 그대로 흉내낸다 - 제목은 첫 줄이다.
    text = f"{title}\n지역: 전국\n{section_type}\n{body}"
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-{section_type}",
        doc_id=f"subsidy:{policy_id}:v1",
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=(section_type,),
        ordinal=0,
        citation_locator=section_type,
        content_hash=compute_content_hash(text),
        metadata={"source_id": policy_id, "section_type": section_type},
    )
    return RetrievedChunk(
        query_id="q1", chunk=chunk, rank=1, score=0.0,
        score_type="not_ranked", retriever_version="test", index_name="subsidy",
    )


class _ExplodingStore:
    """재검색을 시도하면 실패한다 - full_chunks만으로 판정하는지 확인용."""

    def search(self, *args, **kwargs):  # pragma: no cover - 호출되면 테스트 실패
        raise AssertionError("subsidy_full_chunks 가 있으면 재검색하면 안 된다")


def _state(full_chunks, policy_ids, verdict="충족"):
    return {
        "query_id": "q1",
        "subsidy_full_chunks": full_chunks,
        "eligibility_verdicts": [
            {"policy_id": pid, "verdict": verdict} for pid in policy_ids
        ],
        "claim_plan": [
            {"policy_id": pid, "claim_type": "duplicate", "status": "supported"}
            for pid in policy_ids
        ],
    }


def test_conflict_names_the_other_policy_in_the_same_answer() -> None:
    full = [
        _chunk("p-a", "가정양육수당 지원", "support_details",
               "○ 중복불가서비스 : 영유아보육료 지원"),
        _chunk("p-b", "영유아보육료 지원", "support_details", "보육료를 지원합니다."),
    ]
    result = check_duplicate_benefit(_state(full, ["p-a", "p-b"]), _ExplodingStore())

    by_policy = {v["policy_id"]: v for v in result["duplicate_verdicts"]}
    assert by_policy["p-a"]["status"] == "불가"
    assert by_policy["p-a"]["conflicts_with"] == ["p-b"]
    assert "영유아보육료 지원" in by_policy["p-a"]["condition_note"]
    # 상대 쪽 문서에는 조항이 없으므로 단정하지 않는다.
    assert by_policy["p-b"]["status"] == "미확인"


def test_other_clause_without_a_named_counterpart_is_conditional() -> None:
    full = [_chunk("p-a", "청년 월세 지원", "support_target", "※ 타 기관 사업과 중복 지원 불가")]
    result = check_duplicate_benefit(_state(full, ["p-a"]), _ExplodingStore())

    verdict = result["duplicate_verdicts"][0]
    assert verdict["status"] == "조건부"
    assert verdict["clause_kind"] == "other"
    assert verdict["conflicts_with"] == []


def test_household_clause_is_not_reported_as_duplicate_restriction() -> None:
    """"1가구 1회"는 중복수급 제한이 아니다 - 상태를 '조건부'로 올리지 않는다."""

    full = [_chunk("p-a", "출산 지원금", "support_target", "- 1가구당 중복지원 불가")]
    result = check_duplicate_benefit(_state(full, ["p-a"]), _ExplodingStore())

    verdict = result["duplicate_verdicts"][0]
    assert verdict["status"] == "미확인"
    assert verdict["clause_kind"] == "household"
    # _find_restriction_clauses 가 문장을 다듬으면서 글머리표를 떼어낸다.
    assert verdict["household_clauses"] == ["1가구당 중복지원 불가"]


def test_header_only_clause_points_to_the_official_document() -> None:
    full = [_chunk("p-a", "평생교육이용권", "eligibility_criteria", "※ 중복수혜불가 조건")]
    result = check_duplicate_benefit(_state(full, ["p-a"]), _ExplodingStore())

    verdict = result["duplicate_verdicts"][0]
    assert verdict["status"] == "미확인"
    assert verdict["clause_kind"] == "header"
    assert "공식 문서" in verdict["condition_note"]


def test_no_clause_never_becomes_possible() -> None:
    """조항이 없다고 "가능"으로 단정하지 않는다 - 안 적힌 것과 허용은 다르다."""

    full = [_chunk("p-a", "청년 월세 지원", "support_details", "월 20만원을 지원합니다.")]
    result = check_duplicate_benefit(_state(full, ["p-a"]), _ExplodingStore())

    verdict = result["duplicate_verdicts"][0]
    assert verdict["status"] == "미확인"
    assert verdict["clause_kind"] is None
