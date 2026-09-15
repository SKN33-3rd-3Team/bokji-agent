"""로그인 직후 진입하는 마이페이지 정보 기반 정책 자동 검색 화면(``home.py``).

PR #55 리뷰 후속조치: "회원가입 때 입력한 지역·성별·생년월일을 미리 채워두고,
그 조건을 기반으로 정책을 자동 검색해서 보여주는 기본 화면"이 실제로 그렇게
동작하는지 검증한다. ``chat.py``의 ``run_pipeline`` 모킹 패턴(test_streamlit_chat.py)
과 같은 방식으로 ``home.ask``/``home.answer_followup``을 통째로 갈아끼운다.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from streamlit_ui.pages import home as home_module

# home 모듈 전역을 갈아끼우는 스크립트가 있어서, chat.py 테스트와 같은 이유로
# 매 테스트 후 원래대로 되돌린다(test_streamlit_chat.py 상단 주석 참고).
_PATCHED_HOME_GLOBALS = ("ask", "answer_followup", "VECTOR_DB_DIR")


@pytest.fixture(autouse=True)
def _restore_home_module_globals():
    saved = {name: getattr(home_module, name) for name in _PATCHED_HOME_GLOBALS}
    yield
    for name, value in saved.items():
        setattr(home_module, name, value)


_AUTH_USER = {
    "username": "user@example.com",
    "display_name": "김복지",
    "region": "부산광역시",
    "gender": "female",
    "birth_date": "1997-03-05",
    "interests": ["장애인"],
    "disability_status": "registered",
    "veteran_status": "registered",
    "income_bracket": "under_30",
    "household_types": ["single_parent", "newlywed"],
    "marketing_opt_in": False,
}

_ANSWERED_RESULT = {
    "status": "answered",
    "answer_status": "complete",
    "final_answer": "확인한 정책입니다.",
    "final_citations": [],
    "policies": [{"policy_id": "p1", "title": "청년월세지원"}],
    "llm_status": {"enabled": False},
}


def _home_app(data_dir, *, auth_user: dict | None = _AUTH_USER,
              fake_ask_body: str, fake_followup_body: str = "") -> AppTest:
    script = f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import home

st.session_state.auth_user = {auth_user!r}
init_session()
home.VECTOR_DB_DIR = Path({str(data_dir)!r})

{fake_ask_body}
home.ask = fake_ask
{fake_followup_body}
home.page_home()
'''
    return AppTest.from_string(script).run(timeout=10)


def _vector_db_dir(tmp_path):
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    return data_dir


def test_home_requires_login() -> None:
    app = _home_app(
        object(),  # VECTOR_DB_DIR 값은 로그인 체크가 먼저라 상관없음
        auth_user=None,
        fake_ask_body="def fake_ask(*a, **k):\n    raise AssertionError('로그인 없이 호출됨')",
    )
    assert any("로그인이 필요한 화면" in str(item.value) for item in app.info)
    assert any(b.label == "로그인하러 가기" for b in app.button)


def test_home_missing_prebuilt_database_stops_before_service_call(tmp_path) -> None:
    missing = tmp_path / "missing-vector-db"
    app = _home_app(
        missing,
        fake_ask_body="def fake_ask(*a, **k):\n    raise AssertionError('DB 없이 호출됨')",
    )
    assert any("준비되지 않았습니다" in str(item.value) for item in app.error)


def test_home_runs_initial_search_with_signup_profile_and_renders_result(tmp_path) -> None:
    """마이페이지(회원가입) 정보가 ``known_*`` 인자로 그대로 전달되고, 반환된
    정책이 화면에 그려진다."""

    data_dir = _vector_db_dir(tmp_path)
    app = _home_app(
        data_dir,
        fake_ask_body=f'''\
def fake_ask(user_input, session_id, *, top_k=5, extra_interests=None,
              known_region=None, known_gender=None, known_birth_date=None,
              known_disability_status=None, known_income_bracket=None,
              known_household_types=None, known_veteran_status=None):
    st.session_state["_ask_calls"] = st.session_state.get("_ask_calls", []) + [dict(
        known_region=known_region, known_gender=known_gender,
        known_birth_date=known_birth_date,
        known_disability_status=known_disability_status,
        known_income_bracket=known_income_bracket,
        known_household_types=known_household_types,
        known_veteran_status=known_veteran_status,
    )]
    return {_ANSWERED_RESULT!r}
''',
    )

    calls = app.session_state["_ask_calls"]
    assert len(calls) == 1
    assert calls[0] == {
        "known_region": "부산광역시",
        "known_gender": "female",
        "known_birth_date": "1997-03-05",
        "known_disability_status": "registered",
        "known_income_bracket": "under_30",
        "known_household_types": ["single_parent", "newlywed"],
        "known_veteran_status": "registered",
    }
    assert any("확인한 정책입니다." in str(item.value) for item in app.markdown)
    assert any("확인한 정책 1건" in str(item.value) for item in app.markdown)


def test_home_needs_input_flow_uses_answer_followup(tmp_path) -> None:
    """하드 게이트 슬롯이 부족하면 되묻고, 답변 제출 시 ``answer_followup``으로
    재개해 최종 결과를 보여준다."""

    data_dir = _vector_db_dir(tmp_path)
    app = _home_app(
        data_dir,
        fake_ask_body='''\
def fake_ask(*a, **k):
    return {"status": "needs_input", "question": "취업 상태를 알려주세요.",
            "missing_slots": ["employment_status"]}
''',
        fake_followup_body=f'''\
def fake_answer_followup(session_id, answer):
    st.session_state["_followup_calls"] = st.session_state.get(
        "_followup_calls", []) + [(session_id, answer)]
    return {_ANSWERED_RESULT!r}

home.answer_followup = fake_answer_followup
''',
    )

    assert any("취업 상태를 알려주세요." in str(item.value) for item in app.info)
    app.text_input(key="home_followup_answer").set_value("무직입니다")
    app = app.run(timeout=10)
    next(b for b in app.button if b.label == "답변 제출").click()
    app = app.run(timeout=10)

    assert app.session_state["_followup_calls"][-1][1] == "무직입니다"
    assert any("확인한 정책입니다." in str(item.value) for item in app.markdown)


def test_home_sidebar_back_button_returns_to_chat(tmp_path) -> None:
    data_dir = _vector_db_dir(tmp_path)
    app = _home_app(
        data_dir,
        fake_ask_body=f"def fake_ask(*a, **k):\n    return {_ANSWERED_RESULT!r}",
    )

    back = next(b for b in app.sidebar.button if "상담으로 돌아가기" in b.label)
    back.click()
    app = app.run(timeout=10)
    assert app.session_state["view"] == "chat"


def test_home_retry_button_reruns_search_with_new_session(tmp_path) -> None:
    data_dir = _vector_db_dir(tmp_path)
    app = _home_app(
        data_dir,
        fake_ask_body=f'''\
def fake_ask(user_input, session_id, **kwargs):
    st.session_state["_ask_calls"] = st.session_state.get("_ask_calls", []) + [session_id]
    return {_ANSWERED_RESULT!r}
''',
    )
    first_session_id = app.session_state["home_session_id"]
    assert len(app.session_state["_ask_calls"]) == 1

    next(b for b in app.button if b.label == "다시 검색").click()
    app = app.run(timeout=10)

    assert len(app.session_state["_ask_calls"]) == 2
    assert app.session_state["home_session_id"] != first_session_id
    assert app.session_state["_ask_calls"][-1] == app.session_state["home_session_id"]
