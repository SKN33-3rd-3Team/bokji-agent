"""N6 공식 정책문서 확인.

Issue #16 (N4~N6): claim_plan 중 doc_check_required=True인 claim만, N4가 이미
검색해둔 subsidy_chunks(같은 정책의 원문)와 대조해서 claim_plan을 갱신한다
(evidence_chunk_ids, status).

입력: GraphState["claim_plan"], GraphState["subsidy_chunks"]
출력: {"claim_plan": list[ClaimDraft]}  (doc_check_required=False인 claim은
      그대로 통과시키고, True인 것만 검증해서 갱신)

설계 전제 (팀 확인 필요, 아직 확정 아님):
    N5(claim_plan.py)의 ClaimExtractor가 claim.reasons를 "원문에서 그대로
    발췌한 문장"으로 채운다고 가정한다. 이 전제가 성립하면 "claim이 원문과
    맞는지"는 단순 텍스트 포함 여부(in 연산자)로 판정 가능해서 LLM이 필요
    없다 - 그래서 이 노드엔 LLM 인터페이스가 없다.

    만약 팀이 "의역 허용"으로 정하면, reasons가 원문과 글자 그대로 안 맞을
    수 있어서 이 노드도 다시 LLM 기반 의미 대조로 바꿔야 한다.

status 값은 rag_design.contracts.EvidenceStatus와 동일한 문자열을 쓴다:
    - supported: claim의 reasons가 전부 원문에서 발견됨
    - partial: 일부만 발견됨
    - unsupported: 하나도 발견되지 않음 (또는 대조할 원문 자체가 없음)
    (원문 그대로 발췌 전제 하에서는 "conflict"는 발생하지 않는다 - claim이
    원문 자체를 인용한 거라 원문과 모순될 수가 없다.)

TODO(N6, 확인 필요):
    - "운영기관 원문(public_detail_url) 실시간 조회"까지 포함할지 여부가
      팀에서 아직 미정. 확정 전까지는 N4가 이미 가져온 subsidy_chunks
      범위 내에서만 대조한다.
"""

from __future__ import annotations

from rag_design.contracts import EvidenceStatus, RetrievedChunk

from rag_chatbot.graph.state import ClaimDraft, GraphState


def _group_chunks_by_policy(
    subsidy_chunks: list[RetrievedChunk],
) -> dict[str, list[RetrievedChunk]]:
    grouped: dict[str, list[RetrievedChunk]] = {}
    for chunk in subsidy_chunks:
        grouped.setdefault(chunk.chunk.doc_id, []).append(chunk)
    return grouped


def verify_official_documents(state: GraphState) -> dict:
    """doc_check_required=True인 claim의 reasons가 원문에 실제로 있는지 확인한다."""

    claim_plan = state.get("claim_plan") or []
    subsidy_chunks = state.get("subsidy_chunks") or []
    chunks_by_policy = _group_chunks_by_policy(subsidy_chunks)

    updated_plan: list[ClaimDraft] = []
    for claim in claim_plan:
        if not claim.get("doc_check_required"):
            # doc_check_required=False는 이 노드를 거치지 않고 그대로 통과
            # (E9: N5 -> N7 직행 경로에 해당).
            updated_plan.append(claim)
            continue

        policy_chunks = chunks_by_policy.get(claim["policy_id"], [])
        if not policy_chunks:
            updated_plan.append(
                {
                    **claim,
                    "status": EvidenceStatus.UNSUPPORTED.value,
                    "evidence_chunk_ids": [],
                }
            )
            continue

        source_text = "\n".join(chunk.chunk.text for chunk in policy_chunks)
        reasons = claim.get("reasons", [])
        found_reasons = [reason for reason in reasons if reason and reason in source_text]

        if reasons and len(found_reasons) == len(reasons):
            status = EvidenceStatus.SUPPORTED.value
        elif found_reasons:
            status = EvidenceStatus.PARTIAL.value
        else:
            status = EvidenceStatus.UNSUPPORTED.value

        updated_plan.append(
            {
                **claim,
                "status": status,
                "evidence_chunk_ids": (
                    [chunk.chunk.chunk_id for chunk in policy_chunks] if found_reasons else []
                ),
            }
        )

    return {"claim_plan": updated_plan}
