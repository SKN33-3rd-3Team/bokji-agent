from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _chat_app(data_dir) -> AppTest:
    script = f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def fake_run_pipeline(*, user_input, session_id, awaiting_followup, top_k):
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

    assert any("김복지 님으로 로그인됨" in str(item.value) for item in app.caption)
    assert not any(button.label == "로그인" for button in app.button)
    next(button for button in app.button if button.label == "대화 초기화").click()
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
