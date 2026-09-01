"""로그인 / 회원가입 — 화면 시안 (기능 미연결).

폼 제출은 ``stub_notice()`` 로 안내만 띄운다. 실제 인증 연동은 추후.
"""

from __future__ import annotations

import streamlit as st

from ..constants import INTEREST_OPTIONS, SIDO_OPTIONS
from ..nav import goto, stub_notice


def _auth_footer_links(active: str) -> None:
    st.space("small")
    row = st.container(horizontal=True)
    if active != "login":
        if row.button("로그인", key="lnk_login", icon=":material/login:"):
            goto("login")
    if active != "signup":
        if row.button("회원가입", key="lnk_signup", icon=":material/person_add:"):
            goto("signup")
    if row.button("상담으로 돌아가기", key="lnk_chat", icon=":material/arrow_back:",
                  type="secondary"):
        goto("chat")


def page_login() -> None:
    st.caption("복지 에이전트에 로그인하고 상담 내역과 내 정보를 이어서 관리하세요.")
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        with st.form("form_login", border=True):
            st.markdown("#### :material/login: 로그인")
            st.text_input("이메일", key="login_email", placeholder="you@example.com")
            st.text_input("비밀번호", key="login_pw", type="password",
                          placeholder="비밀번호 입력")
            st.checkbox("로그인 상태 유지", key="login_keep")
            submitted = st.form_submit_button("로그인", type="primary",
                                              width="stretch")
        if submitted:
            stub_notice()
        c = st.container(horizontal=True)
        if c.button("비밀번호 찾기", key="login_findpw"):
            stub_notice()
        c.caption("계정이 없으신가요?")
        if c.button("회원가입", key="login_to_signup"):
            goto("signup")
    _auth_footer_links("login")


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
            st.selectbox("거주 지역", ["선택 안 함", *SIDO_OPTIONS], key="su_region")
            st.pills("해당하는 지원조건", INTEREST_OPTIONS, selection_mode="multi",
                     key="su_interests", default=[])

            st.space("small")
            st.checkbox("[필수] 서비스 이용약관에 동의합니다.", key="su_tos")
            st.checkbox("[필수] 개인정보 수집·이용에 동의합니다.", key="su_privacy")
            st.checkbox("[선택] 혜택·안내 정보 수신에 동의합니다.", key="su_marketing")

            submitted = st.form_submit_button("회원가입", type="primary",
                                              width="stretch")
        if submitted:
            stub_notice()
        c = st.container(horizontal=True)
        c.caption("이미 계정이 있으신가요?")
        if c.button("로그인", key="signup_to_login"):
            goto("login")
    _auth_footer_links("signup")
