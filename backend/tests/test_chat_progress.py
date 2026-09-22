"""진행률 조회 API + 구동 워밍업 상태 API 계약.

프론트 진행 막대(frontend/src/components/chat/ChatProgressBar.tsx)가 기대하는
것만 확인한다: (1) 도는 동안 읽으면 지금 단계가 나온다, (2) 남의 기록은 못
읽는다, (3) 기록이 없어도 오류가 아니라 unknown이다.
"""

from __future__ import annotations

import threading

import pytest

from src.rag_chatbot.progress import PROGRESS
from backend.app.services import chat_adapter

_SIGNUP_PAYLOAD = {
    "password": "Passw0rd!123",
    "password_confirm": "Passw0rd!123",
    "name": "Progress User",
    "terms_agreed": True,
    "privacy_agreed": True,
}


def _signup(client, email: str):
    return client.post("/api/v1/auth/signup", json={**_SIGNUP_PAYLOAD, "email": email})


@pytest.fixture(autouse=True)
def _clean_progress():
    PROGRESS._records.clear()  # noqa: SLF001 - 프로세스 전역 싱글턴이라 테스트 간 격리
    PROGRESS._aliases.clear()  # noqa: SLF001
    yield
    PROGRESS._records.clear()  # noqa: SLF001
    PROGRESS._aliases.clear()  # noqa: SLF001


def test_progress_requires_login(client):
    assert client.get("/api/v1/chat/progress/whatever").status_code == 401


def test_unknown_token_is_not_an_error(client):
    _signup(client, "progress-unknown@example.com")
    r = client.get("/api/v1/chat/progress/nope")
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


@pytest.mark.parametrize("endpoint", ["messages", "recommendations"])
def test_progress_reports_current_step_while_the_graph_runs(client, monkeypatch, endpoint):
    """상담이 도는 **중간에** 진행률을 읽을 수 있어야 한다.

    user_operation의 회원별 잠금을 진행률 조회가 같이 기다리면 상담이 끝날
    때까지 한 번도 못 읽는다 - 그러면 진행 막대가 의미가 없다. 그래서 그래프
    실행을 붙잡아 둔 상태로 조회해 본다.
    """

    _signup(client, "progress-live@example.com")
    entered = threading.Event()
    release = threading.Event()

    from langgraph.graph import END, START, StateGraph
    from src.rag_chatbot.graph.state import GraphState
    from src.rag_chatbot.timing import timed_node

    def searching(state):
        entered.set()
        assert release.wait(timeout=10)
        return {}

    builder = StateGraph(GraphState)
    builder.add_node("prepare", timed_node("slot_parser", lambda state: {}))
    builder.add_node("search", timed_node("policy_search", searching))
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "search")
    builder.add_edge("search", END)
    graph = builder.compile()

    def slow_ask(message, session_id, **kwargs):
        # 실제 LangGraph 노드 계측이 API 조회 레지스트리에 도달해야 한다.
        graph.invoke({"query_id": session_id})
        return {
            "status": "answered",
            "session_id": session_id,
            "answer_status": "complete",
            "final_answer": "안내입니다.",
            "final_citations": [],
            "policies": [],
            "output_json": {},
            "output_text": "",
            "output_markdown": "",
            "llm_status": {},
            "timing": {},
        }

    monkeypatch.setattr(chat_adapter, "ask", slow_ask)

    result: dict = {}

    def send():
        result["response"] = client.post(
            f"/api/v1/chat/{endpoint}",
            headers={"X-Progress-Token": "tok-1"},
            **({"json": {"message": "안녕하세요"}} if endpoint == "messages" else {}),
        )

    worker = threading.Thread(target=send)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        body = client.get("/api/v1/chat/progress/tok-1").json()
        assert body["status"] == "running"
        assert body["completed_steps"] == 1
        assert body["message"] == "받을 수 있는 지원 제도를 찾고 있어요"
        assert 0 < body["fraction"] <= 0.95
    finally:
        release.set()
        worker.join(timeout=5)

    assert result["response"].status_code == 200
    assert client.get("/api/v1/chat/progress/tok-1").json()["status"] == "done"


def test_progress_is_not_readable_by_another_member(client, monkeypatch):
    _signup(client, "progress-owner@example.com")
    monkeypatch.setattr(
        chat_adapter,
        "ask",
        lambda message, session_id, **kwargs: {
            "status": "answered",
            "session_id": session_id,
            "answer_status": "complete",
            "final_answer": "안내입니다.",
            "final_citations": [],
            "policies": [],
            "output_json": {},
            "output_text": "",
            "output_markdown": "",
            "llm_status": {},
            "timing": {},
        },
    )
    assert client.post(
        "/api/v1/chat/messages",
        json={"message": "안녕하세요"},
        headers={"X-Progress-Token": "tok-owner"},
    ).status_code == 200
    assert client.get("/api/v1/chat/progress/tok-owner").json()["status"] == "done"

    client.post("/api/v1/auth/logout")
    _signup(client, "progress-other@example.com")
    # 남의 토큰은 "없음"으로만 보인다(존재 여부조차 알려주지 않는다).
    assert client.get("/api/v1/chat/progress/tok-owner").json()["status"] == "unknown"


def test_progress_token_length_is_capped(client):
    _signup(client, "progress-long@example.com")
    r = client.post(
        "/api/v1/chat/messages",
        json={"message": "안녕하세요"},
        headers={"X-Progress-Token": "x" * 200},
    )
    assert r.status_code == 400


def test_server_status_is_public_and_reports_warmup(client, monkeypatch):
    from src.rag_chatbot import service

    monkeypatch.setattr(
        service, "warmup_state", lambda: {"status": "running", "message": "준비 중", "seconds": None}
    )
    r = client.get("/api/v1/config/status")
    assert r.status_code == 200
    assert r.json()["status"] == "running"
