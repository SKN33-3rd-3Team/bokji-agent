import json
import os
from unittest.mock import patch

import pytest
from huggingface_hub import InferenceClient

from src.rag_chatbot.service import build_llm_client
from src.rag_chatbot.llm import LLMCallError


ENV = {
    "LLM_BACKEND": "ollama",
    "OLLAMA_MODEL": "qwen-test",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_TIMEOUT_SECONDS": "37",
    "LLM_MAX_NEW_TOKENS": "123",
    "HF_TOKEN": "must-not-reach-ollama",
}


@pytest.mark.parametrize("suffix", ["", "/", "/v1", "/v1/"])
def test_ollama_uses_existing_chat_transport_without_hf_credentials(suffix):
    requests = []

    def post(client, request, **kwargs):
        requests.append(request)
        assert client.timeout == 37
        return json.dumps({"choices": [{"message": {"role": "assistant", "content": "답변"},
                                      "finish_reason": "stop", "index": 0}]}).encode()

    with patch.dict(os.environ, {**ENV, "OLLAMA_BASE_URL": ENV["OLLAMA_BASE_URL"] + suffix}, clear=True):
        client = build_llm_client()
    with patch.object(InferenceClient, "_inner_post", post):
        assert client.complete("질문", system="검증된 근거만 사용") == "답변"
    request = requests[0]
    assert request.url == "http://localhost:11434/v1/chat/completions"
    assert request.json["model"] == "qwen-test"
    assert request.json["max_tokens"] == 123
    assert request.json["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in request.json
    assert request.json["messages"] == [
        {"role": "system", "content": "검증된 근거만 사용"},
        {"role": "user", "content": "질문"},
    ]
    assert "must-not-reach-ollama" not in str(request.headers)
    assert client.summary()["model"] == "qwen-test"
    assert client.summary()["successes"] == 1


@pytest.mark.parametrize("payload", [
    {"choices": []},
    {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
    {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
])
def test_ollama_invalid_or_truncated_output_is_recorded_as_failure(payload):
    with patch.dict(os.environ, ENV, clear=True):
        client = build_llm_client()
    with patch.object(InferenceClient, "_inner_post", return_value=json.dumps(payload).encode()):
        with pytest.raises(LLMCallError):
            client.complete("질문")
    assert client.summary()["failures"] == 1
    assert client.summary()["successes"] == 0


def test_ollama_transport_failure_does_not_expose_raw_error():
    with patch.dict(os.environ, ENV, clear=True):
        client = build_llm_client()
    with patch.object(InferenceClient, "_inner_post", side_effect=TimeoutError("private detail")):
        with pytest.raises(LLMCallError, match="Ollama") as error:
            client.complete("질문")
    assert "private detail" not in str(error.value)
    assert client.summary()["failures"] == 1


@pytest.mark.parametrize("override", [
    {"LLM_BACKEND": "typo"}, {"OLLAMA_MODEL": ""},
    {"OLLAMA_TIMEOUT_SECONDS": "nan"}, {"OLLAMA_TIMEOUT_SECONDS": "0"},
    {"LLM_MAX_NEW_TOKENS": "-1"}, {"OLLAMA_BASE_URL": "file:///tmp/model"},
    {"OLLAMA_BASE_URL": "http://user:secret@localhost:11434"},
])
def test_invalid_ollama_configuration_fails_before_inference(override):
    with patch.dict(os.environ, {**ENV, **override}, clear=True):
        with pytest.raises(ValueError):
            build_llm_client()


def test_hf_default_and_no_token_behavior_are_preserved():
    with patch.dict(os.environ, {}, clear=True):
        assert build_llm_client() is None
    with patch.dict(os.environ, {"HF_TOKEN": "hf-test", "LLM_MODEL_NAME": "hf/model"}, clear=True):
        client = build_llm_client()
    assert client.inner.model == "hf/model"
    assert client.inner.token == "hf-test"
    assert client.inner.base_url is None
    assert client.inner.extra_body is None

    with patch.dict(os.environ, {"HF_TOKEN": "hf-test", "LLM_DISABLE_THINKING": "1"}, clear=True):
        client = build_llm_client()
    assert client.inner.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
