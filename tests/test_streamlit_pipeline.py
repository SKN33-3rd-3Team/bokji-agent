from __future__ import annotations

from streamlit_ui import pipeline


def test_first_prompt_uses_official_ask(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_ask(user_input: str, session_id: str, *, top_k: int, extra_interests=None):
        calls.append(("ask", user_input, session_id, top_k, extra_interests))
        return {"status": "needs_input", "question": "추가 정보"}

    monkeypatch.setattr(pipeline, "ask", fake_ask)
    response = pipeline.run_pipeline(
        user_input="질문",
        session_id="session-a",
        awaiting_followup=False,
        top_k=7,
    )

    assert response["status"] == "needs_input"
    assert calls == [("ask", "질문", "session-a", 7, None)]


def test_first_prompt_forwards_sidebar_interests(monkeypatch) -> None:
    """사이드바에서 고른 지원조건·관심 분야가 ask 까지 전달되는지."""

    calls: list[tuple] = []

    def fake_ask(user_input: str, session_id: str, *, top_k: int, extra_interests=None):
        calls.append(("ask", extra_interests))
        return {"status": "answered"}

    monkeypatch.setattr(pipeline, "ask", fake_ask)
    pipeline.run_pipeline(
        user_input="질문",
        session_id="session-a",
        awaiting_followup=False,
        top_k=5,
        extra_interests=["청년", "주거"],
    )

    assert calls == [("ask", ["청년", "주거"])]


def test_followup_does_not_forward_interests(monkeypatch) -> None:
    """재개 시점에는 체크포인터의 슬롯이 확정돼 있으므로 초기 슬롯을 다시
    끼워 넣지 않는다 - answer_followup 은 이 인자를 받지 않는다."""

    calls: list[tuple] = []

    def fake_followup(session_id: str, user_input: str):
        calls.append((session_id, user_input))
        return {"status": "answered"}

    monkeypatch.setattr(pipeline, "answer_followup", fake_followup)
    pipeline.run_pipeline(
        user_input="서울입니다",
        session_id="session-a",
        awaiting_followup=True,
        top_k=5,
        extra_interests=["청년"],
    )

    assert calls == [("session-a", "서울입니다")]


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
