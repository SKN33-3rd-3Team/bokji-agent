"""세션별 재개/삭제 순서와 예외 시 잠금 해제를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock
import weakref

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.app.core.errors import ApiError
from backend.app.schemas.chat import ChatRequest
from backend.app.services import chat_adapter
from rag_design.vector_store import ChromaUnavailableError
from src.rag_chatbot import service


@pytest.fixture
def memory_graph(monkeypatch):
    """실제 LangGraph 실행과 메모리 체크포인트만 사용하며 외부 I/O는 없다."""
    def step(state):
        if state["user_input"] == "interrupt":
            interrupt("지역이 어디신가요?")
        return state

    builder = StateGraph(dict)
    builder.add_node("step", step)
    builder.add_edge(START, "step")
    builder.add_edge("step", END)
    graph = builder.compile(checkpointer=MemorySaver())
    monkeypatch.setattr(service, "_runtime_cache", {
        "graph": graph, "store": object(), "llm_client": None,
    })
    monkeypatch.setattr(chat_adapter.uuid, "uuid4", lambda: "new-session")
    return graph


@pytest.mark.parametrize("phase,status,code", [
    ("graph", 500, "GRAPH_EXECUTION_ERROR"),
    ("compose", 503, "VECTOR_STORE_UNAVAILABLE"),
    ("schema", 500, "INTERNAL_ERROR"),
    ("ownership", 500, "INTERNAL_ERROR"),
    ("cache", 500, "INTERNAL_ERROR"),
])
def test_failed_start_removes_actual_checkpoint_and_partial_owner(
    client, monkeypatch, memory_graph, phase, status, code,
):
    graph = memory_graph
    config = {"configurable": {"thread_id": "new-session", "checkpoint_ns": ""}}
    other_config = {"configurable": {"thread_id": "other-session"}}
    graph.invoke({"user_input": "other"}, config=other_config)
    store = chat_adapter.chat_session_store
    store.create("other-session", user_id=99)
    other_owner = store.get("other-session", user_id=99)
    other_checkpoints = list(graph.checkpointer.list(other_config))
    # 실패 사이 캐시가 바뀌어도 실제 실행한 그래프만 정리해야 한다.
    unrelated_saver = MemorySaver()
    for saved in graph.checkpointer.list(other_config):
        unrelated_saver.put(config, saved.checkpoint, saved.metadata, saved.checkpoint["channel_versions"])
    unrelated_checkpoints = list(unrelated_saver.list(config))

    def late_failure():
        assert list(graph.checkpointer.list(config)), "failure must follow real checkpoint writes"
        monkeypatch.setattr(service, "_runtime_cache", {
            "graph": SimpleNamespace(checkpointer=unrelated_saver),
            "store": object(), "llm_client": None,
        })

    original_run = service.run_graph
    original_response = service._to_chat_response
    original_create = store.create

    def run(*args, **kwargs):
        result = original_run(*args, **kwargs)
        if phase == "graph":
            late_failure()
            raise RuntimeError("late graph failure")
        return result

    def compose(*args, **kwargs):
        response = original_response(*args, **kwargs)
        late_failure()
        if phase == "compose":
            raise ChromaUnavailableError("late detail lookup failure")
        if phase == "schema":
            response["status"] = "invalid"
        return response

    def create(*args, **kwargs):
        original_create(*args, **kwargs)
        raise RuntimeError("failed after ownership write")

    monkeypatch.setattr(service, "run_graph", run)
    monkeypatch.setattr(service, "_to_chat_response", compose)
    if phase == "ownership":
        monkeypatch.setattr(store, "create", create)
    if phase == "cache":
        monkeypatch.setattr(chat_adapter, "_cache_last_response", Mock(side_effect=RuntimeError("cache")))
    get_graph = Mock(side_effect=AssertionError("cleanup must not look up a graph"))
    monkeypatch.setattr(chat_adapter, "get_graph", get_graph)
    signup = client.post("/api/v1/auth/signup", json={
        "email": "late-failure@example.com", "name": "Tester",
        "password": "Passw0rd!123", "password_confirm": "Passw0rd!123",
        "terms_agreed": True, "privacy_agreed": True,
    })
    assert signup.status_code == 201
    http = TestClient(client.app, cookies=client.cookies, raise_server_exceptions=False)
    response = http.post("/api/v1/chat/messages", json={"message": "question"})
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert list(graph.checkpointer.list(config)) == []
    assert store.get("new-session", user_id=signup.json()["user"]["id"]) is None
    assert store.get("other-session", user_id=99) is other_owner
    assert list(graph.checkpointer.list(other_config)) == other_checkpoints
    assert list(unrelated_saver.list(config)) == unrelated_checkpoints
    get_graph.assert_not_called()


@pytest.mark.parametrize("cleanup", ["checkpoint", "ownership"])
def test_failed_start_cleanup_error_preserves_original(monkeypatch, memory_graph, cleanup):
    store = chat_adapter.chat_session_store
    original = ApiError(503, "ORIGINAL_ERROR", "original")
    monkeypatch.setattr(chat_adapter, "_cache_last_response", Mock(side_effect=original))
    target = memory_graph.checkpointer if cleanup == "checkpoint" else store
    method = "delete_thread" if cleanup == "checkpoint" else "delete"
    failed_cleanup = Mock(side_effect=RuntimeError("cleanup failure"))
    monkeypatch.setattr(target, method, failed_cleanup)
    with pytest.raises(ApiError) as caught:
        chat_adapter.start_chat(ChatRequest(message="question"), user_id=1)
    assert caught.value is original
    failed_cleanup.assert_called_once_with("new-session")
    if cleanup == "checkpoint":
        assert store.get("new-session", user_id=1) is None
    else:
        assert list(memory_graph.checkpointer.list({"configurable": {"thread_id": "new-session"}})) == []


@pytest.mark.parametrize("phase", ["graph", "store"])
def test_failed_start_before_graph_writes_does_not_initialize_for_cleanup(
    monkeypatch, memory_graph, phase,
):
    checkpointer = memory_graph.checkpointer
    delete = Mock(wraps=checkpointer.delete_thread)
    monkeypatch.setattr(checkpointer, "delete_thread", delete)
    initialization = Mock(side_effect=SystemExit("initialization failed"))
    monkeypatch.setattr(service, "get_graph" if phase == "graph" else "get_store", initialization)
    lookup = Mock(side_effect=AssertionError("cleanup initialized another graph"))
    monkeypatch.setattr(chat_adapter, "get_graph", lookup)
    with pytest.raises(ApiError) as caught:
        chat_adapter.start_chat(ChatRequest(message="question"), user_id=1)
    assert (caught.value.status_code, caught.value.code) == (503, "VECTOR_STORE_UNAVAILABLE")
    initialization.assert_called_once_with()
    lookup.assert_not_called()
    if phase == "graph":
        delete.assert_not_called()
    else:
        delete.assert_called_once_with("new-session")
    assert chat_adapter.chat_session_store._sessions == {}


@pytest.mark.parametrize("message,status", [("question", "answered"), ("interrupt", "needs_input")])
def test_successful_start_keeps_real_checkpoint_and_owner(monkeypatch, memory_graph, message, status):
    delete = Mock(wraps=memory_graph.checkpointer.delete_thread)
    monkeypatch.setattr(memory_graph.checkpointer, "delete_thread", delete)
    response = chat_adapter.start_chat(ChatRequest(message=message), user_id=1)
    assert response.status == status
    assert list(memory_graph.checkpointer.list({"configurable": {"thread_id": response.session_id}}))
    assert chat_adapter.chat_session_store.get(response.session_id, user_id=1) is not None
    delete.assert_not_called()


class ObservedLock:
    """sleep 없이 두 번째 작업이 실제 잠금을 기다리는 지점을 관측한다."""

    def __init__(self):
        self.lock = Lock()
        self.waiting = Event()

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            self.waiting.set()
            assert self.lock.acquire(timeout=5)

    def __exit__(self, *args):
        self.lock.release()


@pytest.mark.parametrize("first", ["resume", "delete"])
def test_resume_and_delete_are_serialized(monkeypatch, first):
    store = chat_adapter.chat_session_store
    store.create("session", user_id=1)
    record = store.get("session", user_id=1)
    lock = ObservedLock()
    record.operation_lock = lock
    reference = weakref.ref(record)
    entered, release = Event(), Event()
    checkpoints = {"session": "initial"}
    events = []

    def resume(sid, message, *, top_k=None, extra_interests=None):
        events.append("resume")
        if first == "resume":
            entered.set()
            assert release.wait(5)
        checkpoints[sid] = "resumed"
        return {"session_id": sid, "status": "answered"}

    def delete(sid):
        events.append("delete")
        if first == "delete":
            entered.set()
            assert release.wait(5)
        checkpoints.pop(sid, None)

    monkeypatch.setattr(chat_adapter, "answer_followup", resume)
    monkeypatch.setattr(chat_adapter, "get_graph", lambda: SimpleNamespace(
        checkpointer=SimpleNamespace(delete_thread=delete),
    ))
    calls = {
        "resume": lambda: chat_adapter.continue_chat("session", "답변", user_id=1),
        "delete": lambda: chat_adapter.delete_chat_session("session", user_id=1),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(calls[first])
        try:
            assert entered.wait(5)
            second = "delete" if first == "resume" else "resume"
            second_result = pool.submit(calls[second])
            assert lock.waiting.wait(5)
            assert events == [first]
            # 다른 세션은 장시간 그래프 실행 중에도 접근할 수 있다.
            store.create("independent", user_id=2)
            with store.locked("independent", user_id=2) as other:
                assert other is not None
        finally:
            release.set()
        first_result.result(timeout=5)
        if first == "delete":
            with pytest.raises(ApiError) as caught:
                second_result.result(timeout=5)
            assert caught.value.status_code == 404
        else:
            second_result.result(timeout=5)
    assert "session" not in checkpoints
    assert store.get("session", user_id=1) is None
    chat_adapter.delete_chat_session("session", user_id=1)  # 멱등 삭제
    assert events == (["resume", "delete"] if first == "resume" else ["delete"])
    # 대기 작업까지 끝나면 잠금을 가진 레코드를 보관하는 별도 registry가 없다.
    if first == "resume":
        del record
        assert reference() is None


@pytest.mark.parametrize("operation", ["resume", "delete"])
def test_operation_error_releases_session_lock(monkeypatch, operation):
    store = chat_adapter.chat_session_store
    store.create("session", user_id=1)
    record = store.get("session", user_id=1)
    monkeypatch.setattr(chat_adapter, "answer_followup", Mock(side_effect=RuntimeError("failed")))
    checkpointer = Mock()
    checkpointer.delete_thread.side_effect = RuntimeError("failed")
    monkeypatch.setattr(chat_adapter, "get_graph", lambda: SimpleNamespace(checkpointer=checkpointer))
    with pytest.raises((ApiError, RuntimeError)):
        if operation == "resume":
            chat_adapter.continue_chat("session", "답변", user_id=1)
        else:
            chat_adapter.delete_chat_session("session", user_id=1)
    assert record.operation_lock.acquire(blocking=False)
    record.operation_lock.release()
    assert store.get("session", user_id=1) is record
    checkpointer.delete_thread.side_effect = None
    chat_adapter.delete_chat_session("session", user_id=1)
    assert store.get("session", user_id=1) is None
