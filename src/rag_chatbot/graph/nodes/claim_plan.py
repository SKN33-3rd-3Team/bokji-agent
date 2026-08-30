"""N5 후보별 Claim Plan.

Issue #16 (N4~N6): subsidy_chunks의 후보 정책들을 자격·금액·중복수급 claim으로
원자적으로 분해해 claim_plan을 반환한다. (LLM 단발 호출)

입력: GraphState["subsidy_chunks"]
출력: {"claim_plan": list[ClaimDraft]}

주의: 저장소에 아직 LLM 클라이언트 연동 컨벤션(langchain/openai 등)이 확정된
게 없다 (2026-08-29 기준 관련 코드 전무). 그래서 이 노드는 실제 LLM 호출을
직접 하지 않고, ``ClaimExtractor`` 인터페이스로 분리해뒀다 - graph.py 조립
시점에 실제 LLM 기반 구현체를 주입하면 된다. 지금은 테스트/스모크 체크용
FakeClaimExtractor(claim_plan.py 밖, 테스트 파일)로만 검증 가능하다.

TODO(N5, 확인 필요):
    - "doc_check_required=False로 판정하는 기준"이 팀에서 아직 미정
      (결정사항 로그 참고). 기준이 정해지기 전까지는 보수적으로 항상 True.
    - 실제 LLM 기반 ClaimExtractor 구현은 팀 LLM 컨벤션 확정 후 별도 작업.
"""

from __future__ import annotations

from typing import Protocol

from rag_chatbot.graph.state import ClaimDraft, GraphState

CLAIM_TYPES = ("eligibility", "amount", "duplicate")


class ClaimExtractor(Protocol):
    """정책 청크 텍스트 하나에서 claim 초안들을 뽑아내는 인터페이스.

    실제 구현체(LLM 호출)는 graph.py 조립 시점에 주입한다. 이 노드 자체는
    "청크 -> claim 후보 리스트" 변환을 어떻게 하는지는 몰라도 된다.
    """

    def extract(self, *, policy_id: str, text: str) -> list[dict]:
        """[{"claim_type": ..., "law_check_required": bool, "reasons": [...]}]

        reasons는 반드시 text(원문)에서 "그대로 발췌"한 문장이어야 한다
        (의역 금지, 팀 확인 필요 - 확정되면 이 제약을 지우거나 바꾼다).
        N6(document_verification.py)이 이 문장이 원문에 실제로 있는지
        단순 텍스트 포함 여부로 대조하기 때문에, 의역하면 N6이 실제로는
        맞는 claim도 "근거 없음"으로 잘못 판정하게 된다.
        """
        ...


def plan_claims(state: GraphState, extractor: ClaimExtractor) -> dict:
    """subsidy_chunks를 claim 단위로 원자적으로 분해해 claim_plan을 채운다."""

    subsidy_chunks = state.get("subsidy_chunks") or []
    claim_plan: list[ClaimDraft] = []

    for chunk in subsidy_chunks:
        policy_id = chunk.chunk.doc_id
        # 정책 하나가 여러 청크로 쪼개져 있을 수 있어서, claim_id는
        # policy_id가 아니라 청크 고유 chunk_id를 기준으로 유일하게 만든다
        # (policy_id는 여러 claim이 같은 정책을 가리키도록 공유되는 게 맞음).
        chunk_id = chunk.chunk.chunk_id
        raw_claims = extractor.extract(policy_id=policy_id, text=chunk.chunk.text)
        for index, raw in enumerate(raw_claims):
            claim_type = raw["claim_type"]
            claim_plan.append(
                ClaimDraft(
                    claim_id=f"{chunk_id}:{claim_type}:{index}",
                    policy_id=policy_id,
                    claim_type=claim_type,
                    # 판정 기준 미정이므로 보수적으로 항상 True로 둔다
                    # (Issue #16 "하지 않을 일" 참고).
                    doc_check_required=True,
                    law_check_required=bool(raw.get("law_check_required", False)),
                    evidence_chunk_ids=[],
                    status="pending",
                    reasons=list(raw.get("reasons", [])),
                )
            )

    return {"claim_plan": claim_plan}
