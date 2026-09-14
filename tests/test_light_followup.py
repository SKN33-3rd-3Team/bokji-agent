from __future__ import annotations

import json

from src.rag_chatbot.llm import FailingLLMClient, FakeLLMClient
from src.rag_chatbot.light_followup import (
    _profile_facts,
    answer_light_followup,
    build_policy_context,
    respond_to_policy_question,
    verify_light_answer,
)

_POLICY = {
    "title": "청년월세 특별지원",
    "eligibility_status": "충족",
    "detail": {
        "purpose": "청년 주거비 부담 완화",
        "support_target": "만 19~34세 무주택 청년",
        "eligibility_criteria": "",
        "support_details": "월 최대 20만원, 최대 12개월",
        "application_method": "정부24 또는 관할 주민센터 방문 신청",
        "application_period": "매월 말일까지",
        "legal_basis": None,
    },
}

_CONTEXT = """
청년월세 특별지원 · 서울특별시
지원대상: 만 19~34세 무주택 청년
신청방법: 정부24 또는 관할 주민센터 방문 신청. 별도 제출서류 안내는 없습니다.
신청기한: 매월 말일까지
""".strip()


def test_verify_passes_when_all_quotes_are_in_context() -> None:
    assert verify_light_answer(["정부24 또는 관할 주민센터 방문 신청"], _CONTEXT) is True
    assert verify_light_answer(
        ["만 19~34세 무주택 청년", "매월 말일까지"], _CONTEXT
    ) is True


def test_verify_normalizes_whitespace_and_newlines() -> None:
    # 원문에 줄바꿈이 섞여 있어도 공백 정규화 후 매칭돼야 한다.
    quote = "신청방법: 정부24 또는 관할 주민센터 방문 신청"
    assert verify_light_answer([quote], _CONTEXT) is True


def test_verify_fails_when_any_quote_is_not_in_context() -> None:
    # 문맥에 없는 서류를 지어낸 경우.
    assert verify_light_answer(["주민등록등본과 소득증명서 제출"], _CONTEXT) is False
    assert verify_light_answer(
        ["매월 말일까지", "국세청 소득 확인이 필요합니다"], _CONTEXT
    ) is False


def test_verify_fails_when_no_quote_given() -> None:
    assert verify_light_answer([], _CONTEXT) is False
    assert verify_light_answer(None, _CONTEXT) is False


def test_verify_fails_when_context_is_empty() -> None:
    assert verify_light_answer(["만 19~34세 무주택 청년"], "") is False
    assert verify_light_answer(["만 19~34세 무주택 청년"], None) is False


def test_verify_ignores_non_string_or_blank_quotes() -> None:
    assert verify_light_answer([123, "", "  ", "매월 말일까지"], _CONTEXT) is True
    # 유효 발췌가 하나도 없으면(전부 걸러지면) 실패.
    assert verify_light_answer([123, None, "   "], _CONTEXT) is False


# --- build_policy_context ---------------------------------------------------


def test_build_policy_context_uses_detail_sections_and_skips_blanks() -> None:
    context = build_policy_context(_POLICY)

    assert "[청년월세 특별지원]" in context
    assert "지원대상: 만 19~34세 무주택 청년" in context
    assert "신청방법: 정부24 또는 관할 주민센터 방문 신청" in context
    # 빈 문자열/None 섹션은 빠진다.
    assert "선정기준:" not in context
    assert "근거법령:" not in context


def test_build_policy_context_appends_extra_facts() -> None:
    context = build_policy_context(
        _POLICY, extra_facts=["자격 판정 근거: 만 나이 29세로 대상 범위 내"]
    )

    assert "자격 판정 근거: 만 나이 29세로 대상 범위 내" in context


# --- answer_light_followup ------------------------------------------------


def _fake(payload: dict, *, fenced: bool = False) -> FakeLLMClient:
    body = json.dumps(payload, ensure_ascii=False)
    return FakeLLMClient(f"```json\n{body}\n```" if fenced else body)


def test_answer_light_followup_returns_coerced_answer() -> None:
    client = _fake(
        {
            "answerable": True,
            "answer": "정부24 또는 주민센터에서 신청합니다.",
            "evidence_quotes": ["정부24 또는 관할 주민센터 방문 신청"],
        }
    )

    out = answer_light_followup("신청방법: 정부24 또는 관할 주민센터 방문 신청", "어떻게 신청해요?", llm_client=client)

    assert out == {
        "answerable": True,
        "answer": "정부24 또는 주민센터에서 신청합니다.",
        "evidence_quotes": ["정부24 또는 관할 주민센터 방문 신청"],
    }


def test_answer_light_followup_handles_code_fenced_json() -> None:
    client = _fake(
        {"answerable": True, "answer": "매월 말일까지입니다.", "evidence_quotes": ["매월 말일까지"]},
        fenced=True,
    )

    out = answer_light_followup("신청기한: 매월 말일까지", "언제까지 신청해요?", llm_client=client)

    assert out is not None and out["answer"] == "매월 말일까지입니다."


def test_answer_light_followup_normalizes_not_answerable() -> None:
    client = _fake({"answerable": False, "answer": "몰라요", "evidence_quotes": ["지어낸 근거"]})

    out = answer_light_followup("신청기한: 매월 말일까지", "다른 정책도 있어요?", llm_client=client)

    assert out == {"answerable": False, "answer": "", "evidence_quotes": []}


def test_answer_light_followup_returns_none_on_bad_shapes() -> None:
    ctx, q = "신청기한: 매월 말일까지", "언제까지 신청해요?"
    # answerable가 bool이 아님
    assert answer_light_followup(ctx, q, llm_client=_fake({"answerable": "yes", "answer": "x"})) is None
    # answerable=true인데 답 문장이 비어 있음
    assert answer_light_followup(ctx, q, llm_client=_fake({"answerable": True, "answer": "   "})) is None
    # JSON이 아님
    assert answer_light_followup(ctx, q, llm_client=FakeLLMClient("도저히 모르겠습니다")) is None


def test_answer_light_followup_returns_none_on_llm_failure_or_empty_input() -> None:
    assert answer_light_followup("ctx", "q", llm_client=FailingLLMClient()) is None
    assert answer_light_followup("", "질문", llm_client=_fake({"answerable": True, "answer": "x"})) is None
    assert answer_light_followup("ctx", "", llm_client=_fake({"answerable": True, "answer": "x"})) is None


# --- 사용자 프로필(파악한 정보) -------------------------------------------


def test_profile_facts_labels_each_item_and_skips_bad_shapes() -> None:
    facts = _profile_facts(
        [
            {"key": "region", "label": "지역", "value": "서울특별시"},
            {"key": "age", "label": "나이", "value": "만 27세"},
            {"key": "x", "label": "소득", "value": ""},  # 값 없음 -> 제외
            {"label": "", "value": "무시"},  # 라벨 없음 -> 제외
            "문자열",  # dict 아님 -> 제외
        ]
    )

    assert facts == ["사용자 정보 - 지역: 서울특별시", "사용자 정보 - 나이: 만 27세"]


def test_profile_facts_handles_none_and_empty() -> None:
    assert _profile_facts(None) == []
    assert _profile_facts([]) == []


def test_respond_to_policy_question_uses_user_profile_in_context() -> None:
    # 모델이 프로필 문장을 근거로 들면, 그 문장이 컨텍스트에 실제로 있으므로
    # 검증(C)을 통과해 answer가 나온다.
    client = _fake(
        {
            "answerable": True,
            "answer": "서울에 거주하시고 이 정책은 전국 대상이므로 신청하실 수 있습니다.",
            "evidence_quotes": [
                "사용자 정보 - 지역: 서울특별시",
                "지원대상: 전국의 만 19~34세 무주택 청년",
            ],
        }
    )
    policy = {
        "title": "청년월세 특별지원",
        "detail": {"support_target": "전국의 만 19~34세 무주택 청년"},
    }

    out = respond_to_policy_question(
        policy,
        "제가 서울 사는데 이 정책 받을 수 있어요?",
        llm_client=client,
        user_profile=[{"key": "region", "label": "지역", "value": "서울특별시"}],
    )

    assert out["kind"] == "answer"
    assert "서울" in out["text"]


def test_respond_to_policy_question_without_profile_still_works() -> None:
    client = _fake(
        {
            "answerable": True,
            "answer": "만 19~34세 무주택 청년이 대상입니다.",
            "evidence_quotes": ["지원대상: 만 19~34세 무주택 청년"],
        }
    )
    policy = {"title": "청년월세", "detail": {"support_target": "만 19~34세 무주택 청년"}}

    out = respond_to_policy_question(policy, "누가 대상이에요?", llm_client=client)

    assert out["kind"] == "answer"
