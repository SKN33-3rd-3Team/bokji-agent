from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from streamlit_ui.pages import chat as chat_module

# AppTest 스크립트가 ``chat.render_result``/``chat.run_pipeline`` 같은 모듈
# 전역을 갈아끼우는데, ``streamlit_ui.pages.chat`` 은 이 프로세스에서 한 번만
# import 되므로 그 교체가 **다음 테스트까지 그대로 남는다**. 실제로 렌더링을
# 일부러 실패시키는 테스트 뒤에 오는 테스트들이 그 가짜 render_result 를
# 물려받아 엉뚱하게 깨졌다. 매 테스트 후 원래대로 되돌린다.
_PATCHED_CHAT_GLOBALS = ("render_result", "run_pipeline", "VECTOR_DB_DIR")


@pytest.fixture(autouse=True)
def _restore_chat_module_globals():
    saved = {name: getattr(chat_module, name) for name in _PATCHED_CHAT_GLOBALS}
    yield
    for name, value in saved.items():
        setattr(chat_module, name, value)


def _values(elements) -> list[str]:
    return [str(element.value) for element in elements]


def _chat_app(data_dir) -> AppTest:
    script = f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
import streamlit as _stg
if "_auth_seeded" not in _stg.session_state:
    _stg.session_state["_auth_seeded"] = True
    _stg.session_state["auth_user"] = {{"username": "u@example.com", "display_name": "테스터"}}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def fake_run_pipeline(*, user_input, session_id, awaiting_followup, top_k, extra_interests=None):
    if awaiting_followup:
        return {{
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "최종 답변",
            "final_citations": [],
            "policies": [],
            "llm_status": {{"enabled": False}},
        }}
    return {{
        "status": "needs_input",
        "question": "거주 지역을 알려주세요.",
        "missing_slots": ["region"],
        "llm_status": {{"enabled": False}},
    }}

chat.run_pipeline = fake_run_pipeline
chat.page_chat()
'''
    return AppTest.from_string(script).run(timeout=10)


def test_followup_keeps_id_then_answer_rotates_to_empty_session(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _chat_app(data_dir)
    first_id = app.session_state["conversation_id"]

    app.chat_input[0].set_value("첫 질문").run(timeout=10)
    assert app.session_state["conversation_id"] == first_id
    assert app.session_state["awaiting_followup"] is True

    app.chat_input[0].set_value("서울입니다").run(timeout=10)
    assert app.session_state["conversation_id"] != first_id
    assert app.session_state["awaiting_followup"] is False
    assert len(app.session_state["messages"]) == 4


def test_authenticated_sidebar_reset_and_logout_clear_conversation(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _chat_app(data_dir)
    app.session_state["auth_user"] = {
        "username": "user@example.com",
        "display_name": "김복지",
    }
    app.session_state["messages"] = [{"role": "user", "content": "상담 내용"}]
    app.session_state["awaiting_followup"] = True
    app.session_state["pending_prompt"] = "추가 답변"
    app = app.run(timeout=10)
    first_id = app.session_state["conversation_id"]

    assert any("김복지 님" in str(item.value) for item in app.caption)
    assert not any(button.label == "로그인" for button in app.button)
    next(button for button in app.button if button.label == "새 상담 시작").click()
    app = app.run(timeout=10)

    assert app.session_state["auth_user"]["username"] == "user@example.com"
    assert app.session_state["conversation_id"] != first_id
    assert app.session_state["awaiting_followup"] is False
    assert (
        "pending_prompt" not in app.session_state
        or app.session_state["pending_prompt"] is None
    )
    assert app.session_state["messages"] == []

    reset_id = app.session_state["conversation_id"]
    app.session_state["messages"] = [{"role": "user", "content": "새 상담"}]
    app.session_state["awaiting_followup"] = True
    app = app.run(timeout=10)
    next(button for button in app.button if button.label == "로그아웃").click()
    app = app.run(timeout=10)

    assert app.session_state["auth_user"] is None
    assert app.session_state["conversation_id"] != reset_id
    assert app.session_state["awaiting_followup"] is False
    assert app.session_state["messages"] == []


def test_missing_prebuilt_database_stops_before_service_call(tmp_path) -> None:
    missing = tmp_path / "missing-vector-db"
    app = _chat_app(missing)

    assert any("데이터베이스가 준비되지 않았습니다" in str(item.value) for item in app.error)
    assert len(app.chat_input) == 0


def test_empty_prebuilt_database_directory_stops_before_service_call(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    app = _chat_app(data_dir)

    assert any("데이터베이스가 준비되지 않았습니다" in str(item.value) for item in app.error)
    assert len(app.chat_input) == 0


def test_runtime_exception_is_redacted_from_user(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    script = f'''\
from pathlib import Path
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
import streamlit as _stg
if "_auth_seeded" not in _stg.session_state:
    _stg.session_state["_auth_seeded"] = True
    _stg.session_state["auth_user"] = {{"username": "u@example.com", "display_name": "테스터"}}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def fail(**kwargs):
    raise RuntimeError("secret-internal-traceback")

chat.run_pipeline = fail
chat.page_chat()
'''
    app = AppTest.from_string(script).run(timeout=10)
    app.chat_input[0].set_value("질문").run(timeout=10)

    visible_errors = " ".join(str(item.value) for item in app.error)
    assert "상담 처리 중 오류" in visible_errors
    assert "secret-internal-traceback" not in visible_errors


def test_fresh_result_render_exception_is_redacted_from_user(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    script = f'''\
from pathlib import Path
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
import streamlit as _stg
if "_auth_seeded" not in _stg.session_state:
    _stg.session_state["_auth_seeded"] = True
    _stg.session_state["auth_user"] = {{"username": "u@example.com", "display_name": "테스터"}}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})
chat.run_pipeline = lambda **kwargs: {{"status": "answered"}}

def fail(result):
    raise RuntimeError("secret-fresh-render")

chat.render_result = fail
chat.page_chat()
'''
    app = AppTest.from_string(script).run(timeout=10)
    first_id = app.session_state["conversation_id"]
    app.chat_input[0].set_value("질문").run(timeout=10)

    visible_errors = " ".join(str(item.value) for item in app.error)
    assert "상담 처리 중 오류" in visible_errors
    assert "secret-fresh-render" not in visible_errors
    assert app.session_state["messages"][-1]["result"]["status"] == "answered"
    assert app.session_state["conversation_id"] != first_id
    assert app.session_state["awaiting_followup"] is False


def test_fresh_result_render_exception_preserves_followup_transition(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    script = f'''\
from pathlib import Path
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
import streamlit as _stg
if "_auth_seeded" not in _stg.session_state:
    _stg.session_state["_auth_seeded"] = True
    _stg.session_state["auth_user"] = {{"username": "u@example.com", "display_name": "테스터"}}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})
chat.run_pipeline = lambda **kwargs: {{"status": "needs_input", "question": "지역?"}}

def fail(result):
    raise RuntimeError("secret-followup-render")

chat.render_result = fail
chat.page_chat()
'''
    app = AppTest.from_string(script).run(timeout=10)
    first_id = app.session_state["conversation_id"]
    app.chat_input[0].set_value("질문").run(timeout=10)

    visible_errors = " ".join(str(item.value) for item in app.error)
    assert "상담 처리 중 오류" in visible_errors
    assert "secret-followup-render" not in visible_errors
    assert app.session_state["messages"][-1]["result"]["status"] == "needs_input"
    assert app.session_state["conversation_id"] == first_id
    assert app.session_state["awaiting_followup"] is True


def test_history_result_render_exception_is_redacted_from_user(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    script = f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
import streamlit as _stg
if "_auth_seeded" not in _stg.session_state:
    _stg.session_state["_auth_seeded"] = True
    _stg.session_state["auth_user"] = {{"username": "u@example.com", "display_name": "테스터"}}
st.session_state.messages = [
    {{"role": "assistant", "result": {{"status": "answered"}}}}
]
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def fail(result):
    raise RuntimeError("secret-history-render")

chat.render_result = fail
chat.page_chat()
'''
    app = AppTest.from_string(script).run(timeout=10)

    visible_errors = " ".join(str(item.value) for item in app.error)
    assert "상담 처리 중 오류" in visible_errors
    assert "secret-history-render" not in visible_errors


# ── 사이드바 "파악한 정보" ──────────────────────────────────────────


def _profile_chat_app(data_dir) -> AppTest:
    """needs_input -> answered 두 턴 모두 output_json["profile"]을 주는 앱."""

    script = f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
import streamlit as _stg
if "_auth_seeded" not in _stg.session_state:
    _stg.session_state["_auth_seeded"] = True
    _stg.session_state["auth_user"] = {{"username": "u@example.com", "display_name": "테스터"}}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def fake_run_pipeline(*, user_input, session_id, awaiting_followup, top_k, extra_interests=None):
    if awaiting_followup:
        return {{
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "최종 답변",
            "final_citations": [],
            "policies": [],
            "output_json": {{"status": "answered", "profile": [
                {{"key": "region", "label": "지역", "value": "서울특별시"}},
                {{"key": "age", "label": "나이", "value": "만 5세"}},
            ]}},
        }}
    return {{
        "status": "needs_input",
        "question": "생년월일을 알려주세요.",
        "missing_slots": ["birth_date"],
        "output_json": {{"status": "needs_input", "profile": [
            {{"key": "region", "label": "지역", "value": "서울특별시"}},
        ]}},
    }}

chat.run_pipeline = fake_run_pipeline
chat.page_chat()
'''
    return AppTest.from_string(script).run(timeout=10)


def _sidebar_markdown(app) -> str:
    return " ".join(str(element.value) for element in app.sidebar.markdown)


def test_profile_sidebar_fills_during_followup_and_survives_the_answer(tmp_path) -> None:
    """답변이 나오면 new_conversation()이 세션을 새로 여는데, 그때 "파악한
    정보"까지 사라지면 사용자는 무슨 정보로 판단했는지 알 수 없다."""

    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _profile_chat_app(data_dir)

    assert "파악한 정보" not in _sidebar_markdown(app)

    app.chat_input[0].set_value("서울 살아요").run(timeout=10)
    assert app.session_state["profile"] == [
        {"key": "region", "label": "지역", "value": "서울특별시"}
    ]
    sidebar = _sidebar_markdown(app)
    assert "파악한 정보" in sidebar
    assert "지역: 서울특별시" in sidebar

    app.chat_input[0].set_value("2021년 3월 5일생이요").run(timeout=10)
    # 답변 뒤 new_conversation()이 돌아도 profile은 남는다.
    assert app.session_state["awaiting_followup"] is False
    sidebar = _sidebar_markdown(app)
    assert "나이: 만 5세" in sidebar


def test_reset_conversation_clears_profile_sidebar(tmp_path) -> None:
    """공용 PC에서 대화를 초기화하면 소득·장애 등이 담긴 파악한 정보도
    반드시 함께 지워져야 한다."""

    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _profile_chat_app(data_dir)
    app.chat_input[0].set_value("서울 살아요").run(timeout=10)
    assert app.session_state["profile"]

    reset = next(b for b in app.sidebar.button if "새 상담 시작" in b.label)
    app = reset.click().run(timeout=10)

    assert app.session_state["profile"] == []
    assert "파악한 정보" not in _sidebar_markdown(app)


def test_profile_sidebar_keeps_previous_value_when_response_has_no_profile(tmp_path) -> None:
    """output_json 자체가 없는 예전 계약 응답에서도 깨지지 않는다."""

    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _chat_app(data_dir)
    app.session_state["profile"] = [
        {"key": "region", "label": "지역", "value": "부산광역시"}
    ]
    app = app.run(timeout=10)

    app.chat_input[0].set_value("첫 질문").run(timeout=10)
    assert app.session_state["profile"] == [
        {"key": "region", "label": "지역", "value": "부산광역시"}
    ]
    assert "지역: 부산광역시" in _sidebar_markdown(app)


# ── 사이드바 지원조건 · 관심 분야 ───────────────────────────────────


def _interests_chat_app(data_dir) -> AppTest:
    """run_pipeline 이 받은 extra_interests 를 session_state 에 기록하는 앱."""

    script = f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
import streamlit as _stg
if "_auth_seeded" not in _stg.session_state:
    _stg.session_state["_auth_seeded"] = True
    _stg.session_state["auth_user"] = {{"username": "u@example.com", "display_name": "테스터"}}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def fake_run_pipeline(*, user_input, session_id, awaiting_followup, top_k, extra_interests=None):
    # run_pipeline 은 워커 스레드에서 돌기 때문에 여기서 st.session_state 를
    # 건드릴 수 없다(ScriptRunContext 없음). 받은 값을 응답에 실어 보낸다.
    return {{
        "status": "answered",
        "answer_status": "complete",
        "final_answer": f"선택={{extra_interests!r}} top_k={{top_k!r}}",
        "final_citations": [],
        "policies": [],
    }}

chat.run_pipeline = fake_run_pipeline
chat.page_chat()
'''
    return AppTest.from_string(script).run(timeout=10)


def test_sidebar_condition_and_field_pickers_reach_the_pipeline(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _interests_chat_app(data_dir)

    labels = [element.label for element in app.sidebar.multiselect]
    assert labels == ["지원조건", "관심 분야"]

    app.session_state["interests_pick"] = ["청년"]
    app.session_state["fields_pick"] = ["주거"]
    app = app.run(timeout=10)
    app.chat_input[0].set_value("지원 뭐 받을 수 있나요").run(timeout=10)

    assert "선택=['청년', '주거']" in " ".join(_values(app.markdown))


def test_sidebar_pickers_deduplicate_overlapping_choices(tmp_path) -> None:
    """"장애인"처럼 두 목록에 다 있는 값을 양쪽에서 고르면 한 번만 보낸다."""

    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _interests_chat_app(data_dir)

    app.session_state["interests_pick"] = ["장애인", "청년"]
    app.session_state["fields_pick"] = ["장애인", "돌봄"]
    app = app.run(timeout=10)
    app.chat_input[0].set_value("질문").run(timeout=10)

    assert "선택=['장애인', '청년', '돌봄']" in " ".join(_values(app.markdown))


def test_sidebar_pickers_send_empty_list_when_nothing_selected(tmp_path) -> None:
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _interests_chat_app(data_dir)

    app.chat_input[0].set_value("질문").run(timeout=10)

    assert "선택=[]" in " ".join(_values(app.markdown))


def test_sidebar_top_k_slider_reaches_the_pipeline(tmp_path) -> None:
    """"정책 후보 수" 슬라이더가 실제로 파이프라인까지 가는지."""

    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _interests_chat_app(data_dir)

    assert [slider.label for slider in app.sidebar.slider] == ["정책 후보 수"]
    app.sidebar.slider[0].set_value(12).run(timeout=10)
    app.chat_input[0].set_value("질문").run(timeout=10)

    assert "top_k=12" in " ".join(_values(app.markdown))


def test_profile_sidebar_replaces_tilde_with_hyphen(tmp_path) -> None:
    """소득 라벨의 "중위소득 30~50%"가 Markdown 취소선으로 읽히지 않아야."""

    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    app = _chat_app(data_dir)
    app.session_state["profile"] = [
        {"key": "income_bracket", "label": "소득", "value": "차상위 수준(중위소득 30~50%)"}
    ]
    app = app.run(timeout=10)

    sidebar = _sidebar_markdown(app)
    assert "중위소득 30-50%" in sidebar
    assert "~" not in sidebar
