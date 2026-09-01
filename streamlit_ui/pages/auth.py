"""로그인 / 회원가입 — 화면 + 인증 연동.

폼 제출은 ``rag_chatbot.auth`` 서비스(SQLite ``users`` 테이블 + bcrypt +
Fernet)로 처리한다. 회원가입은 이름·거주 지역·관심 지원조건·마케팅 동의까지
저장하고, 로그인 성공 시 그 프로필을 복호화해 ``st.session_state["auth_user"]``
(``session.auth_user_dict`` 형태)에 심는다. 마이페이지에서 이 값을 표시하고
``update_profile`` / ``change_password`` 로 수정한다.

로그인 5회 연속 실패 시 계정이 일정 시간 잠긴다(``rag_chatbot.auth.lockout``).
잠금 중에는 ``AccountLockedError`` 로 안내만 하고 폼을 막지는 않는다.
"""

from __future__ import annotations

import streamlit as st
from rag_chatbot.auth import (
    AccountLockedError,
    AuthError,
    InvalidCredentialsError,
    PasswordPolicyError,
    UsernameTakenError,
    authenticate,
    sign_up,
)

from ..constants import INTEREST_OPTIONS, SIDO_OPTIONS
from ..nav import goto
from ..session import auth_user_dict as _user_to_session
from ..session import clear_auth_form_state, clear_conversation_state, escape_md

_REGION_NONE = "선택 안 함"


def _auth_footer_back() -> None:
    # 로그인<->회원가입 이동은 폼 아래 인라인 링크 하나로 충분하다.
    # 여기서는 상담으로 돌아가는 버튼만 둔다.
    st.space("small")
    if st.button("상담으로 돌아가기", key="lnk_chat", icon=":material/arrow_back:",
                 type="secondary"):
        goto("chat")


def _handle_login() -> None:
    email = (st.session_state.get("login_email") or "").strip()
    password = st.session_state.get("login_pw") or ""
    if not email or not password:
        st.error("이메일과 비밀번호를 모두 입력해 주세요.")
        return
    try:
        user = authenticate(email, password)
    except AccountLockedError as exc:
        st.warning(str(exc), icon=":material/lock_clock:")
        return
    except InvalidCredentialsError:
        st.error("이메일 또는 비밀번호가 올바르지 않습니다.")
        return
    except AuthError as exc:
        st.error(str(exc))
        return
    st.session_state.auth_user = _user_to_session(user)
    # 로그인/회원가입 폼 입력(비밀번호 포함)을 세션에서 모두 제거한다.
    clear_auth_form_state()
    # 공용 PC 계정 전환 대비: 로그인 경계에서 이전 사용자의 상담 내역·슬롯을 비운다.
    clear_conversation_state()
    st.toast(f"{escape_md(user.display_name or user.username)} 님, 환영합니다.",
             icon=":material/check_circle:")
    goto("chat")


def _handle_signup() -> None:
    email = (st.session_state.get("su_email") or "").strip()
    password = st.session_state.get("su_pw") or ""
    password2 = st.session_state.get("su_pw2") or ""
    name = (st.session_state.get("su_name") or "").strip()

    if not email or not password:
        st.error("이메일과 비밀번호를 입력해 주세요.")
        return
    if password != password2:
        st.error("비밀번호와 비밀번호 확인이 일치하지 않습니다.")
        return
    if not (st.session_state.get("su_tos") and st.session_state.get("su_privacy")):
        st.error("[필수] 이용약관과 개인정보 수집·이용에 동의해 주세요.")
        return

    region_sel = st.session_state.get("su_region") or _REGION_NONE
    region = "" if region_sel == _REGION_NONE else region_sel
    interests = list(st.session_state.get("su_interests") or [])
    marketing = bool(st.session_state.get("su_marketing"))

    try:
        sign_up(email, password, name, region=region, interests=interests,
                marketing_opt_in=marketing)
    except PasswordPolicyError as exc:
        for violation in exc.violations:
            st.error(violation)
        return
    except UsernameTakenError:
        st.error("이미 가입된 이메일입니다.")
        return
    except AuthError as exc:
        st.error(str(exc))
        return

    # 회원가입 폼 입력(비밀번호·이름·동의 등)을 세션에서 모두 제거한다.
    clear_auth_form_state()
    st.toast("회원가입이 완료되었습니다. 로그인해 주세요.",
             icon=":material/check_circle:")
    goto("login")


def page_login() -> None:
    st.caption("복지 에이전트에 로그인하고 상담 내역과 내 정보를 이어서 관리하세요.")
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        with st.form("form_login", border=True):
            st.markdown("#### :material/login: 로그인")
            st.text_input("이메일", key="login_email", placeholder="you@example.com")
            st.text_input("비밀번호", key="login_pw", type="password",
                          placeholder="비밀번호 입력")
            submitted = st.form_submit_button("로그인", type="primary",
                                              width="stretch")
        if submitted:
            _handle_login()
        c = st.container(horizontal=True)
        c.caption("계정이 없으신가요?")
        if c.button("회원가입", key="login_to_signup"):
            goto("signup")
    _auth_footer_back()


def page_signup() -> None:
    st.caption("몇 가지 정보만 입력하면 맞춤 지원 제도 추천을 받을 수 있어요.")
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        with st.form("form_signup", border=True):
            st.markdown("#### :material/person_add: 회원가입")
            st.text_input("이메일", key="su_email", placeholder="you@example.com")
            st.text_input("비밀번호", key="su_pw", type="password",
                          help="8자 이상, 영문·숫자·특수문자를 섞어 주세요.")
            st.text_input("비밀번호 확인", key="su_pw2", type="password")
            st.text_input("이름", key="su_name", placeholder="실명 또는 닉네임")

            st.space("small")
            st.markdown("**기본 정보 (선택)**")
            st.caption("입력하면 마이페이지에 저장되고 추천에 활용됩니다.")
            st.selectbox("거주 지역", [_REGION_NONE, *SIDO_OPTIONS], key="su_region")
            st.pills("해당하는 지원조건", INTEREST_OPTIONS, selection_mode="multi",
                     key="su_interests", default=[])

            st.space("small")
            st.checkbox("[필수] 서비스 이용약관에 동의합니다.", key="su_tos")
            st.checkbox("[필수] 개인정보 수집·이용에 동의합니다.", key="su_privacy")
            st.checkbox("[선택] 혜택·안내 정보 수신에 동의합니다.", key="su_marketing")

            submitted = st.form_submit_button("회원가입", type="primary",
                                              width="stretch")
        if submitted:
            _handle_signup()
        c = st.container(horizontal=True)
        c.caption("이미 계정이 있으신가요?")
        if c.button("로그인", key="signup_to_login"):
            goto("login")
    _auth_footer_back()
