"""N6 공식 정책문서 확인.

Issue #16 (N4~N6): claim_plan 중 doc_check_required=True인 claim만 공고·지침
원문과 대조해서 claim_plan을 갱신한다(evidence_chunk_ids, status).

입력: GraphState["claim_plan"] (doc_check_required=True 대상만)
출력: GraphState["claim_plan"] 갱신

참고:
    - subsidy_chunks 섹션을 재사용할 수 있으면 재사용 (재검색 최소화)
    - "운영기관 원문"까지 포함해 public_detail_url을 실시간 조회할지는
      아직 미정 (Issue #16 "하지 않을 일" 참고) - 확정 전까지는 이미 가진
      chunk 범위 내에서만 대조

TODO(N6): doc_check_required=True 대상 필터링, 공고·지침 원문 대조 로직,
claim_plan.evidence_chunk_ids/status 갱신 구현.
"""

from __future__ import annotations

from rag_chatbot.graph.state import GraphState


def verify_official_documents(state: GraphState) -> GraphState:
    """doc_check_required=True인 claim을 공식 문서와 대조해 claim_plan을 갱신한다."""

    raise NotImplementedError("N6 공식 정책문서 확인 미구현 (Issue #16)")
