"""Streamlit 세션과 LangGraph 대화 ID를 초기화한다."""

from __future__ import annotations

import uuid
from collections.abc import MutableMapping
from typing import Any

import streamlit as st


def new_conversation(
    state: MutableMapping[str, Any], *, clear_messages: bool
) -> None:
    """새 질문용 LangGraph 세션을 만들고 N3 대기 상태를 비운다."""

    state["conversation_id"] = str(uuid.uuid4())
    state["awaiting_followup"] = False
    state["pending_prompt"] = None
    if clear_messages:
        state["messages"] = []


def init_session() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("pending_prompt", None)
    st.session_state.setdefault("awaiting_followup", False)
    if "conversation_id" not in st.session_state:
        new_conversation(st.session_state, clear_messages=False)
    st.session_state.setdefault("view", "chat")
