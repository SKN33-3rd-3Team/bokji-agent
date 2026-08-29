"""N5 후보별 Claim Plan.

Issue #16 (N4~N6): subsidy_chunks의 후보 정책들을 자격·금액·중복수급 claim으로
원자적으로 분해해 claim_plan을 반환한다. (LLM 단발 호출)

입력: GraphState["subsidy_chunks"]
출력: GraphState["claim_plan"] (list[ClaimDraft])

참고할 rag_design 모듈:
    - rag_design.contracts.ClaimCheck (claim 구조 참고)

TODO(N5):
    - claim_type 종류 정의 (예: eligibility / amount / duplicate)
    - doc_check_required=False로 판정하는 기준 확정 전까지는 보수적으로
      True를 기본값으로 둔다 (Issue #16 "하지 않을 일" 참고)
    - LLM 호출로 청크에서 claim 구조화 추출
"""

from __future__ import annotations

from rag_chatbot.graph.state import GraphState


def plan_claims(state: GraphState) -> GraphState:
    """subsidy_chunks를 claim 단위로 원자적으로 분해해 claim_plan을 채운다."""

    raise NotImplementedError("N5 후보별 Claim Plan 미구현 (Issue #16)")
