"""정책 상세 문의 채팅방 모달(D/E) - chat.py 라우팅 통합 테스트.

``tests/test_streamlit_chat.py``의 ``AppTest.from_string`` 패턴을 따른다.
경량 응답 로직 자체(B/C)는 ``tests/test_light_followup.py``가 단위로 덮으므로
여기서는 배선만 확인한다:
- 정책 카드 버튼 → ``st.dialog`` 모달 채팅방이 열림
- 그 입력 → 무거운 N1~N14가 아니라 경량 경로로, 메인 대화(``messages``)를
  건드리지 않고 ``detail_chat_history``에만 쌓임
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from src.rag_chatbot import light_followup as light_module
from src.rag_chatbot import service as service_module
from streamlit_ui.pages import chat as chat_module

_PATCH_TARGETS = (
    (chat_module, "run_pipeline"),
    (chat_module, "render_result"),
    (chat_module, "VECTOR_DB_DIR"),
    (service_module, "get_llm_client"),
    (light_module, "respond_to_policy_question"),
)


@pytest.fixture(autouse=True)
def _restore_patched_globals():
    saved = [(mod, name, getattr(mod, name)) for mod, name in _PATCH_TARGETS]
    yield
    for mod, name, value in saved:
        setattr(mod, name, value)


def _prebuilt_db(tmp_path):
    data_dir = tmp_path / "vector_db"
    data_dir.mkdir()
    (data_dir / "chroma.sqlite3").touch()
    return data_dir


_POLICY = {
    "policy_id": "P1",
    "title": "청년월세지원",
    "eligibility_status": "충족",
    "eligibility_reasons": ["만 19~34세 청년"],
    "detail": {
        "support_details": "월 최대 20만원을 최대 12개월 지원한다.",
        "application_method": "복지로에서 온라인 신청한다.",
    },
}

# f-string 스크립트에 ``{_SEED_AUTH}``로 치환돼 들어가므로 중괄호는 하나만.
_SEED_AUTH = (
    'import streamlit as _stg\n'
    'if "_auth_seeded" not in _stg.session_state:\n'
    '    _stg.session_state["_auth_seeded"] = True\n'
    '    _stg.session_state["auth_user"] = '
    '{"username": "u@example.com", "display_name": "테스터"}\n'
)


def _answered_with_policy_app(tmp_path) -> AppTest:
    """첫 질문에 정책 1건이 담긴 answered 응답을 주는 앱."""

    data_dir = _prebuilt_db(tmp_path)
    script = f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat

init_session()
{_SEED_AUTH}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def fake_run_pipeline(*, user_input, session_id, awaiting_followup, top_k, extra_interests=None):
    return {{
        "status": "answered",
        "answer_status": "complete",
        "final_answer": "청년월세지원을 받을 수 있어요.",
        "final_citations": [],
        "policies": [{_POLICY!r}],
        "llm_status": {{"enabled": False}},
    }}

chat.run_pipeline = fake_run_pipeline
chat.page_chat()
'''
    return AppTest.from_string(script).run(timeout=10)


def _detail_chat_script(data_dir, *, light_result: dict | None) -> str:
    if light_result is None:
        respond_body = 'raise RuntimeError("secret-light-traceback")'
    else:
        respond_body = (
            'st.session_state.setdefault("_respond_calls", []).append('
            '{"policy_id": policy.get("policy_id"), "question": question, '
            '"llm_client": llm_client, "user_profile": user_profile})\n'
            f'    return {light_result!r}'
        )
    return f'''\
from pathlib import Path
import streamlit as st
from streamlit_ui.session import init_session
from streamlit_ui.pages import chat
from src.rag_chatbot import light_followup, service

init_session()
{_SEED_AUTH}
chat.VECTOR_DB_DIR = Path({str(data_dir)!r})

def _boom(**kwargs):
    raise AssertionError("run_pipeline이 상세 채팅 턴에서 호출됐다")

chat.run_pipeline = _boom
service.get_llm_client = lambda: "fake-llm-client"

def fake_respond(policy, question, *, llm_client, user_profile=None):
    {respond_body}

light_followup.respond_to_policy_question = fake_respond
chat.page_chat()
'''


def _detail_chat_app(tmp_path, *, light_result: dict | None) -> AppTest:
    app = AppTest.from_string(
        _detail_chat_script(_prebuilt_db(tmp_path), light_result=light_result)
    ).run(timeout=10)
    app.session_state["detail_chat_policy"] = dict(_POLICY)
    return app.run(timeout=10)


def _detail_input(app):
    return next(c for c in app.chat_input if c.key == "detail_chat_input")


def test_clicking_ask_button_opens_chat_dialog(tmp_path) -> None:
    app = _answered_with_policy_app(tmp_path)
    app.chat_input[0].set_value("월세 지원 되나요").run(timeout=10)

    ask = next(b for b in app.button if b.label == "이 정책에 대해 물어보기")
    app = ask.click().run(timeout=10)

    assert app.session_state["detail_chat_policy"]["policy_id"] == "P1"
    # 모달 채팅방에 그 정책 헤더 + 전용 입력창이 뜬다.
    assert any("청년월세지원" in str(m.value) for m in app.markdown)
    assert any("전용 채팅방" in str(c.value) for c in app.caption)
    assert any(c.key == "detail_chat_input" for c in app.chat_input)
    assert "청년월세지원" in _detail_input(app).placeholder


def test_close_button_closes_chat_dialog(tmp_path) -> None:
    app = _detail_chat_app(
        tmp_path,
        light_result={"kind": "guidance", "text": "안내", "evidence_quotes": []},
    )
    assert "detail_chat_policy" in app.session_state

    close = next(b for b in app.button if b.label == "닫기")
    app = close.click().run(timeout=10)

    assert "detail_chat_policy" not in app.session_state
    assert "detail_chat_history" not in app.session_state
    assert not any(c.key == "detail_chat_input" for c in app.chat_input)


def test_detail_chat_question_routes_to_light_followup_not_pipeline(tmp_path) -> None:
    app = _detail_chat_app(
        tmp_path,
        light_result={
            "kind": "answer",
            "text": "월 최대 20만원을 지원해요.",
            "evidence_quotes": ["월 최대 20만원을 최대 12개월 지원한다."],
        },
    )
    first_id = app.session_state["conversation_id"]

    _detail_input(app).set_value("월세 얼마 나와요").run(timeout=10)

    calls = app.session_state["_respond_calls"]
    assert len(calls) == 1
    assert calls[0]["question"] == "월세 얼마 나와요"
    assert calls[0]["policy_id"] == "P1"
    assert calls[0]["llm_client"] == "fake-llm-client"

    assert not app.error
    assert app.session_state["conversation_id"] == first_id
    assert app.session_state["awaiting_followup"] is False
    assert "detail_chat_policy" in app.session_state
    # 메인 대화는 손대지 않는다.
    assert app.session_state["messages"] == []

    history = app.session_state["detail_chat_history"]
    assert history[-2] == {"role": "user", "content": "월세 얼마 나와요"}
    assert history[-1]["role"] == "assistant"
    assert history[-1]["light_answer"]["kind"] == "answer"


def test_detail_chat_passes_session_profile_to_light_followup(tmp_path) -> None:
    app = _detail_chat_app(
        tmp_path,
        light_result={"kind": "guidance", "text": "안내", "evidence_quotes": []},
    )
    app.session_state["profile"] = [
        {"key": "region", "label": "지역", "value": "서울특별시"},
        {"key": "age", "label": "나이", "value": "만 27세"},
    ]
    app = app.run(timeout=10)

    _detail_input(app).set_value("우리 지역도 되나요").run(timeout=10)

    call = app.session_state["_respond_calls"][0]
    assert call["user_profile"] == [
        {"key": "region", "label": "지역", "value": "서울특별시"},
        {"key": "age", "label": "나이", "value": "만 27세"},
    ]


def test_detail_chat_renders_answer_with_evidence(tmp_path) -> None:
    app = _detail_chat_app(
        tmp_path,
        light_result={
            "kind": "answer",
            "text": "월 최대 20만원을 지원해요.",
            "evidence_quotes": ["월 최대 20만원을 최대 12개월 지원한다."],
        },
    )
    _detail_input(app).set_value("월세 얼마 나와요").run(timeout=10)

    shown = " ".join(str(m.value) for m in app.markdown)
    assert "월 최대 20만원을 지원해요." in shown
    # "보충자료" expander 안의 발췌도 함께 그려진다.
    assert "월 최대 20만원을 최대 12개월 지원한다." in shown
    assert not any("월 최대 20만원을 지원해요." in str(i.value) for i in app.info)


def test_detail_chat_renders_guidance_as_info(tmp_path) -> None:
    guidance_text = (
        "이 채팅은 '청년월세지원' 정책에 대한 질문만 답할 수 있어요. "
        "다른 정책이나 새로운 검색은 메인 화면에서 다시 물어봐 주세요."
    )
    app = _detail_chat_app(
        tmp_path,
        light_result={"kind": "guidance", "text": guidance_text, "evidence_quotes": []},
    )
    _detail_input(app).set_value("옆 동네 지원도 알려줘").run(timeout=10)

    assert any(guidance_text in str(item.value) for item in app.info)
    history = app.session_state["detail_chat_history"]
    assert history[-1]["light_answer"]["kind"] == "guidance"


def test_detail_chat_llm_failure_is_redacted(tmp_path) -> None:
    """respond_to_policy_question이 터져도 내부 정보 없이 안내로 폴백한다."""

    app = _detail_chat_app(tmp_path, light_result=None)
    _detail_input(app).set_value("질문").run(timeout=10)

    visible = " ".join(str(item.value) for item in [*app.info, *app.error])
    assert "secret-light-traceback" not in visible
    history = app.session_state["detail_chat_history"]
    assert history[-1]["light_answer"]["kind"] == "guidance"
