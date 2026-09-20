"""API14: actual graph/checkpoint with isolated authentication and external I/O."""

from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Timer
from time import monotonic

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from backend.app.api.deps import get_current_user
from backend.app.services import auth_adapter, chat_adapter
from backend.app.core.errors import ApiError
from backend.app.session_store.chat_session import chat_session_store
from src.rag_chatbot import service
from src.rag_chatbot.graph.builder import build_graph
from src.rag_chatbot.llm import FailingLLMClient
from tests.test_auto_recommendation import policy_store


@pytest.fixture
def auto_graph(monkeypatch):
    store = Mock(search=Mock(return_value=[]), get_chunks_by_metadata=Mock(return_value=[]))
    graph = build_graph(store)
    monkeypatch.setattr(service, "_runtime_cache", {"graph": graph, "store": store, "llm_client": None})
    return graph, store


def signup(client, **profile):
    result = client.post("/api/v1/auth/signup", json={
        "email": "auto@example.com", "password": "StrongPass1!", "password_confirm": "StrongPass1!",
        "name": "테스트", "terms_agreed": True, "privacy_agreed": True, **profile,
    })
    assert result.status_code == 201, result.text
    return result.json()


def test_auto_requires_auth_and_no_store(client):
    response = client.post("/api/v1/chat/recommendations")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert response.headers["cache-control"] == "no-store"


def test_missing_profile_actual_graph_no_questions_and_normal_zero(client, auto_graph):
    signup(client)
    response = client.post("/api/v1/chat/recommendations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert body["status"] == "answered" and body["question"] is None
    assert body["missing_slots"] == body["calc_missing_slots"] == body["calc_missing_choices"] == []
    assert body["interrupt_id"] is None and body["calc_slot_inputs"] == []
    assert body["slot_conflicts"] is None
    assert body["final_answer"] == body["output_text"] == body["output_markdown"] == "현재 정보로 추천할 정책이 없습니다"
    assert body["policies"] == body["final_citations"] == []
    assert body["output_json"]["summary"] == {"checked": 0, "eligible": 0, "not_eligible_or_unknown": 0}
    graph, store = auto_graph
    snapshot = graph.get_state({"configurable": {"thread_id": body["session_id"]}})
    assert not snapshot.next and snapshot.values["automatic_recommendation"] is True
    slots = snapshot.values["slots"]
    assert slots.get("employment_status") in (None, "unknown")
    assert not slots.get("region_names") and not slots.get("children_count")
    assert body["output_json"]["profile"] == []
    assert chat_session_store._sessions[body["session_id"]].last_policies == []
    assert store.search.call_args_list[0].kwargs["search_filter"].metadata_equals == {"region_scope": "national"}


@pytest.mark.parametrize("kwargs", [{"json": {}}, {"content": "null"}, {"params": {"user_id": "1"}}])
def test_auto_rejects_any_input_before_graph(client, auto_graph, kwargs):
    signup(client)
    response = client.post("/api/v1/chat/recommendations", **kwargs)
    assert response.status_code == 400 and response.json()["code"] == "VALIDATION_ERROR"
    assert response.headers["cache-control"] == "no-store"
    auto_graph[1].search.assert_not_called()
    assert not chat_session_store._sessions


def test_server_profile_cache_and_next_http_turn(client, monkeypatch):
    signup(client, birth_date="2000-01-01", gender="female", income_bracket="under_30",
           disability_status="not_registered", veteran_status="not_registered")
    assert client.patch("/api/v1/users/me", json={"gender": "male"}).status_code == 200
    store = policy_store({"id": "eligible", "amount": 0}, {"id": "unknown-age", "age_start": 60})
    graph = build_graph(store)
    monkeypatch.setattr(service, "_runtime_cache", {"graph": graph, "store": store, "llm_client": None})
    response = client.post("/api/v1/chat/recommendations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["policy_id"] for p in body["policies"]] == ["eligible"]
    assert body["policies"][0]["amount"] == 0
    sid = body["session_id"]
    cached = chat_session_store._sessions[sid]
    assert cached.last_policies == body["policies"]
    assert cached.last_profile == body["output_json"]["profile"]
    slots = graph.get_state({"configurable": {"thread_id": sid}}).values["slots"]
    assert slots["birth_date"] == "2000-01-01" and slots["gender"] == "male"
    assert slots["veteran_status"] == "not_registered" and slots.get("employment_status") is None
    second = client.post(f"/api/v1/chat/sessions/{sid}/followup", json={"message": "새 주거 질문"})
    assert second.status_code == 200 and second.json()["status"] == "needs_input"
    fresh = graph.get_state({"configurable": {"thread_id": sid}}).values
    assert fresh["automatic_recommendation"] is False and fresh["initial_user_input"] == "새 주거 질문"
    assert "employment_status" in second.json()["missing_slots"]


@pytest.mark.parametrize("failure,code,status", [
    ("provider", "GRAPH_EXECUTION_ERROR", 500), ("timeout", "GRAPH_EXECUTION_ERROR", 500),
    ("vector", "VECTOR_STORE_UNAVAILABLE", 503), ("detail", "GRAPH_EXECUTION_ERROR", 500),
    ("database", "AUTH_BACKEND_UNAVAILABLE", 503),
])
def test_errors_never_zero_and_failed_start_cleans_actual_graph(client, monkeypatch, failure, code, status):
    from rag_design.vector_store import ChromaUnavailableError
    from src.rag_chatbot import timing
    signup(client)
    store = policy_store({"id": "p"})
    release = Event()
    entered = Event()
    finished = Event()
    class SlowProvider:
        def complete(self, *args, **kwargs):
            entered.set()
            try:
                assert release.wait(3)
                return "{}"
            finally:
                finished.set()
    provider = SlowProvider() if failure == "timeout" else FailingLLMClient() if failure == "provider" else None
    graph = build_graph(store, llm_client=provider)
    monkeypatch.setattr(service, "_runtime_cache", {"graph": graph, "store": store, "llm_client": None})
    if failure == "timeout":
        monkeypatch.setattr(timing, "LLM_NODE_TIMEOUT_SECONDS", 0.08)
    elif failure == "vector":
        store.search = Mock(side_effect=ChromaUnavailableError("offline failure"))
    elif failure == "detail":
        monkeypatch.setattr(service, "_fetch_policy_detail", Mock(side_effect=ValueError("late composition")))
    elif failure == "database":
        monkeypatch.setattr(auth_adapter, "get_profile", Mock(side_effect=ApiError(503, code, "DB unavailable")))
    safety_release = Timer(0.7, release.set)
    safety_release.start()
    try:
        start = monotonic()
        response = client.post("/api/v1/chat/recommendations")
        elapsed = monotonic() - start
        assert (response.status_code, response.json()["code"]) == (status, code), response.text
        assert response.headers["cache-control"] == "no-store"
        assert not chat_session_store._sessions and not list(graph.checkpointer.list(None))
        if failure == "timeout":
            assert entered.is_set() and elapsed < 0.5 and not release.is_set()
    finally:
        release.set()
        safety_release.cancel()
        if failure == "timeout":
            assert finished.wait(3)
            assert not list(graph.checkpointer.list(None))


def test_preauthenticated_auto_waiter_cannot_outlive_withdrawal(client, auto_graph, monkeypatch):
    signup(client)
    entered, release = Event(), Event()
    stale = TestClient(client.app, cookies=client.cookies)
    def delayed(request: Request):
        current = get_current_user(request)
        if request.url.path.endswith("/recommendations"):
            entered.set()
            assert release.wait(5)
        return current
    monkeypatch.setitem(client.app.dependency_overrides, get_current_user, delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(stale.post, "/api/v1/chat/recommendations")
        try:
            assert entered.wait(5)
            assert client.request("DELETE", "/api/v1/users/me", json={"password": "StrongPass1!"}).status_code == 200
            signup(client)
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
    assert not chat_session_store._sessions and not list(auto_graph[0].checkpointer.list(None))


def test_running_auto_finishes_before_withdrawal_then_all_state_is_deleted(client, auto_graph, monkeypatch):
    signup(client)
    entered, release = Event(), Event()
    original = chat_adapter.ask
    def pause(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(chat_adapter, "ask", pause)
    from backend.app.session_store.auth_session import auth_session_store
    user_id = auth_session_store.get(client.cookies.get("session_id")).user_id
    from backend.tests.test_account_chat_cleanup import ObservedLock
    lock = ObservedLock()
    chat_session_store._user_locks[user_id] = lock
    chat_client = TestClient(client.app, cookies=client.cookies)
    delete_client = TestClient(client.app, cookies=client.cookies)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(chat_client.post, "/api/v1/chat/recommendations")
        try:
            assert entered.wait(5)
            deleting = pool.submit(delete_client.request, "DELETE", "/api/v1/users/me", json={"password": "StrongPass1!"})
            assert lock.waiting.wait(5) and not deleting.done()
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
        assert deleting.result(timeout=5).status_code == 200
    assert not chat_session_store._sessions and not list(auto_graph[0].checkpointer.list(None))
