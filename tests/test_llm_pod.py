"""RunPod 응답 계약과 기존 런타임 폴백을 네트워크 없이 검증한다."""

from unittest.mock import Mock

import pytest
import requests

from src.rag_chatbot.llm.client import FallbackLLMClient, LLMCallError, RunPodPodClient


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
