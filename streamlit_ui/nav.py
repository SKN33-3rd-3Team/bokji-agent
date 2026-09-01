"""화면 전환 헬퍼 — ``view`` 세션 상태를 바꾸고 rerun 한다."""

from __future__ import annotations

import streamlit as st


def goto(view: str) -> None:
    st.session_state.view = view
    st.rerun()


def stub_notice() -> None:
    st.toast("화면 시안입니다 · 기능은 아직 연결되지 않았습니다",
             icon=":material/construction:")
