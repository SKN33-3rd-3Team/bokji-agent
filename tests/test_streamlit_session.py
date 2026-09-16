from __future__ import annotations

import uuid

from streamlit.testing.v1 import AppTest

from streamlit_ui.session import get_last_answered_result, new_conversation


def test_new_conversation_rotates_id_and_preserves_visible_history() -> None:
    messages = [{"role": "user", "content": "이전 질문"}]
    state = {
        "conversation_id": "old",
        "awaiting_followup": True,
        "pending_prompt": "남은 예시",
        "messages": messages,
    }

    new_conversation(state, clear_messages=False)

    uuid.UUID(state["conversation_id"])
    assert state["conversation_id"] != "old"
    assert state["awaiting_followup"] is False
    assert state["pending_prompt"] is None
    assert state["messages"] is messages


def test_reset_starts_blank_conversation() -> None:
    state = {
        "conversation_id": "old",
        "awaiting_followup": True,
        "messages": [{"role": "user", "content": "이전 질문"}],
    }

    new_conversation(state, clear_messages=True)

    assert state["conversation_id"] != "old"
    assert state["awaiting_followup"] is False
    assert state["messages"] == []


def test_new_conversation_clears_detail_chat_state() -> None:
    """새 상담은 특정 정책에 묶이지 않으므로 상세 문의 채팅방도 해제한다."""

    state = {
        "conversation_id": "old",
        "awaiting_followup": False,
        "messages": [],
        "detail_chat_policy": {"policy_id": "P1"},
        "detail_chat_history": [{"role": "user", "content": "질문"}],
    }

    new_conversation(state, clear_messages=False)

    assert "detail_chat_policy" not in state
    assert "detail_chat_history" not in state


def test_get_last_answered_result_picks_most_recent_answered() -> None:
    messages = [
        {"role": "user", "content": "월세 지원 되나요"},
        {"role": "assistant", "result": {"status": "answered", "final_answer": "첫 답"}},
        {"role": "user", "content": "다시 물어봄"},
        {"role": "assistant", "result": {"status": "answered", "final_answer": "두 번째 답"}},
    ]

    result = get_last_answered_result(messages)

    assert result is not None
    assert result["final_answer"] == "두 번째 답"


def test_get_last_answered_result_skips_needs_input_and_error() -> None:
    messages = [
        {"role": "assistant", "result": {"status": "answered", "final_answer": "완결된 답"}},
        {"role": "assistant", "result": {"status": "needs_input", "question": "지역이 어디세요"}},
        {"role": "assistant", "error": "상담 처리 중 오류"},
    ]

    result = get_last_answered_result(messages)

    assert result is not None
    assert result["final_answer"] == "완결된 답"


def test_get_last_answered_result_returns_none_when_no_answer() -> None:
    messages = [
        {"role": "user", "content": "질문만 함"},
        {"role": "assistant", "result": {"status": "needs_input", "question": "나이가 어떻게 되세요"}},
    ]

    assert get_last_answered_result(messages) is None
    assert get_last_answered_result([]) is None


def test_get_last_answered_result_returns_a_copy() -> None:
    stored = {"status": "answered", "final_answer": "원본"}
    messages = [{"role": "assistant", "result": stored}]

    result = get_last_answered_result(messages)
    result["final_answer"] = "바뀐 값"

    assert stored["final_answer"] == "원본"


# ── 개발용 자동 로그인 호출 경로 분리 (PR #60 리뷰 회귀 테스트) ──────────
#
# app.py(실서비스 진입점)는 init_session()만 부르고, DEV_AUTOLOGIN_EMAIL이
# 운영 환경에 실수로 남아 있어도 자동 로그인이 실행되지 않아야 한다.
# demo_ui_fake.py 같은 명시적 개발용 진입점만 maybe_dev_autologin()을 따로
# 불러 자동 로그인을 켠다. 계정이 실제로 존재하는지와 무관하게,
# ``_dev_autologin_done`` 플래그가 찍혔는지로 "이 함수가 실행을 시도했는지"
# 를 판별한다(계정이 없으면 예외를 삼키고도 이 플래그는 남긴다).


def test_init_session_alone_never_attempts_dev_autologin(monkeypatch) -> None:
    monkeypatch.setenv("DEV_AUTOLOGIN_EMAIL", "dev@example.com")
    monkeypatch.delenv("AUTH_DB_URL", raising=False)

    script = """\
from streamlit_ui.session import init_session

init_session()
"""
    app = AppTest.from_string(script).run(timeout=10)

    assert app.session_state["auth_user"] is None
    assert "_dev_autologin_done" not in app.session_state


def test_explicit_maybe_dev_autologin_call_still_runs(monkeypatch) -> None:
    monkeypatch.setenv("DEV_AUTOLOGIN_EMAIL", "dev@example.com")
    monkeypatch.delenv("AUTH_DB_URL", raising=False)

    script = """\
from streamlit_ui.session import init_session, maybe_dev_autologin

init_session()
maybe_dev_autologin()
"""
    app = AppTest.from_string(script).run(timeout=10)

    # 이 이메일로 가입된 계정이 없어 로그인 자체는 실패하지만(예외를
    # 삼킨다), 함수가 실제로 실행을 시도했다는 표시는 남는다 - init_session
    # 단독 호출과 구분하는 것이 이 테스트의 목적이다.
    assert app.session_state["_dev_autologin_done"] is True
