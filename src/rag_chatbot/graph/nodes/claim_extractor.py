"""N5(plan_claims)가 요구하는 ClaimExtractor 구현체.

claim_plan.py는 "정책 청크 텍스트 -> claim 후보 리스트" 변환 방법을 몰라도
되게 ``ClaimExtractor`` Protocol로 분리해뒀고, 실제 구현체는 "graph.py 조립
시점에 주입"하도록 명시적으로 미뤄뒀다(claim_plan.py 모듈 docstring). 이
파일이 그 구현체다 - Issue #25(graph builder 조립)에서 추가했다.

DRAFT(팀 확인 필요, 2026-08-31 기준 미확정): 다른 LLM 연동 노드들
(eligibility_verdict.py, benefit_calculator.py, document_verification_llm_judge.py)과
마찬가지로 프롬프트/출력 스키마가 아직 확정되지 않았다. 아래 프롬프트는
draft다.

RuleBasedClaimExtractor: llm_client 없이도 그래프 전체가 끝까지 도는 것을
보장하는 기본 구현체. 정책 청크 텍스트 전체를 그대로 reasons로 써서,
document_verification.py의 "reasons가 원문에 실제로 포함되는지" 문자열 포함
검사를 항상 통과하게 만든다(지어낸 문장이 아니라 원문 자체이므로). 텍스트
하나당 claim_type(eligibility/amount/duplicate) 각각 하나씩 claim을 만들고,
law_check_required는 항상 False로 둔다 - 법령 대조가 실제로 필요한지는
LLM(또는 사람) 판단 없이는 알 수 없어서, 과대 주장을 하지 않는 쪽으로
보수적으로 설계했다.

LLMClaimExtractor: llm_client가 있을 때, 청크 텍스트에서 claim 후보(자격/
금액/중복수급 claim_type, 각 근거 문장 reasons, 법령 확인 필요 여부)를 JSON
으로 뽑아내게 한다. reasons는 반드시 원문에서 그대로 발췌해야 한다는 제약을
프롬프트에 명시하지만, 그 제약을 LLM이 실제로 지켰는지는 이 파일이
검증하지 않는다 - document_verification.py(N6)가 각 reason이 원문에 실제로
포함되는지 별도로 검증하므로, 여기서 거짓으로 지어낸 reason은 N6에서
걸러진다(2차 방어선). LLM 호출/파싱이 실패하면(``LLMCallError`` 또는 JSON
파싱 오류) 그 청크만 ``RuleBasedClaimExtractor``로 안전하게 폴백한다 -
그래프 전체가 죽지 않아야 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

from ...llm import LLMCallError, LLMClient, loads_json_object


_CLAIM_TYPES = ("eligibility", "amount", "duplicate")

EXTRACTOR_SYSTEM_PROMPT = (
    "당신은 복지 정책 원문에서 claim 후보를 뽑아내는 도구입니다. 반드시 "
    "원문에 실제로 있는 문장만 reasons로 인용하세요. 원문에 없는 내용을 "
    "추론하거나 요약해서 새로 만들지 마세요."
)

EXTRACTOR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_type": {
                        "type": "string",
                        "enum": list(_CLAIM_TYPES),
                    },
                    "law_check_required": {"type": "boolean"},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                    "required_aspects": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim_type", "reasons"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}
"""RunPodServerlessClient의 guided_json(또는 호환 structured-output)에 그대로
넘길 수 있는 JSON 스키마. document_verification_llm_judge.py의 관례와
동일하게, 이 모듈은 클라이언트 세부사항에 의존하지 않고
``llm_client.complete(prompt, system=...)``만 호출한다."""


class RuleBasedClaimExtractor:
    """LLM 없이도 항상 안전하게 동작하는 기본 ClaimExtractor."""

    def extract(self, *, policy_id: str, text: str) -> list[dict]:
        reason = text.strip()
        if not reason:
            return []
        return [
            {
                "claim_type": claim_type,
                "law_check_required": False,
                "reasons": [reason],
                "required_aspects": [],
            }
            for claim_type in _CLAIM_TYPES
        ]


def _prefetch_workers() -> int:
    """동시에 보낼 LLM 요청 수.

    올릴수록 빨라지지만(청크 5개면 5로 두어야 한 번에 끝난다) provider가
    429(요청 한도 초과)를 낼 수 있고, 그러면 실패한 청크가 규칙 기반으로
    폴백해 품질이 떨어진다. provider별 한도를 모르니 기본값은 보수적으로
    두고 ``LLM_PREFETCH_WORKERS``로 조절할 수 있게 한다.
    """

    try:
        value = int(os.environ.get("LLM_PREFETCH_WORKERS") or 4)
    except ValueError:
        return 4
    return max(1, min(value, 16))
# 캐시 상한. 한 프로세스가 오래 살아도 메모리가 무한정 늘지 않게 한다.
_CACHE_MAX_ENTRIES = 512


class LLMClaimExtractor:
    """LLM으로 claim 후보를 뽑고, 실패하면 규칙 기반으로 폴백하는 구현체.

    같은 청크를 두 번 뽑지 않는다(캐시)
    --------------------------------
    claim 추출 결과는 **정책 원문에만** 의존한다 - 사용자 슬롯이나 대화
    맥락과 무관하다. 그래서 같은 청크는 언제 물어도 같은 답이 나오고,
    두 번째부터는 부를 이유가 없다.

    캐시가 없을 때 실측(2026-08-31): N5가 청크 5개에 대해 매 턴 LLM을
    5번 불러 292초가 걸렸다. 되묻기로 대화가 길어지면 같은 정책을 계속
    다시 뽑는다. 캐시는 프로세스 안에서만 유지된다(재시작하면 사라짐).

    한계(숨기지 않음): 프로세스 전역이 아니라 인스턴스별 캐시이므로,
    그래프를 다시 조립하면 비워진다. 여러 사용자가 한 프로세스를 공유하는
    환경에서도 정책 원문 기준이라 섞여도 안전하다(사용자 정보가 키에 들어가지
    않는다).
    """

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self._fallback = RuleBasedClaimExtractor()
        self._cache: dict[tuple[str, str], list[dict]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _cache_key(policy_id: str, text: str) -> tuple[str, str]:
        # 원문을 통째로 키에 넣으면 메모리를 크게 쓴다. 해시로 줄인다.
        return policy_id, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def prefetch(self, items: Sequence[tuple[str, str]]) -> None:
        """여러 (policy_id, text)를 미리, 동시에 뽑아 캐시에 채운다.

        청크마다의 추출은 서로 완전히 독립이라 순서대로 기다릴 이유가 없다.
        5개를 순차로 부르면 5배 기다리지만 동시에 부르면 가장 느린 하나만
        기다리면 된다.

        여기서 예외를 밖으로 내보내지 않는다 - prefetch는 최적화일 뿐이고,
        실패하면 ``extract()``가 평소대로 직접 호출해 폴백까지 처리한다.
        """

        pending = [
            (policy_id, text)
            for policy_id, text in items
            if text.strip() and self._cache_key(policy_id, text) not in self._cache
        ]
        if len(pending) <= 1:
            # 하나뿐이면 스레드를 띄우는 비용이 이득보다 크다.
            return

        def _one(item: tuple[str, str]) -> None:
            policy_id, text = item
            try:
                self.extract(policy_id=policy_id, text=text)
            except Exception:  # noqa: BLE001 - prefetch 실패는 조용히 넘긴다
                pass

        with ThreadPoolExecutor(max_workers=_prefetch_workers()) as pool:
            list(pool.map(_one, pending))

    def extract(self, *, policy_id: str, text: str) -> list[dict]:
        if not text.strip():
            return []

        key = self._cache_key(policy_id, text)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            # 호출자가 리스트를 바꿔도 캐시가 오염되지 않게 복사해서 준다.
            return [dict(claim) for claim in cached]

        result = self._extract_uncached(policy_id=policy_id, text=text)
        with self._lock:
            if len(self._cache) < _CACHE_MAX_ENTRIES:
                self._cache[key] = [dict(claim) for claim in result]
        return result

    def _extract_uncached(self, *, policy_id: str, text: str) -> list[dict]:
        prompt = (
            f"[정책 ID: {policy_id}]\n[정책 원문]\n{text}\n\n"
            "위 원문에서 자격(eligibility)/지원금액(amount)/중복수급(duplicate) "
            "claim 후보를 뽑아 JSON으로만 답하세요. reasons는 원문에서 그대로 "
            "발췌한 문장이어야 합니다."
        )
        try:
            raw = self.llm_client.complete(prompt, system=EXTRACTOR_SYSTEM_PROMPT)
            # json.loads(raw)를 그대로 쓰면 모델이 코드펜스(```json)나 앞뒤
            # 설명을 붙여 답할 때마다 파싱이 터져, 제대로 뽑아준 claim이
            # 통째로 버려지고 규칙 기반으로 폴백한다. 프로브에서 N5만 한
            # 번도 성공하지 못한 유력한 원인이다(2026-08-31).
            data = loads_json_object(raw)
            claims = data["claims"]
            if not isinstance(claims, list):
                raise ValueError("claims must be a list")
        except (LLMCallError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return self._fallback.extract(policy_id=policy_id, text=text)

        validated: list[dict] = []
        for raw_claim in claims:
            if not isinstance(raw_claim, dict):
                continue
            claim_type = raw_claim.get("claim_type")
            if claim_type not in _CLAIM_TYPES:
                continue
            reasons = raw_claim.get("reasons")
            if not isinstance(reasons, list) or not reasons:
                continue
            reasons = [str(r) for r in reasons]
            # 원문에 실제로 없는 reason은 여기서 조용히 버린다 - N6이 또
            # 검증하지만, 애초에 지어낸 claim을 claim_plan에 남기지 않는
            # 편이 이후 노드들의 판단을 덜 흐린다.
            reasons = [r for r in reasons if r and r in text]
            if not reasons:
                continue
            validated.append(
                {
                    "claim_type": claim_type,
                    "law_check_required": bool(raw_claim.get("law_check_required", False)),
                    "reasons": reasons,
                    "required_aspects": [
                        str(a) for a in raw_claim.get("required_aspects", []) or []
                    ],
                }
            )

        if not validated:
            return self._fallback.extract(policy_id=policy_id, text=text)
        return validated


__all__ = ["RuleBasedClaimExtractor", "LLMClaimExtractor"]
