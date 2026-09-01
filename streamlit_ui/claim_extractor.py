"""N5용 임시 ClaimExtractor (원문 발췌 기반).

LLM 기반 ClaimExtractor 가 준비되면 이 모듈을 교체한다.  # TODO(N5 LLM)
"""

from __future__ import annotations


class RuleBasedClaimExtractor:
    """정책 청크 원문에서 claim 초안을 만드는 임시 구현.

    N6(document_verification)이 ``reason in chunk_text`` 로 대조하므로,
    reason 은 반드시 청크 원문에서 "그대로 잘라낸" 문자열이어야 한다.
    슬라이스 결과는 항상 원문의 연속 부분 문자열이라 대조를 통과한다.
    """

    def __init__(self) -> None:
        # 정책(policy_id)마다 amount/duplicate claim 은 한 번만 낸다.
        self._amount_done: set[str] = set()
        self._duplicate_done: set[str] = set()

    @staticmethod
    def _verbatim_reason(text: str) -> str | None:
        body = text.split("\n\n", 1)[-1]
        for line in body.split("\n"):
            stripped = line.strip()
            if len(stripped) >= 6:
                # stripped 는 line(=원문의 부분 문자열)의 연속 부분 문자열이고
                # 앞 160자도 그 접두어라 여전히 원문에 그대로 들어 있다.
                return stripped[:160]
        return None

    def extract(self, *, policy_id: str, text: str) -> list[dict]:
        reason = self._verbatim_reason(text)
        if reason is None:
            return []

        claims: list[dict] = [
            {
                "claim_type": "eligibility",
                "law_check_required": False,
                "reasons": [reason],
            }
        ]
        if policy_id not in self._amount_done:
            self._amount_done.add(policy_id)
            claims.append(
                {
                    "claim_type": "amount",
                    "law_check_required": False,
                    "reasons": [reason],
                }
            )
        if policy_id not in self._duplicate_done:
            self._duplicate_done.add(policy_id)
            claims.append(
                {
                    "claim_type": "duplicate",
                    "law_check_required": False,
                    "reasons": [reason],
                }
            )
        return claims
