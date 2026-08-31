"""LLM 호출 추상화 계층.

2026-08-31 기준 팀 계획(확정 아님, 프롬프트/서빙 세부사항 바뀔 수 있음):
- 후보 모델 3개를 비교 중: skt/A.X-4.0-Light, Qwen/Qwen3.5-9B,
  Bllossom/llama-3.2-Korean-Bllossom-3B.
- fine-tuning은 로컬에서 하고, 결과 checkpoint를 HuggingFace Hub에 올린 뒤
  RunPod Serverless(자체 API 규격)로 서빙할 계획.
- 프롬프트/출력 스키마는 아직 설계 단계라 이 파일에 확정된 내용은 없다.

그래서 이 모듈은 "LLM을 어떻게 호출하는가"만 추상화하고, 프롬프트 내용
자체는 각 노드(N9/N10)가 소유한다 - 나중에 프롬프트/스키마가 정해지면
노드 쪽 템플릿만 바꾸면 되고, 이 클라이언트 인터페이스는 그대로 쓸 수
있게 하는 게 목적. RunPod 엔드포인트가 아직 안 떠 있어서(2026-08-31 기준)
RunPodServerlessClient는 아직 실제로 호출해보지 못했다 - 엔드포인트가
뜨면 요청/응답 파싱 부분(특히 _parse_output)을 실제 handler 응답 형태에
맞게 조정해야 할 가능성이 높다.
"""

from __future__ import annotations

import os
from typing import Protocol


class LLMClient(Protocol):
    """N9/N10이 의존하는 최소 인터페이스. 구현체는 이것만 만족하면 된다."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """prompt(+ system)를 LLM에 보내고 생성된 텍스트를 그대로 반환한다.

        구조화된 출력(JSON 등)이 필요하면 호출하는 쪽(N9/N10)이 프롬프트에서
        JSON으로 답하라고 지시하고 반환된 문자열을 직접 파싱한다 - 이 계층은
        파싱을 책임지지 않는다(프롬프트/스키마가 아직 안 정해졌기 때문).
        """
        ...


class LLMCallError(Exception):
    """LLM 호출 자체가 실패한 경우 (네트워크/타임아웃/응답 형식 오류 등).

    호출하는 노드는 이 예외를 CollectionNotFoundError와 비슷하게 다뤄야
    한다 - 잡아서 "LLM 단계를 못 거쳤다"는 사실을 정직하게 남기고, 절대
    추측한 값으로 대체하지 않는다.
    """


class RunPodServerlessClient:
    """RunPod Serverless 엔드포인트(자체 API 규격)를 호출하는 클라이언트.

    TODO(팀 확인 필요, 확정 전): RunPod Serverless의 요청/응답 JSON 모양은
    거기 배포하는 worker(handler.py)가 정의하는 것이라 정해진 표준이 없다.
    지금은 vLLM 기반 serverless worker에서 흔히 쓰는 관례(input.messages,
    OpenAI 호환 output.choices[0].message.content)를 기본값으로 잡아뒀는데,
    실제 endpoint를 배포하고 나면 이 클래스의 요청 payload 구성과
    _parse_output()을 실제 handler 응답 형태에 맞게 조정해야 한다.
    """

    def __init__(
        self,
        endpoint_id: str | None = None,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout_seconds: float = 60.0,
    ):
        self.endpoint_id = endpoint_id or os.environ.get("RUNPOD_ENDPOINT_ID", "")
        self.api_key = api_key or os.environ.get("RUNPOD_API_KEY", "")
        self.model = model or os.environ.get("RUNPOD_MODEL_NAME", "")
        self.timeout_seconds = timeout_seconds
        if not self.endpoint_id or not self.api_key:
            raise ValueError(
                "RunPodServerlessClient에는 endpoint_id/api_key가 필요합니다 "
                "(RUNPOD_ENDPOINT_ID / RUNPOD_API_KEY 환경변수로 주거나 "
                "생성자 인자로 직접 전달하세요)."
            )

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        import requests

        url = f"https://api.runpod.ai/v2/{self.endpoint_id}/runsync"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {"input": {"model": self.model, "messages": messages}}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise LLMCallError(f"RunPod 호출 실패: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMCallError(f"RunPod 응답이 JSON이 아님: {response.text[:200]!r}") from exc

        status = data.get("status")
        if status not in (None, "COMPLETED"):
            raise LLMCallError(f"RunPod job 상태가 COMPLETED가 아님: {status!r} (raw={data!r})")

        return self._parse_output(data.get("output"))

    @staticmethod
    def _parse_output(output: object) -> str:
        """vLLM 기반 serverless worker의 흔한 두 응답 형태를 시도해본다.

        실제 handler를 배포하고 나서 응답 형태가 다르면 여기만 고치면 된다.
        """
        try:
            if isinstance(output, list) and output:
                return output[0]["choices"][0]["message"]["content"]
            if isinstance(output, dict):
                if "choices" in output:
                    return output["choices"][0]["message"]["content"]
                if "text" in output:
                    return output["text"]
        except (KeyError, IndexError, TypeError):
            pass
        raise LLMCallError(
            "RunPod 응답을 알려진 형태로 파싱하지 못함 - worker의 실제 output "
            f"형태에 맞게 RunPodServerlessClient._parse_output()을 조정해야 함. "
            f"raw output: {output!r}"
        )


class FakeLLMClient:
    """테스트/로컬 개발용. 미리 정해둔 응답을 그대로 돌려준다.

    RunPod 엔드포인트가 아직 안 떠 있는 지금 단계에서, N9/N10의 LLM 연동
    배선 자체가 맞는지 확인하는 용도로 쓴다.
    """

    def __init__(self, response: str = ""):
        self.response = response
        self.calls: list[dict] = []

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        return self.response


class FailingLLMClient:
    """LLM 호출이 실패하는 상황(네트워크 장애 등)을 테스트하기 위한 가짜 구현."""

    def __init__(self, message: str = "테스트용 강제 실패"):
        self.message = message

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMCallError(self.message)
