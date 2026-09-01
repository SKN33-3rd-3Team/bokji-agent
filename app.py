"""복지 에이전트 Streamlit 서비스 진입점.

프로젝트 규칙(`docs/PROJECT_COMPLIANCE.md`)의 "서비스: Streamlit으로 실행
가능하게 제공"을 충족하는 실행 파일이다.

    streamlit run app.py

화면·파이프라인 배선·렌더링은 ``streamlit_ui`` 패키지에 나눠 두고, 이 파일은
페이지 설정 → 세션 초기화 → 헤더 → 화면 분기만 한다.
"""

from __future__ import annotations

import streamlit as st
import streamlit_ui  # noqa: F401  # import 경로 부트스트랩(rag_design / rag_chatbot)

from streamlit_ui.pages.auth import page_login, page_signup
from streamlit_ui.pages.chat import page_chat
from streamlit_ui.pages.mypage import page_mypage
from streamlit_ui.session import init_session
from streamlit_ui.theme import localize_menu, render_header

_PAGES = {
    "login": page_login,
    "signup": page_signup,
    "mypage": page_mypage,
    "chat": page_chat,
}


def main() -> None:
    st.set_page_config(
        page_title="복지 에이전트",
        page_icon="💬",
        layout="centered",
    )
    init_session()
    localize_menu()
    render_header()

    view = st.session_state.get("view", "chat")
    _PAGES.get(view, page_chat)()


main()
