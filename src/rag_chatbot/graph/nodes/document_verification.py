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

재시도(N7 리뷰 피드백 #5, 그림 E11: N7 -> N6 근거 부족):
    claim.doc_retry_count > 0이면 "재시도"로 판단해서, N4가 처음 가져온
    top-K 범위 밖까지 넓혀서 재검색한다 (store가 주어졌을 때만 - 주어지지
    않으면 기존 subsidy_chunks 범위 내에서만 조용히 검증한다). 재검색은
    VectorSearchFilter.metadata_equals={"source_id": policy_id}로 그
    정책의 청크만 훨씬 큰 top_k로 다시 가져온다 - 의미 검색이 아니라
    "그 정책의 청크를 최대한 다 끌어오는" 목적이라 쿼리 텍스트 자체는
    중요하지 않다.

TODO(N6, 확인 필요):
    - "운영기관 원문(public_detail_url) 실시간 조회"까지 포함할지 여부가
      팀에서 아직 미정. 확정 전까지는 벡터DB(N4/재검색) 범위 내에서만
      대조한다.
"""

from __future__ import annotations

from rag_design.contracts import EvidenceStatus, RetrievedChunk, SourceType

from rag_chatbot.graph.state import ClaimDraft, GraphState

DEFAULT_WIDEN_TOP_K = 20


def _group_chunks_by_policy(
    subsidy_chunks: list[RetrievedChunk],
) -> dict[str, list[RetrievedChunk]]:
    # N5와 동일하게 doc_id(합성 해시값)가 아니라 chunk.metadata["source_id"]
    # (원본 소스ID)로 묶는다 (N7 리뷰 피드백 반영) - claim.policy_id도 이제
    # source_id 기준이라, 그룹 키를 맞춰야 아래 조회가 실제로 매칭된다.
    grouped: dict[str, list[RetrievedChunk]] = {}
    for chunk in subsidy_chunks:
        grouped.setdefault(chunk.chunk.metadata["source_id"], []).append(chunk)
    return grouped


def _widen_search(store, policy_id: str, query_id: str, top_k: int) -> list[RetrievedChunk]:
    """그 정책(policy_id)의 청크를 top_k까지 넓혀서 다시 가져온다.

    의미 검색이 목적이 아니라 "이 정책의 청크를 최대한 다 끌어오는" 게
    목적이라, query 텍스트는 아무 값이나 넣어도 metadata_equals가
    정확히 그 정책으로 좁혀준다.
    """

    from rag_design.vector_store import VectorSearchFilter

    return list(
        store.search(
            SourceType.SUBSIDY,
            policy_id,  # 의미 검색 대상 아님, metadata_equals가 실제 필터
            query_id=query_id,
            top_k=top_k,
            search_filter=VectorSearchFilter(metadata_equals={"source_id": policy_id}),
        )
    )


def _merge_unique_chunks(
    base: list[RetrievedChunk], extra: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    seen = {chunk.chunk.chunk_id for chunk in base}
    merged = list(base)
    for chunk in extra:
        if chunk.chunk.chunk_id not in seen:
            merged.append(chunk)
            seen.add(chunk.chunk.chunk_id)
    return merged


def verify_official_documents(
    state: GraphState,
    *,
    store=None,
    widen_top_k: int = DEFAULT_WIDEN_TOP_K,
) -> dict:
    """doc_check_required=True인 claim의 reasons가 원문에 실제로 있는지 확인한다.

    store를 주면, doc_retry_count > 0인 claim에 한해 그 정책을 top_k까지
    넓혀서 재검색한 뒤 대조한다 (N7 리뷰 피드백 #5). store가 없으면
    재시도 claim도 에러 없이 기존 범위 내에서만 검증한다 (선택적 기능).
    """

    claim_plan = state.get("claim_plan") or []
    subsidy_chunks = state.get("subsidy_chunks") or []
    query_id = state.get("query_id", "")
    chunks_by_policy = _group_chunks_by_policy(subsidy_chunks)

    updated_plan: list[ClaimDraft] = []
    for claim in claim_plan:
        if not claim.get("doc_check_required"):
            # doc_check_required=False는 이 노드를 거치지 않고 그대로 통과
            # (E9: N5 -> N7 직행 경로에 해당).
            updated_plan.append(claim)
            continue

        policy_id = claim["policy_id"]
        policy_chunks = chunks_by_policy.get(policy_id, [])

        is_retry = claim.get("doc_retry_count", 0) > 0
        if is_retry and store is not None:
            widened = _widen_search(store, policy_id, query_id, widen_top_k)
            policy_chunks = _merge_unique_chunks(policy_chunks, widened)

        reasons = claim.get("reasons", [])

        # N7 리뷰 피드백 반영: evidence_chunk_ids에는 "정책에 속한 청크
        # 전부"가 아니라, reason이 실제로 발견된 그 청크만 넣는다. 그래서
        # 원문을 다 이어붙여서 한 번에 검사하지 않고, 청크 하나하나를
        # 따로 검사해서 어느 청크에서 발견됐는지 추적한다.
        matched_chunk_ids: list[str] = []
        found_reasons: set[str] = set()
        for chunk in policy_chunks:
            chunk_text = chunk.chunk.text
            chunk_has_match = False
            for reason in reasons:
                if reason and reason in chunk_text:
                    found_reasons.add(reason)
                    chunk_has_match = True
            if chunk_has_match:
                matched_chunk_ids.append(chunk.chunk.chunk_id)

        if not policy_chunks:
            status = EvidenceStatus.UNSUPPORTED.value
        elif reasons and len(found_reasons) == len(reasons):
            status = EvidenceStatus.SUPPORTED.value
        elif found_reasons:
            status = EvidenceStatus.PARTIAL.value
        else:
            status = EvidenceStatus.UNSUPPORTED.value

        updated_plan.append(
            {
                **claim,
                "status": status,
                "evidence_chunk_ids": matched_chunk_ids,
            }
        )

    return {"claim_plan": updated_plan}
