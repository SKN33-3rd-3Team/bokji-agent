"""N1~N14 공식 서비스에 연결된 상담 화면."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from ..constants import (
    BOT_AVATAR,
    DEFAULT_TOP_K,
    EXAMPLE_PROMPTS,
    USER_AVATAR,
    VECTOR_DB_DIR,
)
from ..nav import goto
from ..pipeline import run_pipeline
from ..rendering import render_result
from ..session import clear_conversation_state, escape_md, logout, new_conversation

_GENERIC_ERROR_MESSAGE = (
    "상담 처리 중 오류가 발생했습니다. 잠시 후 다시 시도하거나 대화를 초기화해 주세요."
)
_SETUP_ERROR_MESSAGE = "서비스 실행 설정을 확인할 수 없습니다. 관리자에게 문의해 주세요."


def _reset_conversation() -> None:
    clear_conversation_state()


def _render_intro() -> None:
    with st.container(border=True):
        st.markdown(
            "#### :material/waving_hand: 안녕하세요, 복지 에이전트입니다\n"
            "거주 지역과 기본 정보를 알려주시면 받을 수 있는 지원 제도를 찾아 "
            "**자격 · 지원금 · 중복수급**을 근거와 함께 확인해 드려요."
        )
        st.caption("아래 예시를 눌러 바로 시작할 수 있어요.")
        for idx, example in enumerate(EXAMPLE_PROMPTS):
            preview = example if len(example) <= 46 else example[:45] + "…"
            if st.button(
                preview,
                key=f"ex_{idx}",
                width="stretch",
                icon=":material/bolt:",
            ):
                st.session_state.pending_prompt = example
                st.rerun()


def _render_sidebar() -> int:
    with st.sidebar:
        st.subheader(":material/tune: 설정")
        top_k = st.slider(
            "정책 후보 수",
            min_value=3,
            max_value=15,
            value=DEFAULT_TOP_K,
            key="topk",
            help="검색이 가져오는 후보 정책 청크 수. 늘리면 더 많은 제도를 훑습니다.",
        )

        if st.button(
            "대화 초기화",
            icon=":material/delete_sweep:",
            width="stretch",
            type="secondary",
        ):
            _reset_conversation()
            st.rerun()

        st.markdown(":material/account_circle: **계정**")
        auth_user = st.session_state.get("auth_user")
        if auth_user:
            name = auth_user.get("display_name") or auth_user.get("username", "")
            st.caption(f":material/check_circle: {escape_md(name)} 님으로 로그인됨")
        account = st.container(horizontal=True)
        if not auth_user:
            if account.button(
                "로그인",
                icon=":material/login:",
                width="stretch",
                key="sb_login",
            ):
                goto("login")
            if account.button(
                "회원가입",
                icon=":material/person_add:",
                width="stretch",
                key="sb_signup",
            ):
                goto("signup")
        if st.button(
            "마이페이지",
            icon=":material/person:",
            width="stretch",
            key="sb_mypage",
        ):
            goto("mypage")
        if auth_user:
            if st.button(
                "로그아웃",
                icon=":material/logout:",
                width="stretch",
                key="sb_logout",
                type="secondary",
            ):
                logout()
                st.rerun()

    return top_k


def _render_result_safely(result: Mapping[str, object]) -> None:
    try:
        render_result(result)
    except Exception:  # 내부 예외나 비밀값은 화면에 노출하지 않는다.
        st.error(_GENERIC_ERROR_MESSAGE, icon=":material/error:")


def _render_history() -> None:
    for message in st.session_state.messages:
        avatar = USER_AVATAR if message["role"] == "user" else BOT_AVATAR
        with st.chat_message(message["role"], avatar=avatar):
            if message["role"] == "user":
                st.markdown(message["content"])
            elif message.get("error"):
                st.error(message["error"], icon=":material/error:")
            else:
                _render_result_safely(message["result"])


def page_chat() -> None:
    st.caption(
        "거주 지역·기본 정보를 바탕으로 지원 제도를 찾아 자격·지원금·중복수급을 "
        "근거와 함께 확인합니다."
    )
    top_k = _render_sidebar()

    if not (VECTOR_DB_DIR / "chroma.sqlite3").is_file():
        st.error(
            "서비스 데이터베이스가 준비되지 않았습니다. 관리자에게 문의해 주세요.",
            icon=":material/database_off:",
        )
        st.caption("사전 구축된 `data/vector_db`가 필요하며 샘플 데이터는 자동 색인하지 않습니다.")
        return

    _render_history()
    if not st.session_state.messages and not st.session_state.pending_prompt:
        _render_intro()

    typed = st.chat_input(
        "메시지를 입력하세요 (예: 서울 사는 2021년 3월생 아이 유아학비 지원 되나요?)"
    )
    pending = st.session_state.pop("pending_prompt", None)
    prompt = typed or pending
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(prompt)

    result: Mapping[str, object] | None = None
    error_message: str | None = None
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        with st.spinner("상담을 진행하고 있어요"):
            try:
                result = run_pipeline(
                    user_input=prompt,
                    session_id=st.session_state.conversation_id,
                    awaiting_followup=st.session_state.awaiting_followup,
                    top_k=top_k,
                )
            except SystemExit:
                error_message = _SETUP_ERROR_MESSAGE
            except Exception:  # 서비스 내부 정보나 traceback은 화면에 노출하지 않는다.
                error_message = _GENERIC_ERROR_MESSAGE

        if error_message:
            st.error(error_message, icon=":material/error:")
        elif result is not None:
            _render_result_safely(result)

    if error_message:
        st.session_state.messages.append(
            {"role": "assistant", "error": error_message}
        )
    elif result is not None:
        st.session_state.messages.append({"role": "assistant", "result": dict(result)})
        if result.get("status") == "needs_input":
            st.session_state.awaiting_followup = True
        elif result.get("status") == "answered":
            new_conversation(st.session_state, clear_messages=False)

    st.rerun()
