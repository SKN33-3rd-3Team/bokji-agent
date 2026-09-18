"""실제 동기 LangGraph + 가짜 provider로 노드별 공유 예산을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.rag_chatbot import deadline, timing
from src.rag_chatbot.graph.builder import resume_graph, run_graph
from src.rag_chatbot.graph.nodes.claim_extractor import LLMClaimExtractor
from src.rag_chatbot.llm.client import (
    FallbackLLMClient, HuggingFaceInferenceClient, LLMCallError, RecordingLLMClient, RunPodPodClient,
)


def test_multiple_calls_and_fallback_share_transport_remaining_without_mutating_clients(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(deadline, "monotonic", lambda: clock[0])
    timeouts = []

    def post(*args, **kwargs):
        timeouts.append(("pod", kwargs["timeout"]))
        clock[0] += 10
        raise requests.ConnectionError("offline synthetic failure")

    class HF:
        def __init__(self, **kwargs):
            timeouts.append(("hf", kwargs["timeout"]))

        def chat_completion(self, **kwargs):
            clock[0] += 20
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")])

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("huggingface_hub.InferenceClient", HF)
    pod = RunPodPodClient(pod_id="fake", timeout_seconds=120)
    hf = HuggingFaceInferenceClient("fake", token="fake", timeout_seconds=120)
    client = FallbackLLMClient(pod, hf)
    completed = []

    def node(state):
        for _ in range(3):
            completed.append(client.complete("fixture"))
        return {}

    with pytest.raises(deadline.NodeDeadlineExceeded):
        timing.timed_node("claim_plan", node, llm=True)({})
    assert completed == ["ok", "ok"]
    assert timeouts == [("pod", 90), ("hf", 80), ("pod", 60), ("hf", 50), ("pod", 30), ("hf", 20)]
    assert pod.timeout_seconds == hf.timeout_seconds == 120


def test_expired_primary_never_starts_fallback_even_if_it_raises_normal_call_error(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(deadline, "monotonic", lambda: clock[0])
    def fail(*args, **kwargs):
        clock[0] = 90
        raise LLMCallError("primary failed")
    primary = SimpleNamespace(complete=fail)
    secondary = Mock()
    with pytest.raises(deadline.NodeDeadlineExceeded):
        timing.timed_node("slot_parser", lambda _: FallbackLLMClient(primary, secondary).complete("x"), llm=True)({})
    secondary.complete.assert_not_called()


def test_per_node_fresh_budget_and_non_llm_and_direct_calls_unchanged(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(deadline, "monotonic", lambda: clock[0])
    budgets = []
    def node(state):
        budgets.append(deadline.remaining_timeout(200))
        clock[0] += 89
        return {"answer_status": "complete"}
    builder = StateGraph(dict)
    builder.add_node("first", timing.timed_node("first", node, llm=True))
    builder.add_node("second", timing.timed_node("second", node, llm=True))
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    run_graph(builder.compile(checkpointer=MemorySaver()), session_id="fresh", user_input="x")
    assert budgets == [90, 90]
    assert clock[0] == 178  # No whole-chat90 cap.
    assert timing.timed_node("non_llm", node)({}) == {"answer_status": "complete"}
    assert budgets[-1] == 200
    # Direct API12-style LLM calls have their existing transport timeout, no node budget.
    observed = []
    def post(*args, **kwargs):
        observed.append(kwargs["timeout"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(requests, "post", post)
    assert RunPodPodClient(pod_id="fake", timeout_seconds=123).complete("x") == "ok"
    assert observed == [123]


def test_parallel_prefetch_shares_one_token_and_concurrent_requests_are_isolated(monkeypatch):
    barrier = Barrier(4)
    observed = []
    lock = Lock()
    class Provider:
        def complete(self, prompt, **kwargs):
            with lock:
                observed.append((prompt, deadline._deadline.get(), deadline._execution.get()))
            barrier.wait(3)
            return '{"claims": [{"claim_type": "eligibility", "reasons": ["근거"]}]}'

    def request(name):
        extractor = LLMClaimExtractor(Provider())
        def node(state):
            extractor.prefetch([(name + "1", "근거 하나"), (name + "2", "근거 둘")])
            return {}
        with deadline.graph_execution():
            timing.timed_node("claim_plan", node, llm=True)({})
        assert len(extractor._cache) == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(request, "a"), pool.submit(request, "b")
        a.result(5)
        b.result(5)
    for prefix in ("a", "b"):
        pairs = [(token, execution) for prompt, token, execution in observed if f"ID: {prefix}" in prompt]
        assert len(pairs) == 2 and pairs[0][0] is pairs[1][0] and pairs[0][1] is pairs[1][1]
    assert len({id(row[1]) for row in observed}) == len({id(row[2]) for row in observed}) == 2


def test_expired_prefetch_cannot_write_shared_cache_or_late_recording_stats(monkeypatch):
    release, entered, finished = Event(), Barrier(3), Barrier(3)
    class Provider:
        def complete(self, *args, **kwargs):
            entered.wait(3)
            assert release.wait(3)
            return '{"claims": [{"claim_type": "eligibility", "reasons": ["근거"]}]}'
    recorder = RecordingLLMClient(Provider())
    extractor = LLMClaimExtractor(recorder)
    original_extract = extractor.extract
    def tracked(**kwargs):
        try:
            return original_extract(**kwargs)
        finally:
            finished.wait(3)
    monkeypatch.setattr(extractor, "extract", tracked)
    monkeypatch.setattr(timing, "LLM_NODE_TIMEOUT_SECONDS", .15)
    def node(_):
        extractor.prefetch([("a", "근거 하나"), ("b", "근거 둘")])
        return {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(timing.timed_node("claim_plan", node, llm=True), {})
        try:
            entered.wait(3)
            with pytest.raises(deadline.NodeDeadlineExceeded):
                future.result(1)
            assert not extractor._cache
            before = recorder.summary()
        finally:
            release.set()
            finished.wait(3)
    assert not extractor._cache
    assert recorder.summary() == before
    assert before["calls"] == 2 and before["successes"] == 0


def test_interrupt_resume_copied_context_has_fresh_deadline_and_preserves_input(monkeypatch):
    tokens = []
    def node(state):
        tokens.append(deadline._deadline.get())
        first = interrupt("first")
        second = interrupt("second")
        return {"answer_status": "complete", "final_answer": first + second}
    builder = StateGraph(dict)
    builder.add_node("ask", timing.timed_node("request_calc_info", node, llm=True))
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    graph = builder.compile(checkpointer=MemorySaver())
    assert "__interrupt__" in run_graph(graph, session_id="interrupt", user_input="x")
    assert "__interrupt__" in resume_graph(graph, session_id="interrupt", user_input="A")
    result = resume_graph(graph, session_id="interrupt", user_input="B")
    assert result["final_answer"] == "AB"
    assert len(tokens) == 3 and len({id(token) for token in tokens}) == 3


def test_pool_admission_is_bounded_by_node_budget_and_does_not_enqueue_more_work(monkeypatch):
    pool = deadline.BoundedExecutor(1, "test-bounded")
    entered, release = Event(), Event()
    def block():
        entered.set()
        assert release.wait(3)
    busy = pool.submit(block)
    assert entered.wait(3)
    monkeypatch.setattr(timing, "LLM_NODE_TIMEOUT_SECONDS", .04)
    extra = Mock()
    try:
        with pytest.raises(deadline.NodeDeadlineExceeded):
            timing.timed_node("claim_plan", lambda _: pool.submit(extra), llm=True)({})
        extra.assert_not_called()
    finally:
        release.set()
        busy.result(3)
        pool.pool.shutdown()


@pytest.mark.parametrize("discovery_seconds", [20, 90])
def test_installed_hf_post_rechecks_budget_after_provider_discovery(monkeypatch, discovery_seconds):
    from huggingface_hub import InferenceClient
    from huggingface_hub.inference import _client
    clock = [0.0]
    monkeypatch.setattr(deadline, "monotonic", lambda: clock[0])
    def prepare(**kwargs):
        clock[0] += discovery_seconds
        return object()
    monkeypatch.setattr(_client, "get_provider_helper", lambda *a, **k: SimpleNamespace(prepare_request=prepare))
    sent = []
    def post(self, *args, **kwargs):
        sent.append(self.timeout)
        return b'{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop","index":0}],"created":0,"id":"fake","model":"fake","object":"chat.completion"}'
    monkeypatch.setattr(InferenceClient, "_inner_post", post)
    client = HuggingFaceInferenceClient("fake", token="fake", timeout_seconds=120)
    node = timing.timed_node("answer_generation", lambda _: client.complete("fixture"), llm=True)
    if discovery_seconds == 90:
        with pytest.raises(deadline.NodeDeadlineExceeded):
            node({})
        assert sent == []
    else:
        assert node({}) == "ok"
        assert sent == [70]
    assert client.timeout_seconds == 120


def test_cache_lock_wait_cannot_extend_deadline_or_admit_late_write(monkeypatch):
    produced, release, finished = Event(), Event(), Event()
    extractor = LLMClaimExtractor(Mock())
    def extract(**kwargs):
        produced.set()
        assert release.wait(3)
        return [{"claim_type": "eligibility", "reasons": ["근거"]}]
    monkeypatch.setattr(extractor, "_extract_uncached", extract)
    monkeypatch.setattr(timing, "LLM_NODE_TIMEOUT_SECONDS", .1)
    def node(_):
        try:
            return extractor.extract(policy_id="p", text="근거")
        finally:
            finished.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(timing.timed_node("claim_plan", node, llm=True), {})
        assert produced.wait(3)
        with extractor._lock:
            release.set()
            with pytest.raises(deadline.NodeDeadlineExceeded):
                future.result(1)
            assert not extractor._cache and not finished.is_set()
        assert finished.wait(3)
    assert not extractor._cache


def test_one_request_timeout_does_not_cancel_another_request(monkeypatch):
    entered, release = Barrier(3), Event()
    def node(_):
        entered.wait(3)
        assert release.wait(3)
        return {"ok": True}
    def request(llm):
        with deadline.graph_execution():
            return timing.timed_node("node", node, llm=llm)({})
    monkeypatch.setattr(timing, "LLM_NODE_TIMEOUT_SECONDS", .08)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(request, True), pool.submit(request, False)
        try:
            entered.wait(3)
            with pytest.raises(deadline.NodeDeadlineExceeded):
                a.result(1)
            assert not b.done()
        finally:
            release.set()
        assert b.result(3) == {"ok": True}
