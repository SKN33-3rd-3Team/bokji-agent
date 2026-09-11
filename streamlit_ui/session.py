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


def md_text(value: object) -> str:
    """Markdown 으로 그릴 문자열에서 ``~``를 ``-``로 바꾼다.

    ``~``는 GFM 취소선(``~~``) 문법과 겹친다. 한 줄에 두 번 나오면
    ("중위소득 30~50% ... 75~100%") 그 사이가 통째로 취소선으로 그려지고,
    한 번만 나와도 렌더러에 따라 문자가 사라진다. 정책 원문에는
    "만 3~5세", "30~50%" 같은 범위 표기가 흔해서 실제로 자주 깨진다.
    범위를 뜻하는 ``-``는 의미가 같고 Markdown 특수문자도 아니다.
    """

    return str(value if value is not None else "").replace("~", "-")


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
    # 사이드바 "파악한 정보"에 쓰는 값(서비스 응답의 output_json["profile"]).
    # 소득·장애 같은 값이 들어 있으므로 새 상담에서는 반드시 비운다.
    state["profile"] = []
    # 정책 상세 문의 사이드 채팅도 해제한다(새 상담은 특정 정책에 묶이지 않는다).
    state.pop("detail_chat_policy", None)
    state.pop("detail_chat_history", None)
    if clear_messages:
        state["messages"] = []


def get_last_answered_result(messages: list) -> dict | None:
    """``messages``에서 가장 최근의 완료된(``status="answered"``) 상담 응답을 찾는다.

    후속질문 경량 응답이 재사용할 컨텍스트다. 답변이 끝나면
    ``new_conversation``이 ``conversation_id``·``slots``·``profile``을 비우지만
    ``messages``는 그대로 남으므로(``clear_messages=False``), 여기서 마지막
    응답(``final_answer``/``final_citations``/``policies`` 포함)을 되찾을 수 있다.

    ``needs_input``(되묻는 중)이나 ``error`` 응답은 건너뛴다 - 완결된 답이 아니다.
    반환값은 원본을 건드리지 않도록 얕은 복사본이다.
    """

    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        result = message.get("result")
        if isinstance(result, dict) and result.get("status") == "answered":
            return dict(result)
    return None


def init_session() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("pending_prompt", None)
    st.session_state.setdefault("awaiting_followup", False)
    st.session_state.setdefault("profile", [])
    if "conversation_id" not in st.session_state:
        new_conversation(st.session_state, clear_messages=False)
    st.session_state.setdefault("view", "chat")
    # 로그인 사용자: None 또는 auth_user_dict() 결과.
    # display_name·interests 는 로그인 시 복호화된 값이다(원문 저장 아님).
    st.session_state.setdefault("auth_user", None)
    _maybe_dev_autologin()


def _maybe_dev_autologin() -> None:
    """개발 편의: ``.env``에 ``DEV_AUTOLOGIN_EMAIL``이 있으면 그 계정으로 자동
    로그인한다. 앱을 재시작할 때마다 로그인·상담을 다시 하지 않아도 된다.

    운영에서는 이 변수를 비워 두면 아무 일도 안 한다. 명시적으로 로그아웃하면
    같은 세션에서는 다시 자동 로그인하지 않는다(``_dev_autologin_done`` 플래그).
    """

    import os

    email = (os.environ.get("DEV_AUTOLOGIN_EMAIL") or "").strip()
    if not email or st.session_state.get("auth_user") is not None:
        return
    if st.session_state.get("_dev_autologin_done"):
        return
    try:
        from rag_chatbot.auth import get_profile

        st.session_state["auth_user"] = auth_user_dict(get_profile(email))
        st.session_state["_dev_autologin_done"] = True
    except Exception:
        # 계정이 없거나 auth DB가 없으면 조용히 넘어간다(그냥 수동 로그인).
        st.session_state["_dev_autologin_done"] = True


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
