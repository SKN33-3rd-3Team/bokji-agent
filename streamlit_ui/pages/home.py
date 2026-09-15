"""로그인 직후 진입 화면 — 마이페이지 정보 기반 정책 자동 검색 (PR #55 후속).

PR #55의 원래 목표("회원가입 때 저장한 지역·성별·생년월일을 미리 채워두고,
그 조건을 기반으로 정책을 자동 검색해서 보여주는 기본 화면")를 완성하는
최소 동작 버전이다. 리뷰 요청대로 화면 디자인은 다루지 않는다(다른 화면으로
교체될 예정) - `service.ask()`를 호출해 정책 리스트를 보여주는 것까지만
한다. 사이드바에는 상담 화면으로 돌아가는 버튼 하나만 둔다.

로그인 성공 시 ``streamlit_ui/pages/auth.py``의 ``_handle_login()``이 이
화면(``"home"`` 뷰)으로 이동시킨다.

메인 채팅(``chat.py``)과 별도의 LangGraph 세션(``home_session_id``)을 써서
진행 중인 상담의 체크포인터 상태를 건드리지 않는다. 최초 진입 시 한 번만
``ask()``를 호출하고 결과를 세션에 캐시해 재실행을 막는다(그래프 실행은
비용이 크다).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

import streamlit as st

from src.rag_chatbot.service import answer_followup, ask

from ..constants import VECTOR_DB_DIR
from ..nav import goto
from ..rendering import render_result

_LOG = logging.getLogger("bokji.streamlit")
_GENERIC_ERROR_MESSAGE = (
    "정책 검색 중 오류가 발생했습니다. 잠시 후 다시 시도하거나 상담 화면을 이용해 주세요."
)

_SESSION_KEY = "home_session_id"
_RESULT_KEY = "home_result"
_ANSWER_WIDGET_KEY = "home_followup_answer"
# 회원가입 정보만으로 첫 검색을 시작하기 위한 고정 질의. 사용자가 직접 입력한
# 문장이 아니므로 이 화면에서는 사용자 원문 대신 이 문구가 정책 검색 질의에
# 쓰인다(policy_search._build_query가 interests + 이 질문을 합쳐 검색한다).
_INITIAL_QUERY = "제가 받을 수 있는 지원 제도를 찾아주세요."


def _known(auth_user: Mapping[str, object], key: str):
    """``auth_user`` 세션 dict에서 ``known_*`` 인자로 넘길 값을 뽑는다.

    빈 문자열/빈 리스트는 "회원가입 때 입력 안 함"이므로 ``None``으로
    바꾼다 - ``ask()``가 빈 값을 받으면 그대로 무시하지만, 의도를 명확히
    하기 위해 여기서 정리한다.
    """

    value = auth_user.get(key)
    if isinstance(value, list):
        return list(value) or None
    return value or None


def _reset_search() -> None:
    st.session_state[_SESSION_KEY] = str(uuid.uuid4())
    st.session_state.pop(_RESULT_KEY, None)
    st.session_state.pop(_ANSWER_WIDGET_KEY, None)


def _run_initial_search(auth_user: Mapping[str, object]) -> None:
    with st.spinner("마이페이지 정보로 맞춤 지원 제도를 찾고 있어요..."):
        try:
            st.session_state[_RESULT_KEY] = ask(
                _INITIAL_QUERY,
                st.session_state[_SESSION_KEY],
                known_region=_known(auth_user, "region"),
                known_gender=_known(auth_user, "gender"),
                known_birth_date=_known(auth_user, "birth_date"),
                known_disability_status=_known(auth_user, "disability_status"),
                known_income_bracket=_known(auth_user, "income_bracket"),
                known_household_types=_known(auth_user, "household_types"),
                known_veteran_status=_known(auth_user, "veteran_status"),
            )
        except SystemExit:
            _LOG.exception("서비스 실행 설정 오류 (home)")
            st.session_state[_RESULT_KEY] = None
        except Exception:  # 서비스 내부 정보나 traceback은 화면에 노출하지 않는다.
            _LOG.exception("마이페이지 기반 정책 자동 검색 실패")
            st.session_state[_RESULT_KEY] = None


def _render_needs_input(result: Mapping[str, object]) -> None:
    """하드 게이트 슬롯이 부족해 되묻는 상태 - 취업상태처럼 회원가입에서
    수집하지 않는 슬롯이 있으면 여기로 온다. chat.py의 인터럽트 처리를
    한 슬롯씩 채우는 최소 형태로 단순화했다."""

    st.info(str(result.get("question") or "추가 정보가 필요합니다."))
    answer = st.text_input("답변", key=_ANSWER_WIDGET_KEY)
    if st.button("답변 제출", key="home_followup_submit", type="primary") and answer.strip():
        try:
            st.session_state[_RESULT_KEY] = answer_followup(
                st.session_state[_SESSION_KEY], answer.strip()
            )
        except SystemExit:
            # get_store()/get_graph()가 설정 오류(EMBEDDING_PROVIDER 등)로
            # SystemExit을 던질 수 있다 - _run_initial_search와 같은 이유로
            # Exception만 잡으면 놓친다(SystemExit은 BaseException 계열).
            _LOG.exception("서비스 실행 설정 오류 (home 재개)")
            st.error(_GENERIC_ERROR_MESSAGE, icon=":material/error:")
            return
        except Exception:  # noqa: BLE001 - 서비스 내부 정보는 화면에 노출하지 않는다
            _LOG.exception("마이페이지 기반 정책 자동 검색 재개 실패")
            st.error(_GENERIC_ERROR_MESSAGE, icon=":material/error:")
            return
        st.session_state.pop(_ANSWER_WIDGET_KEY, None)
        st.rerun()


def page_home() -> None:
    auth_user = st.session_state.get("auth_user")
    if not auth_user:
        st.info("로그인이 필요한 화면입니다.")
        if st.button("로그인하러 가기", icon=":material/login:", key="home_need_login"):
            goto("login")
        return

    with st.sidebar:
        if st.button(
            "상담으로 돌아가기",
            icon=":material/arrow_back:",
            key="home_back_to_chat",
            width="stretch",
            type="secondary",
        ):
            goto("chat")

    st.caption(
        "마이페이지에 저장된 정보로 맞춤 지원 제도를 자동으로 찾아드려요. "
        "(베타 · 화면은 추후 교체될 예정입니다)"
    )

    if not (VECTOR_DB_DIR / "chroma.sqlite3").is_file():
        st.error(
            "서비스 데이터베이스가 준비되지 않았습니다. 관리자에게 문의해 주세요.",
            icon=":material/database_off:",
        )
        return

    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = str(uuid.uuid4())
    if _RESULT_KEY not in st.session_state:
        _run_initial_search(auth_user)

    if st.button("다시 검색", icon=":material/refresh:", key="home_retry"):
        _reset_search()
        st.rerun()

    result = st.session_state.get(_RESULT_KEY)
    if not isinstance(result, Mapping):
        st.error(_GENERIC_ERROR_MESSAGE, icon=":material/error:")
        return

    if result.get("status") == "needs_input":
        _render_needs_input(result)
        return

    render_result(result)
