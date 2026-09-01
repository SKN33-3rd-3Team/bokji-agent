"""LLM 호출 추상화 계층.

2026-08-31 기준 팀 계획(확정 아님, 프롬프트/서빙 세부사항 바뀔 수 있음):
- 후보 모델 3개를 비교 중: skt/A.X-4.0-Light, Qwen/Qwen3.5-9B,
  Bllossom/llama-3.2-Korean-Bllossom-3B.
- fine-tuning은 로컬에서 하고, 결과 checkpoint를 HuggingFace Hub에 올린 뒤
  RunPod Serverless(자체 API 규격)로 서빙할 계획.
- 프롬프트/출력 스키마는 아직 설계 단계라 이 파일에 확정된 내용은 없다.

그래서 이 모듈은 "LLM을 어떻게 호출하는가"만 추상화하고, 프롬프트 내용
자체는 각 노드(N5/N9/N13)가 소유한다 - 나중에 프롬프트/스키마가 정해지면
노드 쪽 템플릿만 바꾸면 되고, 이 클라이언트 인터페이스는 그대로 쓸 수
있게 하는 게 목적. RunPod 엔드포인트가 아직 안 떠 있어서(2026-08-31 기준)
RunPodServerlessClient는 아직 실제로 호출해보지 못했다 - 엔드포인트가
뜨면 요청/응답 파싱 부분(특히 _parse_output)을 실제 handler 응답 형태에
맞게 조정해야 할 가능성이 높다.

fine-tuning 전에 "프롬프팅만으로 되는지" 먼저 확인해볼 수 있도록
``HuggingFaceInferenceClient``도 추가했다 (아래 참고) - RunPod에 fine-tuned
checkpoint를 올리기 전에, 같은 후보 모델의 instruct 버전을 HuggingFace
Inference API로 그대로 불러서 N5/N9/N13 프롬프트가 통하는지 테스트하는
용도다. ``scripts/llm_prompt_probe.py``가 이 클라이언트로 실제 프롬프트를
넣어보는 스크립트다.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Protocol

from ..timing import TIMER


class LLMClient(Protocol):
    """N1/N5/N9/N10/N13이 의존하는 최소 인터페이스. 구현체는 이것만 만족하면 된다."""

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """prompt(+ system)를 LLM에 보내고 생성된 텍스트를 그대로 반환한다.

        구조화된 출력(JSON 등)이 필요하면 호출하는 쪽(N5/N9/N13)이 프롬프트에서
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


# ---------------------------------------------------------------------------
# HuggingFace 호출 실패 원인 진단
# ---------------------------------------------------------------------------
#
# "403 Forbidden"만 던지면 사용자가 뭘 고쳐야 하는지 알 수 없다. 실제로
# 2026-08-31에 토큰/크레딧/provider 활성화 중 무엇이 문제인지 몰라 한참
# 헤맸다. HTTP 상태코드별로 "무엇을 확인하면 되는지"까지 메시지에 담는다.

_HTTP_STATUS_PATTERN = re.compile(r"\b([45]\d{2})\b")

_HF_SETTINGS_TOKENS = "https://huggingface.co/settings/tokens"
_HF_SETTINGS_BILLING = "https://huggingface.co/settings/billing"
_HF_SETTINGS_PROVIDERS = "https://huggingface.co/settings/inference-providers"

# provider 라우터가 "그 모델을 서빙하는 provider가 네 계정에 하나도 안 켜져
# 있다"고 답할 때 쓰는 코드. 400으로 오는데, 원인은 요청 형식이 아니라 계정
# 설정이라 따로 잡아야 한다(2026-08-31 실측: Bllossom/A.X가 여기 걸렸다).
_MODEL_NOT_SUPPORTED_MARKER = "model_not_supported"

_HTTP_STATUS_HINTS: dict[int, str] = {
    400: (
        "요청이 거부됨(400). 모델 이름 오타이거나, 그 모델을 서빙하는 "
        "provider가 계정에 켜져 있지 않을 수 있습니다 - 아래 원문을 확인하세요."
    ),
    401: (
        "토큰이 잘못됐거나 만료됨(401 Unauthorized). "
        f"{_HF_SETTINGS_TOKENS} 에서 토큰을 새로 만들고 .env의 HF_TOKEN을 "
        "교체하세요."
    ),
    402: (
        "크레딧 소진(402 Payment Required). 무료 티어는 월 $0.10이고 다 쓰면 "
        f"다음 달까지 막힙니다. {_HF_SETTINGS_BILLING} 에서 잔액/결제 상태를 "
        "확인하세요. 결제했는데도 이 에러면 결제가 아직 반영되지 않았거나 "
        "다른 조직 계정에 붙었을 수 있습니다."
    ),
    403: (
        "권한 거부(403 Forbidden). 셋 중 하나입니다. "
        f"(1) 토큰 권한: {_HF_SETTINGS_TOKENS} 에서 그 토큰에 "
        "'Make calls to Inference Providers' 권한이 켜져 있는지 확인 "
        "(Fine-grained 토큰이면 특히 자주 빠집니다). "
        f"(2) provider 미활성: {_HF_SETTINGS_PROVIDERS} 에서 이 모델을 서빙하는 "
        "provider(예: Featherless AI)를 켰는지 확인. "
        "(3) gated 모델: 모델 페이지에서 사용 약관 동의가 필요한 모델일 수 "
        "있습니다."
    ),
    404: (
        "모델을 찾을 수 없음(404 Not Found). 모델 이름 오타이거나, 어떤 "
        f"provider도 이 모델을 서빙하지 않습니다. {_HF_SETTINGS_PROVIDERS} 와 "
        "모델 페이지의 'Inference Providers' 항목을 확인하세요."
    ),
    422: (
        "요청 형식 거부(422). 이 모델이 chat_completion(대화형) 형식을 "
        "지원하지 않을 수 있습니다 - instruct/chat 모델인지 확인하세요."
    ),
    429: (
        "요청 한도 초과(429 Too Many Requests). 잠시 후 다시 시도하거나 "
        "호출 간격을 두세요."
    ),
    500: "provider 내부 오류(500). 잠시 후 재시도하세요.",
    503: (
        "모델이 아직 로딩 중이거나 provider가 일시적으로 불가능(503). "
        "잠시 후 재시도하세요."
    ),
}


def _extract_http_status(exc: BaseException) -> int | None:
    """예외에서 HTTP 상태코드를 최대한 뽑아낸다.

    huggingface_hub이 던지는 예외 종류가 경로(provider 라우팅 여부)에 따라
    달라서, 응답 객체 -> 속성 -> 메시지 문자열 순으로 훑는다.
    """

    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    match = _HTTP_STATUS_PATTERN.search(str(exc))
    return int(match.group(1)) if match else None


# 프록시/터널 차단은 HTTP 상태코드처럼 생긴 문자열을 달고 온다. 예:
#   ProxyError('Unable to connect to proxy',
#              OSError('Tunnel connection failed: 403 Forbidden'))
# 이걸 HuggingFace가 준 403으로 읽으면 "토큰 권한 문제"라고 엉뚱하게 단정하게
# 된다(2026-08-31에 실제로 이 오진을 했다 - 샌드박스 프록시가 huggingface.co를
# 막고 있었는데 토큰을 재발급하러 갔다). 상태코드 해석보다 먼저 걸러낸다.
_PROXY_ERROR_MARKERS = (
    "proxyerror",
    "unable to connect to proxy",
    "tunnel connection failed",
    "proxy_error",
)
_NETWORK_ERROR_MARKERS = (
    "connection",
    "network",
    "dns",
    "name or service not known",
    "temporary failure in name resolution",
    "ssl",
    "certificate",
)


def diagnose_hf_error(exc: BaseException, model: str) -> str:
    """HuggingFace 호출 실패를 "무엇을 고치면 되는지"까지 담은 문장으로 만든다."""

    status = _extract_http_status(exc)
    detail = str(exc).strip() or exc.__class__.__name__
    header = f"HuggingFace 호출 실패 (모델={model!r})"
    lowered_all = detail.lower()

    if any(marker in lowered_all for marker in _PROXY_ERROR_MARKERS):
        return (
            f"{header}: 프록시가 huggingface.co 연결을 차단했습니다. "
            "**이건 토큰/크레딧/provider 문제가 아닙니다** - 요청이 HuggingFace에 "
            "닿지도 못했습니다. 여기 붙은 상태코드는 프록시가 낸 것이지 "
            "HuggingFace의 응답이 아닙니다. 사내망/샌드박스처럼 외부 접속이 "
            f"제한된 환경에서 실행하고 있는지 확인하세요. 원문={detail}"
        )

    if _MODEL_NOT_SUPPORTED_MARKER in lowered_all:
        return (
            f"{header}: 이 모델을 서빙하는 provider가 계정에 **하나도 켜져 있지 "
            f"않습니다**(model_not_supported). 토큰·크레딧 문제가 아닙니다 - "
            f"같은 토큰으로 다른 모델은 정상 호출됩니다. "
            f"{_HF_SETTINGS_PROVIDERS} 에 가서, 모델 페이지의 "
            "'Inference Providers' 항목에 적힌 provider를 켜세요"
            "(Bllossom / skt-A.X-4.0-Light는 Featherless AI). "
            "provider를 켤 수 없으면 이미 동작하는 다른 모델을 "
            f"LLM_MODEL_NAME으로 지정하세요. 원문={detail}"
        )

    if status is None:
        if "timeout" in lowered_all or "timed out" in lowered_all:
            return (
                f"{header}: 응답 시간 초과. 원문={detail} "
                "-> 모델이 크거나 provider가 느린 경우입니다. timeout_seconds를 "
                "늘리거나 더 작은 모델을 쓰세요."
            )
        if any(marker in lowered_all for marker in _NETWORK_ERROR_MARKERS):
            return (
                f"{header}: 네트워크 연결 실패(HuggingFace에 닿지 못함). "
                f"원문={detail} -> 인터넷/프록시/방화벽을 확인하세요. "
                "토큰 문제가 아닙니다."
            )
        return f"{header}: {detail} (HTTP 상태코드를 확인하지 못함)"

    hint = _HTTP_STATUS_HINTS.get(status)
    if hint is None:
        hint = f"알려진 원인 목록에 없는 상태코드입니다({status})."
    return f"{header}: HTTP {status} - {hint} / 원문={detail}"


def loads_json_object(raw: object) -> dict:
    """모델 응답에서 JSON 객체 하나를 꺼낸다.

    ``json.loads(raw)``를 그대로 쓰면 안 되는 이유: 실제 모델은 코드펜스
    (```json ... ```)나 앞뒤 설명을 붙여서 답하는 일이 아주 흔하다. 그때마다
    파싱이 터지면 "LLM은 제대로 답했는데 우리가 못 읽어서" 값이 통째로
    버려진다. 첫 ``{``부터 마지막 ``}``까지만 잘라서 파싱한다.

    실패하면 ``ValueError``/``TypeError``를 던진다 - 호출부가 폴백을
    결정하도록 두고, 여기서 임의의 기본값을 만들지 않는다.
    """

    if not isinstance(raw, str):
        raise TypeError("LLM 응답이 문자열이 아님")
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM 응답에서 JSON 객체를 찾지 못함")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM 응답의 최상위가 JSON 객체가 아님")
    return data


class RecordingLLMClient:
    """다른 LLM 클라이언트를 감싸 호출 성공/실패를 기록하는 래퍼.

    노드들은 LLM 호출이 실패해도 규칙 기반으로 폴백해서 그래프를 끝까지
    돌린다(설계상 의도). 문제는 그 폴백이 **조용해서**, 화면에는 결과가 정상
    출력되지만 실제로는 LLM이 한 번도 안 돌았을 수 있다는 것이다. 실제로
    HF 토큰이 403을 뱉는 동안에도 답변은 멀쩡히 나왔다.

    그래서 실패를 삼키지 않고 여기 모아둔 뒤, service 계층이
    ``ChatResponse["llm_status"]``로 화면까지 올려 "LLM 없이 규칙으로만
    처리됐다"는 사실을 드러낸다(docs/PROJECT_COMPLIANCE.md - 한계를 숨기지
    않는다).

    주의: 인스턴스 하나를 그래프 전체가 공유하므로 기록도 공유된다. 요청
    단위로 보려면 ``reset()``을 호출한 뒤 그래프를 돌려야 한다. Streamlit
    처럼 여러 사용자가 같은 그래프 객체를 공유하는 환경에서는 동시 요청의
    기록이 섞일 수 있다(현재 구조의 한계 - 세션별 그래프가 필요하면 별도
    작업).
    """

    def __init__(self, inner: LLMClient):
        self.inner = inner
        # 진단 메시지에 모델명을 남기려고 안쪽 클라이언트의 model을 그대로 노출.
        self.model = getattr(inner, "model", None)
        self.call_count = 0
        self.success_count = 0
        self.failures: list[str] = []
        self.durations: list[float] = []
        # N5가 청크별 추출을 동시에 돌리기 시작하면서 이 카운터들이 여러
        # 스레드에서 갱신된다. 잠그지 않으면 호출 수가 실제보다 적게 세진다.
        self._lock = threading.Lock()

    def reset(self) -> None:
        self.call_count = 0
        self.success_count = 0
        self.failures = []
        self.durations = []

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        with self._lock:
            self.call_count += 1
        started = time.perf_counter()
        try:
            result = self.inner.complete(prompt, system=system)
        except LLMCallError as exc:
            message = str(exc)
            with self._lock:
                # 같은 원인이 노드마다 반복되므로 중복은 한 번만 남긴다.
                if message not in self.failures:
                    self.failures.append(message)
            raise
        finally:
            # 실패한 호출도 시간을 잰다. 타임아웃으로 느린 경우가 있어서
            # 성공한 것만 재면 "왜 느린지"를 놓친다.
            elapsed = time.perf_counter() - started
            with self._lock:
                self.durations.append(elapsed)
            TIMER.record("llm_call", elapsed)
        with self._lock:
            self.success_count += 1
        return result

    def summary(self) -> dict:
        """service 계층이 ChatResponse에 실어 보낼 요약."""

        return {
            "enabled": True,
            "model": self.model,
            "calls": self.call_count,
            "successes": self.success_count,
            "failures": len(self.failures),
            "messages": list(self.failures),
            # 호출 하나가 얼마나 걸리는지. 추론형 모델은 내부 사고에 토큰을
            # 크게 써서 호출당 수십 초가 나오기도 한다 - 체감 지연의 주범
            # 인지 여기서 바로 보인다.
            "total_seconds": sum(self.durations),
            "slowest_seconds": max(self.durations) if self.durations else 0.0,
            "avg_seconds": (sum(self.durations) / len(self.durations)) if self.durations else 0.0,
        }


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


class HuggingFaceInferenceClient:
    """HuggingFace Inference API(호스팅형 서버리스)를 호출하는 클라이언트.

    RunPod에 fine-tuned checkpoint를 올리기 전, "프롬프팅만으로 N5/N9/N13이
    되는지" 후보 instruct 모델로 먼저 확인해보는 용도다(``client.py`` 상단
    docstring 참고). ``huggingface_hub``의 ``InferenceClient.chat_completion``
    (OpenAI 호환 chat 포맷)을 쓰므로, 이 프로젝트의 ``complete(prompt,
    system=...)`` 관례를 messages 배열로 그대로 옮기면 된다.

    주의 (2026-08-31 기준, 실제로 겪어본 문제들):
    - 대형/비주류 모델은 HuggingFace Inference Providers 중 이 계정에서
      활성화된 provider가 하나도 없을 수 있다 - 그 경우
      ``model_not_supported``로 요청 자체가 거부된다(``LLMCallError``).
      계정의 https://huggingface.co/settings/inference-providers 에서
      provider를 켜야 한다(모델 페이지에 provider가 나열돼 있어도 계정에서
      별도로 켜야 쓸 수 있다). 그래도 안 되면 유료 Inference Endpoint를
      새로 띄우거나 GPU 환경에서 로컬로 직접 로드해야 한다.
    - "추론(thinking)형" 모델(예: Qwen3.5 계열)은 최종 답을 쓰기 전에 내부
      사고 과정(reasoning)에 토큰을 많이 쓴다 - ``max_new_tokens``가 작으면
      사고 과정만 채우고 실제 답(``message.content``)은 빈 문자열로 잘려서
      나온다(``finish_reason="length"``). **실측(scripts/llm_prompt_probe.py,
      2026-08-31): Qwen3.5-9B가 4096으로도 부족해서(특히 N5처럼 claim_type
      3개를 한 번에 JSON으로 뽑는 무거운 프롬프트) 실패하는 걸 확인했고,
      같은 프롬프트인데도 실행마다 성공/실패가 갈리는 것도 확인함(추론
      길이가 매번 달라짐 - 결정적이지 않음)** - 그래서 기본값을 8192로 더
      올렸다. 그래도 여전히 불안정할 수 있다 - 근본적으로 "추론형" 모델은
      토큰 예산을 아무리 늘려도 매 호출 소요 시간/비용/성공 여부가
      들쭉날쭉하다는 뜻이므로, 프로덕션에는 비-추론형 instruct 모델이 더
      적합할 수 있다.
    - 일부 provider(모델의 chat template이 지원하면)는 ``extra_body``로
      provider 전용 파라미터를 얹을 수 있다 - 예를 들어 Qwen3 계열 일부는
      ``{"chat_template_kwargs": {"enable_thinking": False}}``로 내부 사고
      과정을 끌 수 있다고 알려져 있다(Qwen 공식 문서 기준. **주의: 이
      HuggingFace Inference Providers 라우팅 경로에서 실제로 통하는지는
      네트워크 제약 때문에 직접 검증하지 못했다 - 써보고 안 통하면(그냥
      무시되거나 에러) provider가 지원 안 하는 것으로 보고 포기하면 된다**).
      생성자의 ``extra_body`` 인자로 그대로 전달할 수 있게 열어뒀다.
    - ``huggingface_hub``는 이 리포의 기존 requirements 파일에 없다.
      ``pip install huggingface_hub``로 따로 설치해야 한다(이제
      ``requirements-graph.txt``에 있다).
    - HF 토큰이 필요하다(``HF_TOKEN`` 환경변수 또는 생성자 인자, 계정
      설정에서 "Make calls to Inference Providers" 권한이 켜져 있어야
      한다). 토큰 없이는 대부분의 모델에서 요청이 거부된다.
    """

    def __init__(
        self,
        model: str,
        token: str | None = None,
        *,
        provider: str | None = None,
        timeout_seconds: float = 60.0,
        max_new_tokens: int = 8192,
        extra_body: dict | None = None,
    ):
        self.model = model
        self.token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        if not self.token:
            raise ValueError(
                "HuggingFaceInferenceClient에는 HF 토큰이 필요합니다 "
                "(HF_TOKEN 환경변수로 주거나 생성자 인자로 직접 전달하세요)."
            )
        # provider=None이면 huggingface_hub가 그 모델을 서빙하는 provider를
        # 자동으로 고른다(허브의 라우팅 로직에 위임) - 특정 provider로 고정하고
        # 싶으면 명시적으로 넘기면 된다("hf-inference", "together" 등).
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_new_tokens = max_new_tokens
        # provider별 확장 파라미터(예: 추론형 모델의 "생각 끄기") - 위 docstring
        # 참고. None이면 아무것도 얹지 않는다(기본 동작 그대로).
        self.extra_body = extra_body

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        try:
            from huggingface_hub import InferenceClient
            from huggingface_hub.errors import HfHubHTTPError
        except ImportError as exc:
            raise LLMCallError(
                "huggingface_hub가 설치되어 있지 않습니다. "
                "'pip install huggingface_hub'로 설치하세요."
            ) from exc

        client = InferenceClient(
            model=self.model,
            token=self.token,
            provider=self.provider,
            timeout=self.timeout_seconds,
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = client.chat_completion(
                messages=messages,
                max_tokens=self.max_new_tokens,
                extra_body=self.extra_body,
            )
        except (HfHubHTTPError, Exception) as exc:
            # 상태코드별로 "무엇을 확인하면 되는지"까지 담아 던진다.
            # HfHubHTTPError를 따로 잡지 않는 이유: provider 라우팅 경로에서는
            # requests의 HTTPError 등 다른 예외가 그대로 올라오는 경우가 있어
            # (실측 2026-08-31의 403이 그랬다), 종류와 무관하게 같은 진단을
            # 적용하는 편이 실제로 도움이 된다.
            raise LLMCallError(diagnose_hf_error(exc, self.model)) from exc

        try:
            choice = response.choices[0]
            content = choice.message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMCallError(f"HuggingFace 응답을 파싱하지 못함: {response!r}") from exc
        if not content:
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason == "length":
                # "추론형" 모델이 사고 과정에 max_new_tokens를 다 써버리고
                # 정작 최종 답은 못 쓴 경우가 흔하다 - 위 클래스 docstring 참고.
                raise LLMCallError(
                    f"HuggingFace 응답이 비어 있음(모델={self.model!r}) - "
                    f"finish_reason='length'로 max_new_tokens={self.max_new_tokens} 안에 "
                    "답을 다 못 씀(추론형 모델이면 사고 과정에 토큰을 다 썼을 수 있음). "
                    "max_new_tokens를 늘려보세요."
                )
            raise LLMCallError(f"HuggingFace 응답이 비어 있음: {response!r}")
        return content


class FakeLLMClient:
    """테스트/로컬 개발용. 미리 정해둔 응답을 그대로 돌려준다.

    RunPod 엔드포인트가 아직 안 떠 있는 지금 단계에서, N5/N9/N13의 LLM 연동
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
