"""노드 예산과 늦게 끝나는 동기 호출의 수명 경계를 외부 I/O 없이 검증한다."""

from threading import Barrier, Event, Timer
from time import monotonic
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.rag_chatbot import service, timing
from src.rag_chatbot.graph.builder import build_graph, run_graph
from src.rag_chatbot.graph.state import GraphState
from backend.app.services import chat_adapter
from backend.app.core.errors import ApiError
from backend.app.schemas.chat import ChatRequest


@pytest.mark.parametrize("provider", ["fake", "ollama"])
def test_http_deadline_cleans_checkpoint_without_waiting_for_late_provider(client, monkeypatch, provider):
    entered, release, finished = Event(), Event(), Event()

    class SlowProvider:
        def complete(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            finished.set()
            return "{}"

    llm_client = SlowProvider()
    if provider == "ollama":
        monkeypatch.delenv("RUNPOD_POD_ID", raising=False)
        monkeypatch.setenv("LLM_BACKEND", "ollama")
        monkeypatch.setenv("LLM_MODEL_NAME", "local-fixture")
        llm_client = service.build_llm_client()
        def open_response(*args, **kwargs):
            return BytesIO(('{"done":true,"message":{"content":' +
                            '"' + SlowProvider().complete() + '"}}').encode())
        monkeypatch.setattr(llm_client.inner._opener, "open", open_response)

    store = SimpleNamespace(search=lambda *args, **kwargs: [])
    graph = build_graph(store, llm_client=llm_client)
    monkeypatch.setattr(timing, "LLM_NODE_TIMEOUT_SECONDS", 0.08, raising=False)
    monkeypatch.setattr(service, "_runtime_cache", {"graph": graph, "store": store, "llm_client": None})
    monkeypatch.setattr(chat_adapter.uuid, "uuid4", lambda: "deadline-session")
    assert client.post("/api/v1/auth/signup", json={
        "email": "deadline@example.com", "name": "테스터", "password": "Passw0rd!123",
        "password_confirm": "Passw0rd!123", "terms_agreed": True, "privacy_agreed": True,
    }).status_code == 201
    # Red is bounded too: the old implementation eventually returns needs_input200.
    release_timer = Timer(0.4, release.set)
    release_timer.start()
    try:
        started = monotonic()
        response = client.post("/api/v1/chat/messages", json={"message": "지원 알려줘"})
        elapsed = monotonic() - started
        assert entered.is_set()
        assert (response.status_code, response.json().get("code")) == (500, "GRAPH_EXECUTION_ERROR")
        assert elapsed < 0.3 and not finished.is_set()
        assert not chat_adapter.chat_session_store._sessions
        config = {"configurable": {"thread_id": "deadline-session"}}
        assert not list(graph.checkpointer.list(config))
    finally:
        release.set()
        release_timer.cancel()
        release_timer.join()
        assert finished.wait(3)
    assert not list(graph.checkpointer.list(config))


def test_expiry_does_not_wait_for_sibling_or_resurrect_deleted_checkpoint(monkeypatch):
    entered, finished, release = Barrier(3), Barrier(3), Event()
    def provider_node(state):
        if state["user_input"] == "other":
            return {"answer_status": "complete"}
        try:
            entered.wait(3)
            assert release.wait(3)
            return Command(update={"final_answer": "late answer"})
        finally:
            finished.wait(3)
    def sibling_node(state):
        if state["user_input"] == "other":
            return {}
        try:
            entered.wait(3)
            assert release.wait(3)
            state["slots"]["region"] = "late mutation"
            return {"slots": state["slots"]}
        finally:
            finished.wait(3)

    builder = StateGraph(GraphState)
    builder.add_node("provider", timing.timed_node("benefit_calculator", provider_node, llm=True))
    builder.add_node("sibling", timing.timed_node("duplicate_benefit", sibling_node))
    for name in ("provider", "sibling"):
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    graph = builder.compile(checkpointer=MemorySaver())
    run_graph(graph, session_id="other", user_input="other")
    other_config = {"configurable": {"thread_id": "other"}}
    before = list(graph.checkpointer.list(other_config))
    monkeypatch.setattr(service, "_runtime_cache", {"graph": graph, "store": object(), "llm_client": None})
    monkeypatch.setattr(timing, "LLM_NODE_TIMEOUT_SECONDS", .15)
    monkeypatch.setattr(chat_adapter.uuid, "uuid4", lambda: "failed")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(chat_adapter.start_chat, ChatRequest(message="x"), user_id=1)
        try:
            entered.wait(3)
            with pytest.raises(ApiError) as caught:
                future.result(1)
            assert (caught.value.status_code, caught.value.code) == (500, "GRAPH_EXECUTION_ERROR")
            assert not list(graph.checkpointer.list({"configurable": {"thread_id": "failed"}}))
            assert chat_adapter.chat_session_store.get("failed", user_id=1) is None
        finally:
            release.set()
            finished.wait(3)
    assert not list(graph.checkpointer.list({"configurable": {"thread_id": "failed"}}))
    assert list(graph.checkpointer.list(other_config)) == before
