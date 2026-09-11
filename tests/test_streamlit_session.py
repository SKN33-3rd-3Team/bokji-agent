from __future__ import annotations

import uuid

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
