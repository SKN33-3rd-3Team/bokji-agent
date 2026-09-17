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
