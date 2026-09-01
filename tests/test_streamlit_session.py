from __future__ import annotations

import uuid

from streamlit_ui.session import new_conversation


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
