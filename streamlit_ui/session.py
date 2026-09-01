"""세션 상태 초기화 — 한 곳에서만 기본값을 심는다."""

from __future__ import annotations

import streamlit as st


def init_session() -> None:
    st.session_state.setdefault("messages", [])       # [{role, kind, ...}]
    st.session_state.setdefault("slots", {})
    st.session_state.setdefault("slot_ask_counts", {})
    st.session_state.setdefault("debug", False)
    st.session_state.setdefault("pending_prompt", None)
    # 샘플 색인 완료 toast 를 세션당 한 번만 띄우기 위한 플래그
    st.session_state.setdefault("ingest_toast_shown", False)
    # 화면 전환: chat | login | signup | mypage
    st.session_state.setdefault("view", "chat")
