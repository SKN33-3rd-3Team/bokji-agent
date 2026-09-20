"""Ollama transport and service routing checks without inference or network."""

import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from src.rag_chatbot.llm import LLMCallError, OllamaClient


def mock_response(value):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = (
        value if isinstance(value, bytes) else json.dumps(value).encode()
    )
    return response


def test_success_payload_metadata_and_token_override():
    client = OllamaClient(model="local-model", num_ctx=8192)
    response = {"model": "local-model", "message": {"content": "answer"},
                "eval_count": 5, "done": True, "prompt": "private"}
    with patch.object(client._opener, "open", return_value=mock_response(response)) as send:
        assert client.complete("private", system="system", max_tokens=77) == "answer"
    req = send.call_args.args[0]
    payload = json.loads(req.data)
    assert req.full_url == "http://127.0.0.1:11434/api/chat"
    assert payload == {"model": "local-model", "stream": False,
                       "messages": [{"role": "system", "content": "system"},
                                    {"role": "user", "content": "private"}],
                       "options": {"temperature": 0, "top_p": 1, "top_k": 0,
                                   "repeat_penalty": 1, "seed": 42,
                                   "presence_penalty": 0, "frequency_penalty": 0,
                                   "num_batch": 128,
                                   "num_ctx": 8192, "num_predict": 77}}
    assert client.options["num_predict"] == 1024
    assert client.last_response == {"model": "local-model", "eval_count": 5, "done": True,
                                    "thinking_present": False}


@pytest.mark.parametrize("capabilities,expected", [(["completion"], None),
                                                 (["thinking", "completion"], False)])
def test_thinking_capabilities_checked_once(capabilities, expected):
    client = OllamaClient(model="local", disable_thinking=True)
    with patch.object(client._opener, "open", side_effect=[
        mock_response({"capabilities": capabilities}),
        mock_response({"message": {"content": "ok"}, "done": True}),
        mock_response({"message": {"content": "ok"}, "done": True}),
    ]) as send:
        client.complete("one")
        client.complete("two")
    assert send.call_args_list[0].args[0].full_url.endswith("/api/show")
    for call in send.call_args_list[1:]:
        payload = json.loads(call.args[0].data)
        assert payload.get("think") is expected
        assert ("think" in payload) == (expected is False)


@pytest.mark.parametrize("url", ["https://ollama.com", "http://192.168.1.2:11434",
    "http://localhost.evil", "http://user:pass@localhost", "ftp://localhost",
    "http://localhost/api", "http://localhost?proxy=1", "http://[::2]:11434"])
def test_external_or_ambiguous_address_rejected(url):
    with pytest.raises(ValueError, match="loopback"):
        OllamaClient(model="local", base_url=url)


def test_model_required_and_transport_guards():
    with pytest.raises(ValueError, match="explicit model"):
        OllamaClient(model=" ")
    from src.rag_chatbot.llm.ollama import _NoRedirect
    assert _NoRedirect().redirect_request(None, None, 307, None, None, "https://external") is None
    from urllib.request import ProxyHandler
    with patch("src.rag_chatbot.llm.ollama.request.build_opener") as opener:
        OllamaClient(model="local", base_url="http://[::1]:11434")
    proxy = opener.call_args.args[0]
    assert isinstance(proxy, ProxyHandler)
    assert proxy.proxies == {}


@pytest.mark.parametrize("response", [b"private invalid JSON", b"\xff", [],
    {"error": "private"}, {"message": {"content": " "}, "done": True}, {"message": None},
    {"message": {"content": "unfinished"}},
    {"message": {"content": "unfinished"}, "done": False}])
def test_bad_response_sanitized(response):
    client = OllamaClient(model="local")
    with patch.object(client._opener, "open", return_value=mock_response(response)):
        with pytest.raises(LLMCallError) as exc:
            client.complete("private")
    assert "private" not in str(exc.value)
    assert not exc.value.__cause__


@pytest.mark.parametrize("failure", [TimeoutError("private"), URLError("private"),
    HTTPError("http://localhost", 500, "private", {}, io.BytesIO(b"private"))])
def test_transport_error_sanitized_and_metadata_reset(failure):
    client = OllamaClient(model="local")
    client.last_response = {"eval_count": 10}
    with patch.object(client._opener, "open", side_effect=failure):
        with pytest.raises(LLMCallError) as exc:
            client.complete("private")
    assert "private" not in str(exc.value)
    assert client.last_response == {}


def test_service_ollama_route_never_constructs_hf(monkeypatch):
    from src.rag_chatbot import service
    monkeypatch.delenv("RUNPOD_POD_ID", raising=False)
    for key, value in {"LLM_BACKEND": "ollama", "LLM_MODEL_NAME": "local",
                       "OLLAMA_BASE_URL": "http://localhost:11434",
                       "LLM_TIMEOUT_SECONDS": "30", "LLM_MAX_NEW_TOKENS": "55",
                       "OLLAMA_NUM_CTX": "8192", "LLM_DISABLE_THINKING": "1"}.items():
        monkeypatch.setenv(key, value)
    with patch.object(service, "HuggingFaceInferenceClient") as hf:
        client = service.build_llm_client().inner
        hf.assert_not_called()
    assert isinstance(client, OllamaClient)
    assert client.options["num_ctx"] == 8192
    assert client.options["num_predict"] == 55
    assert client.timeout_seconds == 30
    assert client.think is False


@pytest.mark.parametrize("pod,backend,hf_token,expected", [
    ("pod", "ollama", "token", "fallback"),
    ("pod", "ollama", None, "pod"),
    (None, "ollama", "token", "ollama"),
    (None, None, "token", "hf"),
    (None, None, None, "none"),
])
def test_service_selection_preserves_pod_priority_and_explicit_local_only(
    monkeypatch, pod, backend, hf_token, expected,
):
    from src.rag_chatbot import service
    from src.rag_chatbot.llm import FallbackLLMClient, HuggingFaceInferenceClient, RunPodPodClient

    for key in ("RUNPOD_POD_ID", "LLM_BACKEND", "HF_TOKEN", "HUGGINGFACE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {"RUNPOD_POD_ID": pod, "LLM_BACKEND": backend,
                       "HF_TOKEN": hf_token, "LLM_MODEL_NAME": "fixture"}.items():
        if value is not None:
            monkeypatch.setenv(key, value)
    with patch.object(service, "OllamaClient", wraps=OllamaClient) as local:
        recorder = service.build_llm_client()
        if expected == "none":
            assert recorder is None
        else:
            inner = recorder.inner
            assert isinstance(inner, {"fallback": FallbackLLMClient, "pod": RunPodPodClient,
                                      "ollama": OllamaClient, "hf": HuggingFaceInferenceClient}[expected])
            if expected == "fallback":
                assert isinstance(inner.primary, RunPodPodClient)
                assert isinstance(inner.secondary, HuggingFaceInferenceClient)
        assert local.call_count == (expected == "ollama")


@pytest.fixture
def node_budget(monkeypatch):
    from src.rag_chatbot import deadline
    clock = [0.0]
    monkeypatch.setattr(deadline, "monotonic", lambda: clock[0])
    token = deadline._deadline.set(deadline.Deadline(90))
    try:
        yield clock
    finally:
        deadline._deadline.reset(token)


def test_ollama_show_and_multiple_calls_share_remaining_node_budget(node_budget):
    client = OllamaClient(model="local", disable_thinking=True, timeout_seconds=120)
    timeouts = []

    def send(req, *, timeout):
        timeouts.append(timeout)
        show = req.full_url.endswith("/api/show")
        node_budget[0] += 20 if show else 30
        return mock_response({"capabilities": ["thinking"]} if show else
                             {"message": {"content": "ok"}, "done": True})

    with patch.object(client._opener, "open", side_effect=send):
        assert client.complete("one") == client.complete("two") == "ok"
    assert timeouts == [90, 70, 40]
    assert client.timeout_seconds == 120
    assert client._supports_thinking is True


@pytest.mark.parametrize("failure", [None, TimeoutError("private"),
    HTTPError("http://localhost", 503, "private", {}, io.BytesIO()),
    ValueError("private")])
def test_expired_show_never_starts_chat_or_updates_capability_cache(node_budget, failure):
    from src.rag_chatbot.deadline import NodeDeadlineExceeded
    client = OllamaClient(model="local", disable_thinking=True)

    def send(*args, **kwargs):
        node_budget[0] = 90
        if failure is not None:
            raise failure
        return mock_response({"capabilities": ["thinking"]})

    with patch.object(client._opener, "open", side_effect=send) as transport:
        with pytest.raises(NodeDeadlineExceeded):
            client.complete("private")
    assert transport.call_count == 1
    assert client._supports_thinking is None and client.last_response == {}


def test_expired_node_does_not_send_or_reset_newer_metadata(node_budget):
    from src.rag_chatbot.deadline import NodeDeadlineExceeded
    client = OllamaClient(model="local")
    client.last_response = {"eval_count": 10}
    node_budget[0] = 90
    with patch.object(client._opener, "open", return_value=mock_response(
        {"message": {"content": "ok"}, "done": True},
    )) as transport:
        with pytest.raises(NodeDeadlineExceeded):
            client.complete("private")
    transport.assert_not_called()
    assert client.last_response == {"eval_count": 10}


def test_late_chat_cannot_overwrite_other_call_metadata(node_budget):
    from src.rag_chatbot.deadline import NodeDeadlineExceeded
    client = OllamaClient(model="local")

    def send(*args, **kwargs):
        node_budget[0] = 90
        client.last_response = {"eval_count": 10}  # Another completed request.
        return mock_response({"message": {"content": "late"}, "done": True, "eval_count": 99})

    with patch.object(client._opener, "open", side_effect=send):
        with pytest.raises(NodeDeadlineExceeded):
            client.complete("private")
    assert client.last_response == {"eval_count": 10}


def test_ollama_failure_is_strict_only_in_automatic_recommendations():
    from src.rag_chatbot.llm.client import GraphLLMClient, GraphProviderError, strict_llm_scope
    client = OllamaClient(model="local")
    graph_client = GraphLLMClient(client)
    with patch.object(client._opener, "open", side_effect=URLError("private")) as transport:
        with strict_llm_scope(True), pytest.raises(GraphProviderError):
            graph_client.complete("private")
        with strict_llm_scope(False), pytest.raises(LLMCallError):
            graph_client.complete("private")
    assert transport.call_count == 2  # No automatic local/remote fallback.
