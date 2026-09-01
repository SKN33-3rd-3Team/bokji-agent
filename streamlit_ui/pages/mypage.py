"""마이페이지 — 회원 정보 확인 · 기본 정보 수정 · 비밀번호 변경 · 회원 탈퇴.

- 헤더/가입 정보/관심 지원조건은 로그인 세션(``auth_user``)과 DB(``get_profile``)
  에서 실제 값을 읽는다. 회원가입 때 입력한 내용이 그대로 보인다.
- "기본 정보 수정" → ``update_profile`` / "비밀번호 변경" → ``change_password`` /
  "회원 탈퇴" → ``delete_account`` 로 연동돼 있다(비밀번호 "찾기" 는 없다 — 변경만).
- "최근 상담" 은 아직 시안(대화 저장 미구현).
- 로그인하지 않은 상태로 오면 로그인 화면으로 유도한다.
"""

from __future__ import annotations

import streamlit as st
from rag_chatbot.auth import (
    AuthError,
    InvalidCredentialsError,
    PasswordPolicyError,
    UserNotFoundError,
    change_password,
    delete_account,
    get_profile,
    update_profile,
)

from ..constants import INTEREST_OPTIONS, SIDO_OPTIONS
from ..nav import goto
from ..session import auth_user_dict, escape_md, logout

_REGION_NONE = "선택 안 함"
_FORM_KEY_PREFIXES = ("pe_", "pc_", "da_")


def _refresh_profile(username: str) -> dict | None:
    """DB에서 최신 프로필을 읽어 세션을 갱신한다.

    - 계정이 사라졌으면(다른 탭에서 탈퇴 등) 세션을 비우고 ``None``.
    - 그 밖의 오류는 기존 세션 값을 유지한다.
    """

    try:
        user = get_profile(username)
    except UserNotFoundError:
        logout()
        return None
    except AuthError:
        return st.session_state.auth_user
    st.session_state.auth_user = auth_user_dict(user)
    return st.session_state.auth_user


def _reset_forms_if_user_changed(username: str) -> None:
    """로그인 사용자가 바뀌면 폼 위젯 상태를 초기화한다(이전 입력 잔존 방지)."""

    if st.session_state.get("_mp_forms_user") == username:
        return
    for key in [
        k for k in list(st.session_state)
        if isinstance(k, str) and k.startswith(_FORM_KEY_PREFIXES)
    ]:
        st.session_state.pop(key, None)
    st.session_state["_mp_forms_user"] = username


def _handle_profile_edit(username: str, name: str, region_sel: str,
                         interests: list[str]) -> None:
    region = "" if region_sel == _REGION_NONE else region_sel
    try:
        user = update_profile(username, display_name=name.strip(),
                              region=region, interests=interests)
    except AuthError as exc:
        st.error(str(exc))
        return
    st.session_state.auth_user = auth_user_dict(user)
    for key in ("pe_name", "pe_region", "pe_interests"):
        st.session_state.pop(key, None)
    st.toast("기본 정보를 저장했습니다.", icon=":material/check_circle:")
    st.rerun()


def _handle_delete_account(username: str, password: str, agree: bool) -> None:
    if not agree:
        st.error("탈퇴 동의에 체크해 주세요.")
        return
    if not password:
        st.error("비밀번호를 입력해 주세요.")
        return
    try:
        delete_account(username, password)
    except InvalidCredentialsError:
        st.error("비밀번호가 올바르지 않습니다.")
        return
    except AuthError as exc:
        st.error(str(exc))
        return
    logout()
    st.toast("회원 탈퇴가 완료되었습니다.", icon=":material/check_circle:")
    goto("chat")


def _handle_password_change(username: str, current: str, new1: str,
                            new2: str) -> None:
    if not current or not new1:
        st.error("현재 비밀번호와 새 비밀번호를 입력해 주세요.")
        return
    if new1 != new2:
        st.error("새 비밀번호와 확인이 일치하지 않습니다.")
        return
    try:
        change_password(username, current, new1)
    except InvalidCredentialsError:
        st.error("현재 비밀번호가 올바르지 않습니다.")
        return
    except PasswordPolicyError as exc:
        for violation in exc.violations:
            st.error(violation)
        return
    except AuthError as exc:
        st.error(str(exc))
        return
    for key in ("pc_cur", "pc_new1", "pc_new2"):
        st.session_state.pop(key, None)
    st.toast("비밀번호를 변경했습니다.", icon=":material/check_circle:")
    st.rerun()


def _seed(key: str, value) -> dict:
    """키가 세션에 없을 때만 위젯 기본값을 넘긴다(중복 지정 경고 방지)."""

    return {} if key in st.session_state else {"value": value}


def _profile_edit_form(user: dict) -> None:
    with st.container(border=True):
        st.markdown("**기본 정보 수정**")
        region = user.get("region") or ""
        region_idx = ([_REGION_NONE, *SIDO_OPTIONS].index(region)
                      if region in SIDO_OPTIONS else 0)
        saved_interests = [i for i in (user.get("interests") or [])
                           if i in INTEREST_OPTIONS]
        with st.form("form_profile_edit"):
            name = st.text_input("이름", key="pe_name",
                                 **_seed("pe_name", user.get("display_name", "")))
            region_sel = st.selectbox(
                "거주 지역", [_REGION_NONE, *SIDO_OPTIONS], key="pe_region",
                **({} if "pe_region" in st.session_state
                   else {"index": region_idx}),
            )
            interests = st.pills(
                "관심 지원조건", INTEREST_OPTIONS, selection_mode="multi",
                key="pe_interests",
                **({} if "pe_interests" in st.session_state
                   else {"default": saved_interests}),
            )
            saved = st.form_submit_button("저장", type="primary")
        if saved:
            _handle_profile_edit(user["username"], name, region_sel,
                                 list(interests or []))


def _password_form(user: dict) -> None:
    with st.container(border=True):
        st.markdown("**비밀번호 변경**")
        st.caption("8자 이상, 영문·숫자·특수문자를 섞어 주세요.")
        with st.form("form_password_change", clear_on_submit=False):
            cur = st.text_input("현재 비밀번호", type="password", key="pc_cur")
            new1 = st.text_input("새 비밀번호", type="password", key="pc_new1")
            new2 = st.text_input("새 비밀번호 확인", type="password", key="pc_new2")
            changed = st.form_submit_button("비밀번호 변경", type="primary")
        if changed:
            _handle_password_change(user["username"], cur, new1, new2)


def _delete_account_form(user: dict) -> None:
    with st.container(border=True):
        st.markdown("**회원 탈퇴**")
        st.caption("탈퇴하면 계정과 저장된 정보(이름·지역·관심조건)가 즉시 "
                   "삭제되며 되돌릴 수 없습니다.")
        with st.form("form_delete_account", clear_on_submit=False):
            pw = st.text_input("비밀번호 확인", type="password", key="da_pw")
            agree = st.checkbox("위 내용을 확인했으며 탈퇴에 동의합니다.",
                                key="da_agree")
            submitted = st.form_submit_button("회원 탈퇴", type="secondary")
        if submitted:
            _handle_delete_account(user["username"], pw, agree)


def page_mypage() -> None:
    session_user = st.session_state.get("auth_user")
    if not session_user:
        st.info("로그인이 필요한 화면입니다.")
        if st.button("로그인하러 가기", icon=":material/login:", key="mp_need_login"):
            goto("login")
        return

    user = _refresh_profile(session_user["username"])
    if not user:
        st.info("세션이 만료되었습니다. 다시 로그인해 주세요.")
        if st.button("로그인하러 가기", icon=":material/login:", key="mp_expired_login"):
            goto("login")
        return

    _reset_forms_if_user_changed(user["username"])
    st.caption("마이페이지")

    display_name = user.get("display_name") or user.get("username", "")
    joined = (user.get("created_at") or "")[:10]

    with st.container(border=True):
        top = st.container(horizontal=True, vertical_alignment="center")
        top.markdown("# :material/account_circle:")
        info = top.container()
        info.markdown(f"#### {escape_md(display_name)} 님")
        info.caption(escape_md(user.get("username", "")))
        top.badge("일반 회원", icon=":material/verified_user:", color="violet")
        st.caption(f"가입일 {joined or '-'}")

    with st.container(border=True):
        st.markdown("**내 가입 정보**")
        region = user.get("region") or "미설정"
        interests = user.get("interests") or []
        marketing = "동의" if user.get("marketing_opt_in") else "미동의"
        rows = [("거주 지역", region), ("마케팅 수신", marketing)]
        cols = st.columns(2)
        for i, (label, value) in enumerate(rows):
            box = cols[i % 2].container(border=True)
            box.caption(label)
            box.markdown(f"**{value}**")
        st.caption("관심 지원조건")
        if interests:
            st.markdown(" ".join(f":blue-badge[{escape_md(x)}]" for x in interests))
        else:
            st.markdown("**미설정**")

    _profile_edit_form(user)
    _password_form(user)
    _delete_account_form(user)

    with st.container(border=True):
        st.markdown("**최근 상담** (예시)")
        for title, when, note in (
            ("유아학비 (누리과정) 지원 문의", "2026-08-30", "제도 2건 확인"),
            ("청년 주거 지원 문의", "2026-08-22", "추가 정보 필요"),
        ):
            r = st.container(horizontal=True, vertical_alignment="center")
            r.markdown(f":material/chat: {title}")
            r.caption(f"{when} · {note}")

    actions = st.container(horizontal=True)
    if actions.button("로그아웃", icon=":material/logout:", key="mp_logout"):
        logout()
        goto("chat")
    if actions.button("상담으로 돌아가기", icon=":material/arrow_back:", key="mp_back",
                      type="secondary"):
        goto("chat")
