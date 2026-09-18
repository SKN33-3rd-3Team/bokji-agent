"""RunPod 응답 계약과 기존 런타임 폴백을 네트워크 없이 검증한다."""

from unittest.mock import Mock

import pytest
import requests

from src.rag_chatbot.llm.client import (
    FallbackLLMClient, LLMCallError, RunPodPodClient, RunPodServerlessClient,
)


@pytest.mark.parametrize("content, finish_reason, valid", [
    ("  완결된 답변입니다.\n", "stop", True),
    ("완결된 답변입니다.", None, True),
    ("", "stop", False),
    (" \n\t", "stop", False),
    (None, "stop", False),
    (["text"], "stop", False),
    (42, "stop", False),
    ("중간에 끊긴 답변", "length", False),
    ("", "length", False),
])
def test_pod_response_validation_and_fallback(monkeypatch, content, finish_reason, valid):
    response = Mock()
    response.json.return_value = {"choices": [{
        "message": {"content": content}, "finish_reason": finish_reason,
    }]}
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    primary = RunPodPodClient(pod_id="test", model="test", api_key="")
    secondary = Mock()
    secondary.complete.return_value = "대체 답변"
    client = FallbackLLMClient(primary, secondary)

    if not valid:
        with pytest.raises(LLMCallError):
            primary.complete("질문")
    result = client.complete("질문", system="system", max_tokens=32)
    if valid:
        assert result == content
        secondary.complete.assert_not_called()
    else:
        assert result == "대체 답변"
        secondary.complete.assert_called_once_with("질문", system="system", max_tokens=32)
    assert post.call_args.kwargs["json"]["max_tokens"] == 32


@pytest.mark.parametrize("client_type", [RunPodPodClient, RunPodServerlessClient])
@pytest.mark.parametrize("status_code, group", [(401, "auth_failure"), (403, "auth_failure"), (503, "server_failure")])
def test_runpod_failure_logged_once_even_when_fallback_succeeds(monkeypatch, caplog, client_type, status_code, group):
    response = requests.Response()
    response.status_code = status_code
    response._content = b'{"error":{"code":"provider_unavailable","type":"upstream_error","message":"secret-raw-output"}}'
    response.raise_for_status = Mock(side_effect=requests.HTTPError("secret-token", response=response))
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    client = (client_type(pod_id="private-pod", api_key="secret-token")
              if client_type is RunPodPodClient else client_type(endpoint_id="private-endpoint", api_key="secret-token"))
    secondary = Mock()
    secondary.complete.return_value = "fallback answer"
    assert FallbackLLMClient(client, secondary).complete("private-profile", system="private-prompt") == "fallback answer"
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert group in message
    assert f"status={status_code}" in message
    assert "code=provider_unavailable" in message and "type=upstream_error" in message
    assert all(secret not in message for secret in ("secret", "private", "fallback answer"))
    caplog.clear()
    with pytest.raises(LLMCallError):
        client.complete("private-profile")
    assert len(caplog.records) == 1  # Direct callers use the same logging boundary.


def test_runpod_failure_log_rejects_free_form_error_fields(monkeypatch, caplog):
    response = requests.Response()
    response.status_code = 500
    response._content = b'{"error":{"code":"Authorization: secret","type":"raw model output\\nsecret"}}'
    response.raise_for_status = Mock(side_effect=requests.HTTPError("secret-token", response=response))
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(LLMCallError):
        RunPodPodClient(pod_id="test").complete("private prompt")
    assert len(caplog.records) == 1
    assert "server_failure" in caplog.text
    assert "secret" not in caplog.text and "raw model" not in caplog.text
