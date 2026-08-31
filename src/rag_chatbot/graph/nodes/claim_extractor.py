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

import json

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


class LLMClaimExtractor:
    """LLM으로 claim 후보를 뽑고, 실패하면 규칙 기반으로 폴백하는 구현체."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self._fallback = RuleBasedClaimExtractor()

    def extract(self, *, policy_id: str, text: str) -> list[dict]:
        if not text.strip():
            return []
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
