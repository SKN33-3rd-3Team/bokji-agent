from __future__ import annotations

import json

from src.rag_chatbot.llm import FailingLLMClient, FakeLLMClient
from src.rag_chatbot.light_followup import (
    _profile_facts,
    answer_light_followup,
    build_policy_context,
    respond_to_policy_question,
    verify_answer_consistency,
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


class _SequencedLLMClient:
    """호출할 때마다 다음 응답을 순서대로 돌려주는 가짜 LLM.

    ``FakeLLMClient``는 항상 같은 응답 하나만 돌려주는데,
    ``respond_to_policy_question``은 이제 한 턴에 LLM을 두 번 부른다
    (①답변 생성 ②``verify_answer_consistency``의 정합성 재검증, PR #59 리뷰
    피드백 반영) - 두 호출에 서로 다른 JSON을 줘야 하는 통합 테스트에서만
    쓴다. 준비한 응답을 다 쓰면(테스트가 예상 못 한 추가 호출) 바로
    ``IndexError``를 내서 실수로 놓친 호출을 조용히 숨기지 않는다.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, prompt: str, *, system: str | None = None,
                 max_tokens: int | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        return self._responses.pop(0)


def _consistent_json() -> str:
    return json.dumps({"consistent": True}, ensure_ascii=False)


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


def test_profile_facts_excludes_interests() -> None:
    # interests(관심 분야)는 검색 질의를 넓히는 힌트일 뿐 자격 판정 조건이나
    # 확정된 사용자 정보가 아니다. 다른 슬롯과 같은 "사용자 정보 - ..." 형식
    # 으로 프롬프트에 들어가면 LLM이 "사용자가 청년이다"처럼 미확정 선택을
    # 확정 사실로 오해할 수 있어 제외한다(PR #59 리뷰 피드백 반영).
    facts = _profile_facts(
        [
            {"key": "region", "label": "지역", "value": "경기도"},
            {"key": "interests", "label": "관심 분야", "value": "청년"},
        ]
    )

    assert facts == ["사용자 정보 - 지역: 경기도"]


def test_respond_to_policy_question_uses_user_profile_in_context() -> None:
    # 모델이 프로필 문장을 근거로 들면, 그 문장이 컨텍스트에 실제로 있으므로
    # 검증(C1: verify_light_answer)을 통과하고, 두 번째 LLM 호출인
    # verify_answer_consistency(C2)도 통과해야 answer가 나온다(PR #59
    # 리뷰 피드백으로 C2가 추가되면서 LLM 호출이 하나 더 필요해졌다).
    client = _SequencedLLMClient([
        json.dumps(
            {
                "answerable": True,
                "answer": "서울에 거주하시고 이 정책은 전국 대상이므로 신청하실 수 있습니다.",
                "evidence_quotes": [
                    "사용자 정보 - 지역: 서울특별시",
                    "지원대상: 전국의 만 19~34세 무주택 청년",
                ],
            },
            ensure_ascii=False,
        ),
        _consistent_json(),
    ])
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
    client = _SequencedLLMClient([
        json.dumps(
            {
                "answerable": True,
                "answer": "만 19~34세 무주택 청년이 대상입니다.",
                "evidence_quotes": ["지원대상: 만 19~34세 무주택 청년"],
            },
            ensure_ascii=False,
        ),
        _consistent_json(),
    ])
    policy = {"title": "청년월세", "detail": {"support_target": "만 19~34세 무주택 청년"}}

    out = respond_to_policy_question(policy, "누가 대상이에요?", llm_client=client)

    assert out["kind"] == "answer"


# --- verify_answer_consistency (PR #59 blocker: [답변 <-> 근거] 정합성) ----


def test_verify_answer_consistency_passes_when_llm_says_consistent() -> None:
    client = FakeLLMClient(_consistent_json())

    assert verify_answer_consistency(
        "월 최대 20만원을 최대 12개월 지원합니다.",
        ["월 최대 20만원을 최대 12개월 지원한다."],
        llm_client=client,
    ) is True


def test_verify_answer_consistency_fails_when_llm_says_inconsistent() -> None:
    # 근거는 진짜(verify_light_answer 통과)지만, 답변이 근거와 다른 말(다른
    # 금액)을 하는 경우 - verify_light_answer만으로는 못 잡던 케이스다.
    client = FakeLLMClient(json.dumps({"consistent": False}))

    assert verify_answer_consistency(
        "월 최대 30만원을 지원합니다.",
        ["월 최대 20만원을 최대 12개월 지원한다."],
        llm_client=client,
    ) is False


def test_verify_answer_consistency_fails_closed_on_llm_failure() -> None:
    assert verify_answer_consistency(
        "답변", ["근거"], llm_client=FailingLLMClient(), attempts=1
    ) is False


def test_verify_answer_consistency_fails_closed_on_malformed_response() -> None:
    # JSON은 맞지만 consistent 키가 bool이 아니거나 없음 - 이것도 "확인
    # 못 함"이므로 통과가 아니라 실패로 본다.
    assert verify_answer_consistency(
        "답변", ["근거"], llm_client=FakeLLMClient(json.dumps({})), attempts=1
    ) is False
    assert verify_answer_consistency(
        "답변", ["근거"],
        llm_client=FakeLLMClient(json.dumps({"consistent": "true"})), attempts=1,
    ) is False
    assert verify_answer_consistency(
        "답변", ["근거"], llm_client=FakeLLMClient("이건 JSON이 아님"), attempts=1
    ) is False


def test_verify_answer_consistency_fails_on_empty_answer_or_quotes() -> None:
    client = FakeLLMClient(_consistent_json())

    assert verify_answer_consistency("", ["근거"], llm_client=client) is False
    assert verify_answer_consistency("답변", [], llm_client=client) is False
    assert verify_answer_consistency("답변", None, llm_client=client) is False


def test_respond_to_policy_question_falls_back_when_answer_contradicts_evidence() -> None:
    # blocker 재현: LLM이 진짜 원문을 근거(evidence_quotes)로 들었지만(그래서
    # verify_light_answer는 통과), 실제 답변 문장은 그 근거와 다른 금액을
    # 말한다 - verify_answer_consistency가 이걸 잡아 guidance로 폴백해야
    # 한다(PR #59 리뷰 피드백, blocker로 지적됨).
    client = _SequencedLLMClient([
        json.dumps(
            {
                "answerable": True,
                "answer": "월 최대 30만원을 지원합니다.",
                "evidence_quotes": ["월 최대 20만원, 최대 12개월"],
            },
            ensure_ascii=False,
        ),
        json.dumps({"consistent": False}, ensure_ascii=False),
    ])

    out = respond_to_policy_question(_POLICY, "한 달에 얼마 받아요?", llm_client=client)

    assert out["kind"] == "guidance"
