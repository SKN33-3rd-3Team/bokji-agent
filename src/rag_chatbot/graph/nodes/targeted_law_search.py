"""N8 targeted search for declared canonical legal metadata."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import date
from typing import Any, TypeAlias

from rag_design.contracts import AbstentionReason, RetrievedChunk, SourceType
from rag_design.index_policy import QueryScope, route_indexes
from rag_design.policy import AbstentionDecision
from rag_design.vector_store import VectorSearchFilter

from ..state import ClaimDraft, GraphState
from .evidence_gate import (
    _ClaimCoverage,
    _as_of_date,
    _claim_plan,
    _law_pair,
    _query_id,
    _resolve_evidence,
    _retrieved_chunks,
    _strict_law_evidence_reason,
    _strict_law_reason,
    _string_list,
)


LawSearch: TypeAlias = Callable[..., Sequence[RetrievedChunk]]


def _fallback_query(
    *,
    coverage: _ClaimCoverage,
    subsidy_chunks: list[RetrievedChunk],
) -> str:
    valid_ids = {item.chunk.chunk_id for item in coverage.subsidy_chunks}
    return "\n".join(
        item.chunk.text
        for item in subsidy_chunks
        if item.chunk.chunk_id in valid_ids
    )


def _validated_results(
    value: Any,
    *,
    query_id: str,
    as_of: date,
    expected_pair: tuple[str, str],
) -> tuple[RetrievedChunk, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("law search must return a sequence of RetrievedChunk values")
    result = tuple(value)
    for item in result:
        if not isinstance(item, RetrievedChunk):
            raise ValueError("law search returned a non-RetrievedChunk value")
        try:
            actual_pair = _law_pair(item)
        except ValueError as exc:
            raise ValueError("law search returned invalid source identity") from exc
        if (
            item.query_id != query_id
            or item.chunk.source_type is not SourceType.LAW
            or item.index_name != SourceType.LAW.value
            or actual_pair != expected_pair
            or _strict_law_reason(item, as_of=as_of) is not None
        ):
            raise ValueError("law search returned an invalid expected-pair result")
    return result


def _same_evidence_payload(first: RetrievedChunk, second: RetrievedChunk) -> bool:
    return (
        first.chunk == second.chunk
        and first.query_id == second.query_id
        and first.chunk.source_type is second.chunk.source_type
        and first.index_name == second.index_name
    )


def _merge_law_candidates(
    candidates: list[RetrievedChunk],
    *,
    query_id: str,
    as_of: date,
    subsidy_ids: set[str],
) -> list[RetrievedChunk]:
    if any(item.chunk.chunk_id in subsidy_ids for item in candidates):
        raise ValueError("law chunk_id collides with the subsidy evidence pool")

    first_by_id: dict[str, RetrievedChunk] = {}
    for item in candidates:
        chunk_id = item.chunk.chunk_id
        previous = first_by_id.get(chunk_id)
        if previous is not None and not _same_evidence_payload(previous, item):
            raise ValueError("same chunk_id refers to different evidence payloads")
        first_by_id.setdefault(chunk_id, item)

    for item in candidates:
        if (
            item.query_id != query_id
            or item.chunk.source_type is not SourceType.LAW
            or item.index_name != SourceType.LAW.value
        ):
            raise ValueError("combined law chunks contain invalid evidence")
        try:
            _law_pair(item)
        except ValueError as exc:
            raise ValueError("combined law chunks contain invalid identity") from exc

    reason = _strict_law_evidence_reason(tuple(candidates), as_of=as_of)
    if reason is AbstentionReason.CONFLICT:
        raise ValueError(
            "combined law chunks contain conflicting source sequences or revision payloads"
        )
    if reason is not None:
        raise ValueError("combined law chunks contain invalid evidence")
    return list(first_by_id.values())


def _unchanged_update(
    claims: list[ClaimDraft], law_chunks: list[RetrievedChunk]
) -> dict[str, Any]:
    return {
        "claim_plan": deepcopy(claims),
        "law_chunks": list(law_chunks),
    }


def search_targeted_laws(
    state: GraphState, *, search: LawSearch
) -> dict[str, Any]:
    """Search each still-missing declared law pair and return an N8 update."""

    if state.get("safety_blocked") is not False:
        raise ValueError("N8 requires safety_blocked to be false")
    if state.get("evidence_gate_verdict") != "insufficient_law":
        raise ValueError("N8 requires an insufficient_law verdict")
    if type(state.get("law_retry_count")) is not int or state["law_retry_count"] != 1:
        raise ValueError("N8 requires law_retry_count == 1")
    decision = state.get("abstention_decision")
    if (
        not isinstance(decision, AbstentionDecision)
        or decision.abstain is not True
        or decision.reason is not AbstentionReason.NO_EVIDENCE
    ):
        raise ValueError("N8 requires a NO_EVIDENCE abstention decision")
    missing_documents = state.get("missing_document_claim_ids")
    if not isinstance(missing_documents, list) or missing_documents != []:
        raise ValueError("N8 requires an empty missing_document_claim_ids list")

    query_id = _query_id(state)
    claims = _claim_plan(state)
    as_of = _as_of_date(state)
    subsidy_chunks = _retrieved_chunks(state, "subsidy_chunks")
    law_chunks = _retrieved_chunks(state, "law_chunks")
    target_ids = _string_list(
        state.get("missing_law_claim_ids"), "missing_law_claim_ids"
    )
    if not target_ids:
        raise ValueError("missing_law_claim_ids must be non-empty")

    resolution = _resolve_evidence(
        query_id=query_id,
        claims=claims,
        subsidy_chunks=subsidy_chunks,
        law_chunks=law_chunks,
        as_of=as_of,
    )
    if (
        resolution.terminal_reason is not None
        or resolution.missing_document_claim_ids
        or list(resolution.missing_law_claim_ids) != target_ids
    ):
        raise ValueError("N8 targets do not match current clean evidence coverage")

    target_set = set(target_ids)
    target_coverages = [
        coverage
        for coverage in resolution.claims
        if coverage.claim["claim_id"] in target_set
    ]

    source = SourceType(route_indexes(QueryScope.LAW)[0])
    additions: list[RetrievedChunk] = []
    subsidy_ids = {item.chunk.chunk_id for item in subsidy_chunks}
    additions_by_claim: dict[str, list[RetrievedChunk]] = {
        claim_id: [] for claim_id in target_ids
    }
    for coverage in target_coverages:
        claim = coverage.claim
        query = claim.get("search_query", "").strip()
        if not query:
            query = _fallback_query(
                coverage=coverage,
                subsidy_chunks=subsidy_chunks,
            )
        if not query:
            if additions:
                _merge_law_candidates(
                    [*law_chunks, *additions],
                    query_id=query_id,
                    as_of=as_of,
                    subsidy_ids=subsidy_ids,
                )
            return _unchanged_update(claims, law_chunks)

        for law_type, source_id in coverage.missing_sources:
            found = _validated_results(
                search(
                    source,
                    query,
                    query_id=query_id,
                    search_filter=VectorSearchFilter(
                        as_of=as_of,
                        metadata_equals={
                            "law_type": law_type,
                            "source_id": source_id,
                        },
                    ),
                ),
                query_id=query_id,
                as_of=as_of,
                expected_pair=(law_type, source_id),
            )
            if not found:
                if additions:
                    _merge_law_candidates(
                        [*law_chunks, *additions],
                        query_id=query_id,
                        as_of=as_of,
                        subsidy_ids=subsidy_ids,
                    )
                return _unchanged_update(claims, law_chunks)
            additions.extend(found)
            additions_by_claim[claim["claim_id"]].extend(found)

    candidates = [*law_chunks, *additions]
    merged_laws = _merge_law_candidates(
        candidates,
        query_id=query_id,
        as_of=as_of,
        subsidy_ids=subsidy_ids,
    )
    updated_plan = deepcopy(claims)
    for claim in updated_plan:
        evidence_ids = list(claim.get("evidence_chunk_ids", []))
        for item in additions_by_claim.get(claim["claim_id"], []):
            if item.chunk.chunk_id not in evidence_ids:
                evidence_ids.append(item.chunk.chunk_id)
        claim["evidence_chunk_ids"] = evidence_ids
    return {"claim_plan": updated_plan, "law_chunks": merged_laws}


__all__ = ["LawSearch", "search_targeted_laws"]
