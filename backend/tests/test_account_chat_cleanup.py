"""회원 탈퇴와 상담 요청의 메모리 수명·경합을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.app.api.deps import get_current_user
from backend.app.services import auth_adapter, chat_adapter
from backend.app.session_store.auth_session import auth_session_store
from backend.app.session_store.chat_session import chat_session_store
from backend.tests.test_chat_session_lifecycle import ObservedLock
from src.rag_chatbot.auth.service import AuthBackendUnavailableError


def signup(client, email):
    response = client.post("/api/v1/auth/signup", json={
        "email": email, "name": "Tester", "password": "Passw0rd!123",
        "password_confirm": "Passw0rd!123", "terms_agreed": True, "privacy_agreed": True,
    })
    assert response.status_code == 201
    return response.json()["user"]["id"]


def withdraw(client, password="Passw0rd!123"):
    return client.request("DELETE", "/api/v1/users/me", json={"password": password})


def start(client):
    response = client.post("/api/v1/chat/messages", json={"message": "question"})
    assert response.status_code == 200
    return response.json()["session_id"]


def checkpoints(graph, session_id):
    return list(graph.checkpointer.list({"configurable": {"thread_id": session_id}}))


@pytest.fixture
def accounts(client, monkeypatch):
    builder = StateGraph(dict)
    builder.add_node("step", lambda state: state)
    builder.add_edge(START, "step")
    builder.add_edge("step", END)
    graph = builder.compile(checkpointer=MemorySaver())

    def answer(session_id, message):
        graph.invoke({"message": message}, config={"configurable": {"thread_id": session_id}})
        return {
            "session_id": session_id, "status": "answered",
            "policies": [{"policy_id": "P1", "title": "cached policy"}],
            "output_json": {"profile": [{"key": "region", "value": "서울"}]},
        }

    def ask(message, session_id, **kwargs):
        kwargs["_on_graph_ready"](graph)
        return answer(session_id, message)

    monkeypatch.setattr(chat_adapter, "ask", ask)
    monkeypatch.setattr(chat_adapter, "answer_followup", answer)
    monkeypatch.setattr(chat_adapter, "get_graph", lambda: graph)
    owner_id = signup(client, "owner@example.com")
    other = TestClient(client.app)
    other_id = signup(other, "other@example.com")
    owned = [start(client), start(client)]
    other_session = start(other)
    return SimpleNamespace(
        owner=client, owner_id=owner_id, owned=owned, graph=graph,
        other=other, other_id=other_id, other_session=other_session,
    )


@pytest.mark.parametrize("failure", [None, "lookup", "checkpoint"])
def test_withdrawal_clears_only_owner_chats_even_if_cleanup_fails(accounts, monkeypatch, failure):
    a = accounts
    records = [chat_session_store.get(sid, user_id=a.owner_id) for sid in a.owned]
    assert all(record.last_profile and record.last_policies for record in records)
    other_record = chat_session_store.get(a.other_session, user_id=a.other_id)
    other_checkpoints = checkpoints(a.graph, a.other_session)
    old_token = a.owner.cookies.get("session_id")
    auth_session_store.create("second-login", user_id=a.owner_id, username="owner@example.com")
    cleanup = Mock(side_effect=SystemExit("unavailable") if failure == "lookup" else RuntimeError("cleanup"))
    if failure == "lookup":
        monkeypatch.setattr(chat_adapter, "get_graph", cleanup)
    elif failure == "checkpoint":
        monkeypatch.setattr(a.graph.checkpointer, "delete_thread", cleanup)

    response = withdraw(a.owner)
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert all(chat_session_store.get(sid, user_id=a.owner_id) is None for sid in a.owned)
    assert auth_session_store.get(old_token) is None
    assert auth_session_store.get("second-login") is None
    assert chat_session_store.get(a.other_session, user_id=a.other_id) is other_record
    assert other_record.last_profile and other_record.last_policies
    assert checkpoints(a.graph, a.other_session) == other_checkpoints
    if failure:
        assert cleanup.call_count == len(a.owned)
    else:
        assert all(not checkpoints(a.graph, sid) for sid in a.owned)
    a.owner.cookies.set("session_id", old_token)
    assert a.owner.post("/api/v1/chat/messages", json={"message": "late"}).status_code == 401
    a.owner.cookies.clear()
    replacement_id = signup(a.owner, "owner@example.com")
    assert replacement_id != a.owner_id
    assert chat_session_store.get(a.owned[0], user_id=replacement_id) is None
    assert a.owner.post(f"/api/v1/chat/sessions/{a.owned[0]}/followup", json={"message": "late"}).status_code == 404
    assert a.owner.delete(f"/api/v1/chat/sessions/{a.other_session}").status_code == 200
    assert chat_session_store.get(a.other_session, user_id=a.other_id) is other_record


@pytest.mark.parametrize("failure", ["password", "database"])
def test_failed_withdrawal_preserves_chats_and_login(accounts, monkeypatch, failure):
    a = accounts
    before = dict(chat_session_store._sessions)
    saved = {sid: checkpoints(a.graph, sid) for sid in a.owned}
    token = a.owner.cookies.get("session_id")
    if failure == "database":
        monkeypatch.setattr(auth_adapter.auth_service, "delete_account", Mock(
            side_effect=AuthBackendUnavailableError("unavailable"),
        ))
    response = withdraw(a.owner, "wrong" if failure == "password" else "Passw0rd!123")
    assert (response.status_code, response.json()["code"]) == (
        (401, "INVALID_CREDENTIALS") if failure == "password" else (503, "AUTH_BACKEND_UNAVAILABLE")
    )
    assert "set-cookie" not in response.headers
    assert auth_session_store.get(token) is not None
    assert chat_session_store._sessions == before
    assert all(checkpoints(a.graph, sid) == saved[sid] for sid in a.owned)
    assert a.owner.get("/api/v1/users/me").status_code == 200
    assert start(a.owner) not in a.owned  # 실패한 탈퇴가 회원 잠금을 붙잡지 않는다.


@pytest.mark.parametrize("operation", ["start", "resume"])
def test_already_authenticated_chat_cannot_run_after_withdrawal(accounts, monkeypatch, operation):
    a = accounts
    authenticated, release = Event(), Event()
    stale_client = TestClient(a.owner.app, cookies=a.owner.cookies)

    def delayed_auth(request: Request):
        current = get_current_user(request)
        if request.url.path.startswith("/api/v1/chat/"):
            authenticated.set()
            assert release.wait(5)
        return current

    monkeypatch.setitem(a.owner.app.dependency_overrides, get_current_user, delayed_auth)
    ask = Mock(wraps=chat_adapter.ask)
    resume = Mock(wraps=chat_adapter.answer_followup)
    monkeypatch.setattr(chat_adapter, "ask", ask)
    monkeypatch.setattr(chat_adapter, "answer_followup", resume)
    path = "/api/v1/chat/messages" if operation == "start" else f"/api/v1/chat/sessions/{a.owned[0]}/followup"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(stale_client.post, path, json={"message": "late"})
        try:
            assert authenticated.wait(5)
            assert withdraw(a.owner).status_code == 200
            assert signup(a.owner, "owner@example.com") != a.owner_id
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert (response.status_code, response.json().get("code")) == (401, "UNAUTHORIZED")
    ask.assert_not_called()
    resume.assert_not_called()
    assert all(record.user_id != a.owner_id for record in chat_session_store._sessions.values())
    assert all(not checkpoints(a.graph, sid) for sid in a.owned)


@pytest.mark.parametrize("operation", ["start", "resume"])
def test_withdrawal_waits_for_running_chat_without_blocking_other_users(accounts, monkeypatch, operation):
    a = accounts
    lock = ObservedLock()
    chat_session_store._user_locks[a.owner_id] = lock
    entered, release = Event(), Event()
    written = []
    token = a.owner.cookies.get("session_id")
    chat_client = TestClient(a.owner.app, cookies=a.owner.cookies)
    withdrawal_client = TestClient(a.owner.app, cookies=a.owner.cookies)
    original_ask = chat_adapter.ask
    original_resume = chat_adapter.answer_followup

    def pause(session_id, response):
        assert checkpoints(a.graph, session_id)
        written.append(session_id)
        entered.set()
        assert release.wait(5)
        return response

    def ask(message, session_id, **kwargs):
        response = original_ask(message, session_id, **kwargs)
        return pause(session_id, response) if message == "inflight" else response

    def resume(session_id, message):
        return pause(session_id, original_resume(session_id, message))

    monkeypatch.setattr(chat_adapter, "ask", ask)
    monkeypatch.setattr(chat_adapter, "answer_followup", resume)
    path = "/api/v1/chat/messages" if operation == "start" else f"/api/v1/chat/sessions/{a.owned[0]}/followup"
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending_chat = pool.submit(chat_client.post, path, json={"message": "inflight"})
        try:
            assert entered.wait(5)
            if operation == "start":
                assert chat_session_store.get(written[0], user_id=a.owner_id) is None
            pending_withdrawal = pool.submit(withdraw, withdrawal_client)
            assert lock.waiting.wait(5)
            assert not pending_withdrawal.done()
            assert auth_session_store.get(token) is not None
            independent = pool.submit(start, a.other).result(timeout=5)
            assert chat_session_store.get(independent, user_id=a.other_id) is not None
        finally:
            release.set()
        assert pending_chat.result(timeout=5).status_code == 200
        assert pending_withdrawal.result(timeout=5).status_code == 200
    assert all(record.user_id != a.owner_id for record in chat_session_store._sessions.values())
    assert all(not checkpoints(a.graph, sid) for sid in a.owned + written)
    assert auth_session_store.get(token) is None
    # 실행/대기 중인 작업만 잠금을 소유하며 완료된 회원 ID는 따로 남지 않는다.
    del lock
    assert not chat_session_store._user_locks
