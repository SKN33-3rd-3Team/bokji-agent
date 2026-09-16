"""``split_document_items`` - S07-06/S10-01(2026-09-16 옵션 ② 확정) 파싱 규칙 검증.

아래 5개 실사용 샘플 문자열은 ``data/samples/subsidy_documents_sample.jsonl``/
``data/evaluation/light_followup_policies.json``(정부24 실데이터 5건, 저장소에
git-tracked)에서 그대로 가져온 것이다 - 가상의 입력이 아니라 실제 데이터로
"지어내지 않는다" 원칙이 지켜지는지 확인한다.
"""

from __future__ import annotations

from backend.app.core.document_parsing import split_document_items


def test_none_stays_none():
    assert split_document_items(None) is None


def test_not_applicable_placeholder_becomes_none():
    assert split_document_items("해당없음") is None
    assert split_document_items("해당 없음") is None
    assert split_document_items("  ") is None


def test_dash_bulleted_multiline_list_splits_per_line():
    # 유아학비(누리과정) 지원
    raw = (
        "- 사회복지서비스 및 급여제공(변경) 신청서\n"
        "- 사회복지서비스 이용권(바우처) 제공(변경) 신청서\n"
        "- 아이사랑 카드발급 신청 및 개인신용정보의 조회·제공·이용 동의서"
    )
    assert split_document_items(raw) == [
        "사회복지서비스 및 급여제공(변경) 신청서",
        "사회복지서비스 이용권(바우처) 제공(변경) 신청서",
        "아이사랑 카드발급 신청 및 개인신용정보의 조회·제공·이용 동의서",
    ]


def test_circle_bulleted_multiline_prose_still_splits_by_line():
    # 근로·자녀장려금 - 내용은 서류명이 아니라 조건문이지만, 원문이 이미
    # 줄 단위로 나눈 것이므로 그대로 존중한다(의미를 판단해 걸러내지 않음).
    raw = (
        "○ 소득재산 등 증거자료 제출 불필요한 경우 : "
        "국세청에서 확인한 소득, 재산 등의 자료와 동일한 경우\n"
        "○ 소득재산 등 증거자료 제출 필요한 경우 : "
        "국세청에서 확인한 소득, 재산 등의 자료와 다른 경우"
    )
    result = split_document_items(raw)
    assert len(result) == 2
    assert result[0].startswith("소득재산 등 증거자료 제출 불필요한 경우")
    assert result[1].startswith("소득재산 등 증거자료 제출 필요한 경우")


def test_single_line_comma_separated_splits_on_commas():
    # 주택금융공사 월세자금보증
    raw = (
        "주민등록등본, 신분증, 확정일자부 월세 계약서 사본, 계약금 지급 영수증, "
        "등기사항전부증명서, 최종학교 졸업증명서, 희망키움통장 유지확인서 등 "
        "대상에 따라 추가서류 필요"
    )
    result = split_document_items(raw)
    assert result[0] == "주민등록등본"
    assert result[1] == "신분증"
    assert result[-1] == "희망키움통장 유지확인서 등 대상에 따라 추가서류 필요"


def test_nested_parenthesis_comma_is_not_split_at_top_level():
    # 친환경 에너지절감장비 보급 - 괄호 안 쉼표는 하위 항목이라 최상위
    # 분리 대상이 아니다.
    raw = "사업신청서, 어업허가 일건서류(어업허가증, 어선검사증 등), 어업경영체 등록 확인서 등"
    assert split_document_items(raw) == [
        "사업신청서",
        "어업허가 일건서류(어업허가증, 어선검사증 등)",
        "어업경영체 등록 확인서 등",
    ]


def test_dash_and_circle_mixed_bullets_multiline():
    # 해양사고 국선 심판변론인 선정 지원
    raw = (
        "○  선정대상임을 증명하는 서류\n"
        "- 「해양사고의 조사 및 심판에 관한 법률 시행규칙」 제17조제1항에 따른 사유"
    )
    result = split_document_items(raw)
    assert len(result) == 2
    assert result[0] == "선정대상임을 증명하는 서류"
    assert result[1] == "「해양사고의 조사 및 심판에 관한 법률 시행규칙」 제17조제1항에 따른 사유"


def test_ambiguous_prose_single_line_is_not_split():
    # 줄바꿈도 쉼표도 없는 산문 한 줄 - 구분자를 못 찾으면 통째로 항목 1개.
    raw = "지원 대상자임을 증명할 수 있는 서류 일체를 제출해야 합니다"
    assert split_document_items(raw) == [raw]


def test_single_bulleted_line_strips_bullet_prefix():
    # 줄이 하나뿐이라 "여러 줄" 분기를 안 타더라도, 글머리표는 여러 줄일 때와
    # 동일하게 제거되어야 한다 - 안 그러면 이 한 줄짜리 항목에만 "-"가 그대로
    # 남는 불일치가 생긴다.
    assert split_document_items("- 주민등록등본") == ["주민등록등본"]
    assert split_document_items("○ 신분증 1부") == ["신분증 1부"]


def test_single_bulleted_line_with_commas_strips_bullet_from_first_segment():
    # 위와 같은 이유로, 쉼표로 더 쪼개지는 경우에도 첫 항목에 글머리표가
    # 남으면 안 된다.
    assert split_document_items("- 주민등록등본, 신분증") == ["주민등록등본", "신분증"]
