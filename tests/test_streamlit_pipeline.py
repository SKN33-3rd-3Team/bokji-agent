from __future__ import annotations

from streamlit_ui import pipeline


def test_first_prompt_uses_official_ask(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_ask(user_input: str, session_id: str, *, top_k: int):
        calls.append(("ask", user_input, session_id, top_k))
        return {"status": "needs_input", "question": "추가 정보"}

    monkeypatch.setattr(pipeline, "ask", fake_ask)
    response = pipeline.run_pipeline(
        user_input="질문",
        session_id="session-a",
        awaiting_followup=False,
        top_k=7,
    )

    assert response["status"] == "needs_input"
    assert calls == [("ask", "질문", "session-a", 7)]


def test_n3_reply_uses_answer_followup_with_same_session(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_followup(session_id: str, user_input: str):
        calls.append(("answer_followup", session_id, user_input))
        return {"status": "answered", "answer_status": "complete"}

    monkeypatch.setattr(pipeline, "answer_followup", fake_followup)
    response = pipeline.run_pipeline(
        user_input="서울입니다",
        session_id="session-a",
        awaiting_followup=True,
        top_k=7,
    )

    assert response["status"] == "answered"
    assert calls == [("answer_followup", "session-a", "서울입니다")]
