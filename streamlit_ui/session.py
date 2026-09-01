"""Streamlit 상담 세션과 로그인 사용자 상태를 관리한다."""

from __future__ import annotations

import re
import uuid
from collections.abc import MutableMapping
from typing import Any

import streamlit as st

# 로그인/회원가입/마이페이지 폼이 쓰는 위젯 키 접두사. 로그아웃·계정 전환 때
# 한꺼번에 비운다(같은 브라우저 세션에서 다른 사용자가 이어 쓸 때 이전 입력이
# 새 사용자 폼에 남지 않게).
_TRANSIENT_AUTH_PREFIXES = ("login_", "su_", "pe_", "pc_", "da_")

_MD_SPECIAL_RE = re.compile(r"([\\`*_\[\]()#>~|$])")


def escape_md(text: object) -> str:
    """사용자 입력 문자열을 Markdown 안에 넣기 전에 특수문자를 이스케이프한다."""

    return _MD_SPECIAL_RE.sub(r"\\\1", str(text or ""))


def new_conversation(
    state: MutableMapping[str, Any], *, clear_messages: bool
) -> None:
    """새 LangGraph 세션을 만들고 이전 상담의 상태를 비운다."""

    state["conversation_id"] = str(uuid.uuid4())
    state["awaiting_followup"] = False
    state["pending_prompt"] = None
    # 공식 서비스가 소유하지 않는 이전 UI 계약의 민감 슬롯도 남기지 않는다.
    state["slots"] = {}
    state["slot_ask_counts"] = {}
    if clear_messages:
        state["messages"] = []


def init_session() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("pending_prompt", None)
    st.session_state.setdefault("awaiting_followup", False)
    if "conversation_id" not in st.session_state:
        new_conversation(st.session_state, clear_messages=False)
    st.session_state.setdefault("view", "chat")
    # 로그인 사용자: None 또는 auth_user_dict() 결과.
    # display_name·interests 는 로그인 시 복호화된 값이다(원문 저장 아님).
    st.session_state.setdefault("auth_user", None)


def auth_user_dict(user) -> dict:
    """``rag_chatbot.auth.AuthUser`` -> ``st.session_state['auth_user']`` 형태."""

    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "created_at": user.created_at,
        "region": user.region,
        "interests": list(user.interests),
        "marketing_opt_in": user.marketing_opt_in,
    }


def clear_auth_form_state() -> None:
    """로그인/회원가입/마이페이지 폼 위젯 상태를 모두 제거한다."""

    for key in [
        k for k in list(st.session_state)
        if isinstance(k, str) and k.startswith(_TRANSIENT_AUTH_PREFIXES)
    ]:
        st.session_state.pop(key, None)
    st.session_state.pop("_mp_forms_user", None)


def clear_conversation_state() -> None:
    """상담 내역·프로필 슬롯을 세션에서 비운다.

    공용 PC 에서 로그아웃·계정 전환 시 이전 사용자의 상담 내용과 소득·장애·
    임신 등 슬롯이 다음 사용자에게 그대로 보이지 않게 한다. init_session 이
    심는 기본값과 같은 형태로 되돌린다.
    """

    new_conversation(st.session_state, clear_messages=True)


def logout() -> None:
    """세션에서 로그인 사용자·폼 입력·상담 상태를 모두 지운다."""

    st.session_state.auth_user = None
    clear_auth_form_state()
    clear_conversation_state()
