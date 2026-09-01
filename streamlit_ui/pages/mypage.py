"""마이페이지 — 화면 시안 (기능 미연결).

표시 값은 모두 더미다. 실제 회원 데이터 연동은 추후.
"""

from __future__ import annotations

import streamlit as st

from ..nav import goto, stub_notice


def page_mypage() -> None:
    st.caption("마이페이지")

    with st.container(border=True):
        top = st.container(horizontal=True, vertical_alignment="center")
        top.markdown("# :material/account_circle:")
        info = top.container()
        info.markdown("#### 홍길동 님")
        info.caption("hong@example.com")
        top.badge("일반 회원", icon=":material/verified_user:", color="violet")
        st.caption("가입일 2026-08-15 · 최근 접속 2026-09-01")

    with st.container(border=True):
        head = st.container(horizontal=True, vertical_alignment="center")
        head.markdown("**내 기본 정보**")
        if head.button("정보 수정", key="mp_edit_profile", icon=":material/edit:"):
            stub_notice()
        fields = [
            ("거주 지역", "서울특별시"), ("성별", "미설정"),
            ("연령대", "30대"), ("소득 구간", "미설정"),
            ("장애 여부", "미설정"), ("취업 상태", "재직"),
        ]
        cols = st.columns(3)
        for i, (label, value) in enumerate(fields):
            box = cols[i % 3].container(border=True)
            box.caption(label)
            box.markdown(f"**{value}**")

    with st.container(border=True):
        st.markdown("**관심 분야**")
        st.markdown(" ".join(f":blue-badge[{x}]" for x in ("육아", "주거", "교육")))

    with st.container(border=True):
        st.markdown("**최근 상담**")
        for title, when, note in (
            ("유아학비 (누리과정) 지원 문의", "2026-08-30", "제도 2건 확인"),
            ("청년 주거 지원 문의", "2026-08-22", "추가 정보 필요"),
            ("한부모 가정 지원 문의", "2026-08-10", "제도 3건 확인"),
        ):
            r = st.container(horizontal=True, vertical_alignment="center")
            r.markdown(f":material/chat: {title}")
            r.caption(f"{when} · {note}")

    actions = st.container(horizontal=True)
    for label, icon, key in (
        ("회원정보 수정", ":material/manage_accounts:", "mp_edit"),
        ("비밀번호 변경", ":material/lock_reset:", "mp_pw"),
        ("로그아웃", ":material/logout:", "mp_logout"),
    ):
        if actions.button(label, icon=icon, key=key):
            stub_notice()
    if actions.button("회원 탈퇴", icon=":material/person_remove:", key="mp_quit",
                      type="secondary"):
        stub_notice()

    st.space("small")
    if st.button("상담으로 돌아가기", icon=":material/arrow_back:", key="mp_back"):
        goto("chat")
