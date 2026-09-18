"""Loopback-only Ollama chat client; no prompts or response text are retained.

API contract: https://docs.ollama.com/api/chat
``think=None`` omits thinking control; ``False`` checks model capabilities first.
"""

from __future__ import annotations

import ipaddress
import json
from urllib import error, request
from urllib.parse import urlsplit

from .client import LLMCallError
from ..deadline import NodeDeadlineExceeded, active_write, check_deadline, remaining_timeout


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OllamaClient:
    def __init__(
        self, *, model: str, base_url: str = "http://localhost:11434",
        timeout_seconds: float = 120.0, num_predict: int = 1024,
        num_ctx: int = 4096, temperature: float = 0, top_p: float = 1,
        top_k: int = 0, repeat_penalty: float = 1, seed: int = 42,
        presence_penalty: float = 0, frequency_penalty: float = 0,
        num_batch: int = 128,
        disable_thinking: bool = False, think: bool | None = None,
    ):
        if not model or not model.strip():
            raise ValueError("Ollama requires an explicit model")
        try:
            url = urlsplit(base_url)
            host = url.hostname
            local = host == "localhost" or ipaddress.ip_address(host).is_loopback
            valid = (local and url.scheme in {"http", "https"}
                     and url.username is None and url.password is None
                     and url.path in {"", "/"} and not url.query and not url.fragment)
            port = url.port or 11434
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Ollama base URL must be a loopback HTTP(S) origin")
        # Pin localhost to a literal loopback address; ignore environment proxies.
        host = "127.0.0.1" if host == "localhost" else host
        self.base_url = f"{url.scheme}://{'[' + host + ']' if ':' in host else host}:{port}"
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.options = dict(temperature=temperature, top_p=top_p, top_k=top_k,
                            repeat_penalty=repeat_penalty, seed=seed,
                            presence_penalty=presence_penalty,
                            frequency_penalty=frequency_penalty, num_batch=num_batch,
                            num_ctx=num_ctx, num_predict=num_predict)
        self.think = False if disable_thinking else think
        self._supports_thinking: bool | None = None
        self.last_response: dict = {}
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def _post(self, path: str, payload: dict) -> dict:
        req = request.Request(self.base_url + path,
                              data=json.dumps(payload).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(req, timeout=remaining_timeout(self.timeout_seconds)) as response:
                result = json.loads(response.read())
            check_deadline()
        except NodeDeadlineExceeded:
            raise
        except error.HTTPError as exc:
            check_deadline()
            raise LLMCallError(f"Ollama HTTP error ({exc.code})") from None
        except (OSError, error.URLError):
            check_deadline()
            raise LLMCallError("Ollama connection or timeout error") from None
        except (ValueError, UnicodeError):
            check_deadline()
            raise LLMCallError("Ollama invalid JSON response") from None
        if not isinstance(result, dict) or "error" in result:
            raise LLMCallError("Ollama invalid or error response")
        return result

    def complete(self, prompt: str, *, system: str | None = None,
                 max_tokens: int | None = None) -> str:
        with active_write():
            self.last_response = {}
        messages = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        options = dict(self.options)
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload = dict(model=self.model, messages=messages, stream=False, options=options)
        if self.think is not None:
            if self._supports_thinking is None:
                info = self._post("/api/show", {"model": self.model})
                capabilities = info.get("capabilities", [])
                with active_write():
                    self._supports_thinking = (
                        isinstance(capabilities, list) and "thinking" in capabilities
                    )
            if self._supports_thinking:
                payload["think"] = self.think
        result = self._post("/api/chat", payload)
        # Explicit metadata allowlist excludes echoed prompts, messages and reasoning.
        metadata = {key: result[key] for key in (
            "model", "created_at", "done", "done_reason", "total_duration",
            "load_duration", "prompt_eval_count", "prompt_eval_cached_count",
            "prompt_eval_duration", "eval_count", "eval_duration",
        ) if key in result}
        message = result.get("message")
        metadata["thinking_present"] = bool(
            isinstance(message, dict) and message.get("thinking")
        )
        with active_write():
            self.last_response = metadata
        content = message.get("content") if isinstance(message, dict) else None
        if result.get("done") is not True:
            raise LLMCallError("Ollama returned an incomplete response")
        if not isinstance(content, str) or not content.strip():
            raise LLMCallError("Ollama returned empty or invalid content")
        check_deadline()
        return content
