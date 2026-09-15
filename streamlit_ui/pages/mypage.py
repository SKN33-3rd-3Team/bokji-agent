"""마이페이지 — 회원 정보 확인 · 기본 정보 수정 · 비밀번호 변경 · 회원 탈퇴.

- 헤더/가입 정보/관심 지원조건은 로그인 세션(``auth_user``)과 DB(``get_profile``)
  에서 실제 값을 읽는다. 회원가입 때 입력한 내용이 그대로 보인다.
- "기본 정보 수정" → ``update_profile`` / "비밀번호 변경" → ``change_password`` /
  "회원 탈퇴" → ``delete_account`` 로 연동돼 있다(비밀번호 "찾기" 는 없다 — 변경만).
- 로그인하지 않은 상태로 오면 로그인 화면으로 유도한다.
"""

from __future__ import annotations

import io
from datetime import date

import streamlit as st
from PIL import Image, UnidentifiedImageError
from rag_chatbot.auth import (
    AuthError,
    InvalidCredentialsError,
    PasswordPolicyError,
    UserNotFoundError,
    authenticate,
    change_password,
    delete_account,
    get_profile,
    update_profile,
)

from ..constants import (
    BIRTH_DATE_MIN,
    DISABILITY_CODE_BY_LABEL_KO,
    DISABILITY_LABELS_KO,
    DISABILITY_NONE,
    GENDER_CODE_BY_LABEL_KO,
    GENDER_LABELS_KO,
    GENDER_NONE,
    HOUSEHOLD_TYPE_CODE_BY_LABEL_KO,
    HOUSEHOLD_TYPE_LABELS_KO,
    INCOME_BRACKET_CODE_BY_LABEL_KO,
    INCOME_BRACKET_LABELS_KO,
    INCOME_BRACKET_NONE,
    SIDO_OPTIONS,
    SIGNUP_INTEREST_OPTIONS,
    VETERAN_CODE_BY_LABEL_KO,
    VETERAN_LABELS_KO,
    VETERAN_NONE,
)
from ..nav import goto
from ..session import auth_user_dict, escape_md, logout
from ..theme import render_avatar

_REGION_NONE = "선택 안 함"
_FORM_KEY_PREFIXES = ("pe_", "pc_", "da_", "av_")

# 프로필 사진 업로드 제한 - 원본은 5MB까지만 받고, 저장은 항상 256x256
# PNG로 축소해서 한다(DB 용량·화면 로딩 모두 위해). st.file_uploader 자체
# 상한(기본 200MB)은 훨씬 커서, 여기서 따로 잘라야 큰 파일을 그냥 열어보다
# 느려지는 걸 막는다.
_AVATAR_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_AVATAR_STORE_SIZE = 256


_AVATAR_ZOOM_MIN = 1.0
_AVATAR_ZOOM_MAX = 2.5


def _process_avatar_upload(raw: bytes, *, zoom: float = 1.0) -> bytes | None:
    """업로드 파일을 정사각형으로 잘라 축소한 PNG 바이트로 바꾼다.

    이미지가 아니거나(확장자만 이미지인 위장 파일 포함) 손상돼 열 수 없으면
    ``None`` - 검증 안 된 바이트를 그대로 저장하지 않는다. ``img.load()`` 를
    직접 불러 지연 디코딩을 이 자리에서 강제한다(안 그러면 나중에 화면에
    그릴 때가 돼서야 깨진 파일이라는 게 드러난다).

    ``zoom``(1.0~2.5)은 가운데를 중심으로 얼마나 좁혀 자를지 - 1.0이면 짧은
    변 그대로(원본 대비 최대 범위), 값이 커질수록 중앙을 더 좁게 잘라 사진이
    원 안에 더 크게(확대되어) 들어간다. 위치 이동(팬)은 없고 확대만 - 중앙
    기준 확대/축소만으로도 "사진이 얼마나 크게 들어갈지" 요구는 충족하고,
    드래그로 위치까지 옮기는 크롭퍼는 Streamlit 기본 위젯만으로는 못 만든다
    (캔버스 드래그가 필요해 커스텀 컴포넌트 없이는 범위 밖).
    """

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError):
        return None

    img = img.convert("RGB")
    width, height = img.size
    side = min(width, height)
    zoom = max(_AVATAR_ZOOM_MIN, min(zoom, _AVATAR_ZOOM_MAX))
    crop_side = side / zoom
    left = (width - crop_side) / 2
    top = (height - crop_side) / 2
    img = img.crop((left, top, left + crop_side, top + crop_side))
    img = img.resize((_AVATAR_STORE_SIZE, _AVATAR_STORE_SIZE), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
                         gender_sel: str, birth_date_sel: date | None,
                         interests: list[str], disability_sel: str,
                         household_labels: list[str], veteran_sel: str,
                         income_sel: str) -> None:
    region = "" if region_sel == _REGION_NONE else region_sel
    gender = GENDER_CODE_BY_LABEL_KO.get(gender_sel, "")
    birth_date = birth_date_sel.isoformat() if isinstance(birth_date_sel, date) else ""
    disability_status = DISABILITY_CODE_BY_LABEL_KO.get(disability_sel, "")
    veteran_status = VETERAN_CODE_BY_LABEL_KO.get(veteran_sel, "")
    income_bracket = INCOME_BRACKET_CODE_BY_LABEL_KO.get(income_sel, "")
    household_types = [
        HOUSEHOLD_TYPE_CODE_BY_LABEL_KO[label]
        for label in household_labels
        if label in HOUSEHOLD_TYPE_CODE_BY_LABEL_KO
    ]
    try:
        user = update_profile(username, display_name=name.strip(),
                              region=region, gender=gender, birth_date=birth_date,
                              interests=interests, disability_status=disability_status,
                              veteran_status=veteran_status, income_bracket=income_bracket,
                              household_types=household_types)
    except AuthError as exc:
        st.error(str(exc))
        return
    st.session_state.auth_user = auth_user_dict(user)
    for key in ("pe_name", "pe_region", "pe_gender", "pe_birth_date", "pe_interests",
                "pe_disability", "pe_household_types", "pe_veteran", "pe_income"):
        st.session_state.pop(key, None)
    st.toast("기본 정보를 저장했습니다.", icon=":material/check_circle:")
    st.rerun()


def _save_avatar(username: str, avatar: bytes | None) -> None:
    """프로필 사진 저장/삭제 - "기본 정보 수정" 폼과 분리된 별도 액션이다.

    확대 슬라이더가 움직일 때마다 미리보기가 바로 바뀌어야 하는데,
    ``st.form()`` 안에 넣으면 "저장" 누르기 전까진 아무것도 다시 안
    그려진다(폼은 제출 전까지 rerun을 안 한다) - 그래서 ``_avatar_upload_section``
    전체를 폼 밖에 두고, 저장도 이 함수로 즉시 처리한다.
    """

    try:
        user = update_profile(username, avatar=avatar)
    except AuthError as exc:
        st.error(str(exc))
        return
    st.session_state.auth_user = auth_user_dict(user)
    for key in ("av_upload", "av_zoom"):
        st.session_state.pop(key, None)
    st.toast(
        "프로필 사진을 저장했습니다." if avatar else "프로필 사진을 지웠습니다.",
        icon=":material/check_circle:",
    )
    st.rerun()


def _avatar_upload_section(user: dict) -> None:
    """프로필 사진 업로드 + 확대(zoom) 조절 - 업로드 직후엔 확대 슬라이더와
    실시간 미리보기를, 평소엔 현재 사진 + 삭제 버튼을 보여준다."""

    display_name = user.get("display_name", "")

    with st.container(border=True):
        st.markdown("**프로필 사진**")
        uploaded = st.file_uploader(
            "프로필 사진", type=["png", "jpg", "jpeg", "webp"],
            key="av_upload", label_visibility="collapsed",
        )

        if uploaded is not None:
            raw = uploaded.getvalue()
            if len(raw) > _AVATAR_MAX_UPLOAD_BYTES:
                st.error("프로필 사진은 5MB 이하여야 합니다.")
                return
            zoom = st.slider(
                "사진 확대", _AVATAR_ZOOM_MIN, _AVATAR_ZOOM_MAX, _AVATAR_ZOOM_MIN,
                step=0.05, key="av_zoom",
                help="원 안에 사진이 얼마나 크게 들어갈지 조절합니다.",
            )
            processed = _process_avatar_upload(raw, zoom=zoom)
            if processed is None:
                st.error("이미지 파일을 읽을 수 없습니다. 다른 파일로 다시 시도해 주세요.")
                return
            preview = st.container(horizontal=True, vertical_alignment="center")
            render_avatar(
                preview, photo=processed, name=display_name, size=72,
                key="av_upload_preview",
            )
            if preview.button("이 사진으로 저장", key="av_save", type="primary"):
                _save_avatar(user["username"], processed)
        else:
            row = st.container(horizontal=True, vertical_alignment="center")
            render_avatar(
                row, photo=user.get("avatar"), name=display_name, size=56,
                key="av_current",
            )
            if user.get("avatar"):
                if row.button("사진 삭제", key="av_remove"):
                    _save_avatar(user["username"], None)


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
    st.session_state.pop("confirm_delete_account", None)
    logout()
    st.toast("회원 탈퇴가 완료되었습니다.", icon=":material/check_circle:")
    goto("chat")


@st.dialog("정말 탈퇴하시겠어요?", width="small")
def _confirm_delete_account_dialog(user: dict) -> None:
    """"회원 탈퇴" 버튼을 눌러도 바로 삭제되지 않고 한 번 더 확인한다
    (2026-09-15, PR 리뷰 피드백 반영) - 원래는 비밀번호+동의 체크박스만
    거치면 클릭 즉시 계정이 삭제됐다(실수로 눌러도 되돌릴 방법이 없었음).
    비밀번호·동의 체크는 이미 바깥 폼(``_delete_account_form``)에서 받은
    값을 그대로 쓴다 - 여기서 다시 입력받지 않는다.
    """

    st.error(
        "계정과 저장된 정보(이름·지역·관심조건)가 **즉시 삭제되며 되돌릴 수 "
        "없습니다.** 정말 탈퇴할까요?",
        icon=":material/warning:",
    )
    col1, col2 = st.columns(2)
    if col1.button("취소", key="confirm_delete_cancel", width="stretch"):
        st.session_state.pop("confirm_delete_account", None)
        st.rerun()
    if col2.button(
        "정말 탈퇴할게요", key="confirm_delete_go", width="stretch",
        type="primary", icon=":material/delete_forever:",
    ):
        _handle_delete_account(
            user["username"],
            st.session_state.get("da_pw", ""),
            bool(st.session_state.get("da_agree")),
        )


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


def _parse_stored_birth_date(value: object) -> date | None:
    """DB에 저장된 ISO 문자열을 위젯 기본값용 ``date``로. 깨진 값은 조용히 None."""

    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _profile_edit_form(user: dict) -> None:
    with st.container(border=True):
        st.markdown("**기본 정보 수정**")
        region = user.get("region") or ""
        region_idx = ([_REGION_NONE, *SIDO_OPTIONS].index(region)
                      if region in SIDO_OPTIONS else 0)
        gender_labels = [GENDER_NONE, *GENDER_LABELS_KO.values()]
        gender_idx = gender_labels.index(GENDER_LABELS_KO[user["gender"]]) \
            if user.get("gender") in GENDER_LABELS_KO else 0
        saved_birth_date = _parse_stored_birth_date(user.get("birth_date"))
        saved_interests = [i for i in (user.get("interests") or [])
                           if i in SIGNUP_INTEREST_OPTIONS]
        disability_labels = [DISABILITY_NONE, *DISABILITY_LABELS_KO.values()]
        disability_idx = disability_labels.index(DISABILITY_LABELS_KO[user["disability_status"]]) \
            if user.get("disability_status") in DISABILITY_LABELS_KO else 0
        veteran_labels = [VETERAN_NONE, *VETERAN_LABELS_KO.values()]
        veteran_idx = veteran_labels.index(VETERAN_LABELS_KO[user["veteran_status"]]) \
            if user.get("veteran_status") in VETERAN_LABELS_KO else 0
        income_labels = [INCOME_BRACKET_NONE, *INCOME_BRACKET_LABELS_KO.values()]
        income_idx = income_labels.index(INCOME_BRACKET_LABELS_KO[user["income_bracket"]]) \
            if user.get("income_bracket") in INCOME_BRACKET_LABELS_KO else 0
        saved_household_labels = [
            HOUSEHOLD_TYPE_LABELS_KO[code] for code in (user.get("household_types") or [])
            if code in HOUSEHOLD_TYPE_LABELS_KO
        ]
        with st.form("form_profile_edit"):
            name = st.text_input("이름", key="pe_name",
                                 **_seed("pe_name", user.get("display_name", "")))
            region_sel = st.selectbox(
                "거주 지역", [_REGION_NONE, *SIDO_OPTIONS], key="pe_region",
                **({} if "pe_region" in st.session_state
                   else {"index": region_idx}),
            )
            gender_sel = st.radio(
                "성별", gender_labels, key="pe_gender", horizontal=True,
                **({} if "pe_gender" in st.session_state
                   else {"index": gender_idx}),
            )
            birth_date_sel = st.date_input(
                "생년월일", min_value=BIRTH_DATE_MIN, max_value=date.today(),
                key="pe_birth_date",
                **_seed("pe_birth_date", saved_birth_date),
            )
            interests = st.pills(
                "관심 지원조건", SIGNUP_INTEREST_OPTIONS, selection_mode="multi",
                key="pe_interests",
                **({} if "pe_interests" in st.session_state
                   else {"default": saved_interests}),
            )
            disability_sel = st.radio(
                "장애 등록 여부", disability_labels, key="pe_disability",
                horizontal=True,
                **({} if "pe_disability" in st.session_state
                   else {"index": disability_idx}),
            )
            household_labels_sel = st.pills(
                "가구 유형 (해당하는 항목 모두 선택)",
                list(HOUSEHOLD_TYPE_LABELS_KO.values()), selection_mode="multi",
                key="pe_household_types",
                **({} if "pe_household_types" in st.session_state
                   else {"default": saved_household_labels}),
            )
            veteran_sel = st.radio(
                "국가유공자/보훈대상자 여부", veteran_labels, key="pe_veteran",
                horizontal=True,
                **({} if "pe_veteran" in st.session_state
                   else {"index": veteran_idx}),
            )
            income_sel = st.selectbox(
                "소득 수준", income_labels, key="pe_income",
                **({} if "pe_income" in st.session_state
                   else {"index": income_idx}),
            )
            saved = st.form_submit_button("저장", type="primary")
        if saved:
            _handle_profile_edit(user["username"], name, region_sel,
                                 gender_sel, birth_date_sel, list(interests or []),
                                 disability_sel, list(household_labels_sel or []),
                                 veteran_sel, income_sel)


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
        st.caption("탈퇴하면 계정과 저장된 정보(이름·지역·성별·생년월일·관심조건)가 "
                   "즉시 삭제되며 되돌릴 수 없습니다.")
        with st.form("form_delete_account", clear_on_submit=False):
            pw = st.text_input("비밀번호 확인", type="password", key="da_pw")
            agree = st.checkbox("위 내용을 확인했으며 탈퇴에 동의합니다.",
                                key="da_agree")
            submitted = st.form_submit_button("회원 탈퇴", type="secondary")
        if submitted:
            # 여기서 바로 삭제하지 않고 한 번 더 확인한다
            # (_confirm_delete_account_dialog 참고). 그 전에 비밀번호부터
            # 검증한다(2026-09-15, 사용자 피드백 반영) - 순서가 반대이면
            # 비밀번호가 틀렸는데도 "정말 탈퇴하시겠어요?" 팝업이 먼저 뜨고,
            # 거기서 "정말 탈퇴할게요"를 눌러야만 비밀번호가 틀렸다는 걸
            # 알게 되어 헷갈린다. ``authenticate``는 삭제 없이 비밀번호만
            # 확인한다(``delete_account``와 달리 부작용이 없다).
            if not agree:
                st.error("탈퇴 동의에 체크해 주세요.")
            elif not pw:
                st.error("비밀번호를 입력해 주세요.")
            else:
                try:
                    authenticate(user["username"], pw)
                except InvalidCredentialsError:
                    st.error("비밀번호가 올바르지 않습니다.")
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["confirm_delete_account"] = True
                    st.rerun()


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

    # 탈퇴 확인 팝업 - chat.py의 confirm_new_chat과 같은 패턴(세션 상태
    # 플래그로 열림 여부를 표시하고, 매 rerun마다 여기서 다시 확인한다).
    if st.session_state.get("confirm_delete_account"):
        _confirm_delete_account_dialog(user)

    st.caption("마이페이지")

    display_name = user.get("display_name") or user.get("username", "")
    joined = (user.get("created_at") or "")[:10]

    with st.container(border=True):
        top = st.container(horizontal=True, vertical_alignment="center")
        render_avatar(
            top, photo=user.get("avatar"), name=display_name, size=56,
            key="mypage_card_avatar",
        )
        info = top.container()
        info.markdown(f"#### {escape_md(display_name)} 님")
        info.caption(escape_md(user.get("username", "")))
        top.badge("일반 회원", icon=":material/verified_user:", color="violet")
        st.caption(f"가입일 {joined or '-'}")

    with st.container(border=True):
        st.markdown("**내 가입 정보**")
        region = user.get("region") or "미설정"
        gender = GENDER_LABELS_KO.get(user.get("gender") or "", "미설정")
        birth_date = user.get("birth_date") or "미설정"
        interests = user.get("interests") or []
        marketing = "동의" if user.get("marketing_opt_in") else "미동의"
        disability = DISABILITY_LABELS_KO.get(user.get("disability_status") or "", "미설정")
        veteran = VETERAN_LABELS_KO.get(user.get("veteran_status") or "", "미설정")
        income = INCOME_BRACKET_LABELS_KO.get(user.get("income_bracket") or "", "미설정")
        household_types = [
            HOUSEHOLD_TYPE_LABELS_KO[code] for code in (user.get("household_types") or [])
            if code in HOUSEHOLD_TYPE_LABELS_KO
        ]
        rows = [
            ("거주 지역", region), ("성별", gender), ("생년월일", birth_date),
            ("장애 등록 여부", disability), ("보훈대상자 여부", veteran),
            ("소득 수준", income), ("마케팅 수신", marketing),
        ]
        cols = st.columns(2)
        for i, (label, value) in enumerate(rows):
            box = cols[i % 2].container(border=True)
            box.caption(label)
            box.markdown(f"**{value}**")
        st.caption("가구 유형")
        if household_types:
            st.markdown(" ".join(f":violet-badge[{escape_md(x)}]" for x in household_types))
        else:
            st.markdown("**미설정**")
        st.caption("관심 지원조건")
        if interests:
            st.markdown(" ".join(f":blue-badge[{escape_md(x)}]" for x in interests))
        else:
            st.markdown("**미설정**")

    _avatar_upload_section(user)
    _profile_edit_form(user)
    _password_form(user)
    _delete_account_form(user)

    actions = st.container(horizontal=True)
    if actions.button("로그아웃", icon=":material/logout:", key="mp_logout"):
        logout()
        goto("chat")
    if actions.button("상담으로 돌아가기", icon=":material/arrow_back:", key="mp_back",
                      type="secondary"):
        goto("chat")
