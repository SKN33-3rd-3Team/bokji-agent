"""N7 evidence gate for exact document and legal-source coverage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from rag_design.chunking import compute_chunk_id_from_document_id
from rag_design.citation import legal_citation_url
from rag_design.contracts import (
    AbstentionReason,
    EvidenceStatus,
    LegalDocumentType,
    RetrievedChunk,
    SourceType,
    compute_content_hash,
    compute_document_id,
    is_canonical_date,
)
from rag_design.index_policy import MetadataFilter, chunk_matches_filter
from rag_design.policy import (
    LEGAL_ARTICLE_BODY_ASPECT,
    LEGAL_INTERPRETATION_ASPECT,
    LEGAL_METADATA_ASPECT,
    AbstentionDecision,
    EvidenceState,
    decide_abstention,
    supported_legal_evidence_aspects,
)

from ..state import ClaimDraft, EvidenceGateVerdict, GraphState


_CLAIM_TYPES = frozenset({"eligibility", "amount", "duplicate"})
_LEGAL_ASPECTS = frozenset(
    {
        LEGAL_METADATA_ASPECT,
        LEGAL_ARTICLE_BODY_ASPECT,
        LEGAL_INTERPRETATION_ASPECT,
    }
)
_UNSUPPORTED_LEGAL_ASPECTS = frozenset(
    {LEGAL_ARTICLE_BODY_ASPECT, LEGAL_INTERPRETATION_ASPECT}
)
_LawPair = tuple[str, str]
_LawRevision = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class _ClaimCoverage:
    claim: ClaimDraft
    subsidy_chunks: tuple[RetrievedChunk, ...]
    missing_sources: tuple[_LawPair, ...]


@dataclass(frozen=True, slots=True)
class _EvidenceResolution:
    claims: tuple[_ClaimCoverage, ...] = ()
    evidence_chunk_ids: tuple[str, ...] = ()
    missing_document_claim_ids: tuple[str, ...] = ()
    missing_law_claim_ids: tuple[str, ...] = ()
    missing_aspects: frozenset[str] = frozenset()
    terminal_reason: AbstentionReason | None = None


def _nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _ascii_decimal(value: Any, field_name: str) -> str:
    value = _nonempty_string(value, field_name)
    if any(character < "0" or character > "9" for character in value):
        raise ValueError(f"{field_name} must contain only ASCII decimal digits")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    for item in value:
        _nonempty_string(item, f"{field_name} item")
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must not contain duplicates")
    return value


def _query_id(state: GraphState) -> str:
    return _nonempty_string(state.get("query_id"), "query_id")


def _required_source_pairs(claim: ClaimDraft) -> tuple[_LawPair, ...]:
    return tuple(
        (source["law_type"], source["source_id"])
        for source in claim.get("required_law_sources", [])
    )


def _claim_plan(state: GraphState) -> list[ClaimDraft]:
    value = state.get("claim_plan")
    if not isinstance(value, list) or not value:
        raise ValueError("claim_plan must be a non-empty list")

    claim_ids: list[str] = []
    for index, claim in enumerate(value):
        if not isinstance(claim, dict):
            raise ValueError(f"claim_plan[{index}] must be an object")
        claim_ids.append(_nonempty_string(claim.get("claim_id"), "claim_id"))
        _nonempty_string(claim.get("policy_id"), "policy_id")
        claim_type = claim.get("claim_type")
        if not isinstance(claim_type, str) or claim_type not in _CLAIM_TYPES:
            raise ValueError("claim_type must be eligibility, amount, or duplicate")

        for field_name in ("doc_check_required", "law_check_required"):
            if type(claim.get(field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")

        _string_list(claim.get("evidence_chunk_ids", []), "evidence_chunk_ids")
        _string_list(claim.get("reasons", []), "reasons")

        status = claim.get("status")
        if status is not None:
            try:
                EvidenceStatus(status)
            except (TypeError, ValueError) as exc:
                raise ValueError("status must be a supported EvidenceStatus") from exc

        aspects = _string_list(
            claim.get("required_aspects", []), "required_aspects"
        )
        unknown = set(aspects) - _LEGAL_ASPECTS
        if unknown:
            raise ValueError(f"unsupported required_aspects: {sorted(unknown)!r}")

        sources = claim.get("required_law_sources", [])
        if not isinstance(sources, list):
            raise ValueError("required_law_sources must be a list")
        pairs: list[_LawPair] = []
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"law_type", "source_id"}:
                raise ValueError(
                    "RequiredLawSource must contain law_type and source_id"
                )
            try:
                law_type = LegalDocumentType(source["law_type"]).value
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "RequiredLawSource.law_type is unsupported"
                ) from exc
            source_id = _ascii_decimal(
                source["source_id"], "RequiredLawSource.source_id"
            )
            pairs.append((law_type, source_id))
        if len(set(pairs)) != len(pairs):
            raise ValueError("required_law_sources must not contain duplicate pairs")

        if not claim["law_check_required"]:
            if aspects:
                raise ValueError(
                    "required_aspects must be empty when law_check_required is false"
                )
            if sources:
                raise ValueError(
                    "required_law_sources must be empty when law_check_required is false"
                )

        if "search_query" in claim and not isinstance(claim["search_query"], str):
            raise ValueError("search_query must be a string")

    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("claim_id must be unique within claim_plan")
    return value


def _retrieved_chunks(state: GraphState, field_name: str) -> list[RetrievedChunk]:
    value = state.get(field_name, [])
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    if any(not isinstance(item, RetrievedChunk) for item in value):
        raise ValueError(f"{field_name} must contain RetrievedChunk values")
    return value


def _retry_count(state: GraphState, field_name: str) -> int:
    value = state.get(field_name, 0)
    if type(value) is not int or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be an integer from 0 to 1")
    return value


def _as_of_date(state: GraphState) -> date:
    value = state.get("as_of")
    if type(value) is date:
        return value
    if not is_canonical_date(value):
        raise ValueError("as_of must be a date or canonical YYYY-MM-DD string")
    return date.fromisoformat(value)


def _claim_status(claim: ClaimDraft) -> EvidenceStatus | None:
    value = claim.get("status")
    return None if value is None else EvidenceStatus(value)


def _law_metadata(item: RetrievedChunk) -> Mapping[str, Any]:
    metadata = item.chunk.metadata
    if not isinstance(metadata, Mapping):
        raise ValueError("law evidence metadata must be a mapping")
    return metadata


def _law_structure_reason(item: RetrievedChunk) -> AbstentionReason | None:
    try:
        metadata = _law_metadata(item)
    except ValueError:
        return AbstentionReason.NO_EVIDENCE
    source_name = metadata.get("source_name")
    if not isinstance(source_name, str) or not source_name.strip():
        return AbstentionReason.NO_EVIDENCE
    return None


def _subsidy_updated_at_is_canonical(value: Any) -> bool:
    if is_canonical_date(value):
        return True
    if not isinstance(value, str) or not ("T" in value or " " in value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _subsidy_parent_id_is_canonical(item: RetrievedChunk) -> bool:
    metadata = item.chunk.metadata
    if not isinstance(metadata, Mapping):
        return False
    source_id = metadata.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        return False

    source_updated_at = metadata.get("source_updated_at")
    effective_from = metadata.get("effective_from")
    if source_updated_at is not None:
        if not _subsidy_updated_at_is_canonical(source_updated_at):
            return False
        content_hash = ""
    elif effective_from is not None:
        if not is_canonical_date(effective_from):
            return False
        content_hash = ""
    else:
        _, separator, content_hash_prefix = item.chunk.doc_id.rpartition(":")
        if (
            not separator
            or len(content_hash_prefix) != 16
            or any(
                character not in "0123456789abcdef"
                for character in content_hash_prefix
            )
        ):
            return False
        content_hash = f"{content_hash_prefix}{'0' * 48}"

    expected_doc_id = compute_document_id(
        source_type=SourceType.SUBSIDY,
        source_id=source_id,
        source_updated_at=source_updated_at,
        effective_from=effective_from,
        content_hash=content_hash,
    )
    return item.chunk.doc_id == expected_doc_id


def _law_pair(item: RetrievedChunk) -> _LawPair:
    metadata = _law_metadata(item)
    try:
        law_type = LegalDocumentType(metadata.get("law_type")).value
    except (TypeError, ValueError) as exc:
        raise ValueError("law evidence has an unsupported law_type") from exc
    return law_type, _ascii_decimal(metadata.get("source_id"), "law source_id")


def _law_revision(item: RetrievedChunk) -> _LawRevision:
    law_type, source_id = _law_pair(item)
    source_sequence = _ascii_decimal(
        _law_metadata(item).get("source_sequence"), "law source_sequence"
    )
    return law_type, source_id, source_sequence


def _law_date_reason(item: RetrievedChunk, *, as_of: date) -> AbstentionReason | None:
    chunk = item.chunk
    structure_reason = _law_structure_reason(item)
    if structure_reason is not None:
        return structure_reason
    metadata = _law_metadata(item)
    for field_name in ("effective_from", "effective_date", "issued_date"):
        if not is_canonical_date(metadata.get(field_name)):
            return AbstentionReason.STALE
    for field_name in ("effective_to", "source_updated_at"):
        value = metadata.get(field_name)
        if value is not None and not is_canonical_date(value):
            return AbstentionReason.STALE
    if metadata["effective_date"] != metadata["effective_from"]:
        return AbstentionReason.STALE
    effective_from = date.fromisoformat(metadata["effective_from"])
    effective_to_value = metadata.get("effective_to")
    if (
        effective_to_value is not None
        and date.fromisoformat(effective_to_value) <= effective_from
    ):
        return AbstentionReason.STALE
    if not chunk_matches_filter(
        chunk, MetadataFilter(source_type=SourceType.LAW, as_of=as_of)
    ):
        return AbstentionReason.STALE
    return None


def _law_parent_payload(item: RetrievedChunk) -> tuple[Any, ...]:
    chunk = item.chunk
    parent_metadata = {
        key: value for key, value in _law_metadata(item).items() if key != "chunk_part"
    }
    return (
        chunk.schema_version,
        chunk.doc_id,
        chunk.source_type,
        chunk.heading_path,
        chunk.citation_locator,
        parent_metadata,
    )


def _strict_law_payload_reason(item: RetrievedChunk) -> AbstentionReason | None:
    chunk = item.chunk
    metadata = _law_metadata(item)

    try:
        law_type, source_id, source_sequence = _law_revision(item)
        part = metadata.get("chunk_part")
        part_count = metadata.get("chunk_part_count")
        version = metadata.get("chunking_version")
        if (
            type(part) is not int
            or type(part_count) is not int
            or part < 0
            or part_count < 1
            or part >= part_count
            or type(chunk.ordinal) is not int
            or chunk.ordinal != part
            or not isinstance(version, str)
        ):
            return AbstentionReason.NO_EVIDENCE
        expected_source_url = legal_citation_url(
            law_type=law_type,
            source_sequence=source_sequence,
            effective_from=metadata["effective_from"],
        )
        expected_doc_id = compute_document_id(
            source_type=SourceType.LAW,
            source_id=source_id,
            source_updated_at=metadata.get("source_updated_at"),
            effective_from=metadata["effective_from"],
            content_hash=chunk.content_hash,
            law_type=law_type,
            source_sequence=source_sequence,
        )
        expected_chunk_id = compute_chunk_id_from_document_id(
            expected_doc_id, chunk.heading_path, part, version
        )
    except (KeyError, TypeError, ValueError):
        return AbstentionReason.NO_EVIDENCE
    if (
        chunk.content_hash != compute_content_hash(chunk.text)
        or metadata.get("source_url") != expected_source_url
        or chunk.doc_id != expected_doc_id
        or chunk.chunk_id != expected_chunk_id
        or LEGAL_METADATA_ASPECT
        not in supported_legal_evidence_aspects((chunk,))
    ):
        return AbstentionReason.NO_EVIDENCE
    return None


def _strict_law_reason(
    item: RetrievedChunk, *, as_of: date
) -> AbstentionReason | None:
    return (
        _law_structure_reason(item)
        or _law_date_reason(item, as_of=as_of)
        or _strict_law_payload_reason(item)
    )


def _strict_law_evidence_reason(
    items: tuple[RetrievedChunk, ...], *, as_of: date
) -> AbstentionReason | None:
    for item in items:
        reason = _law_structure_reason(item)
        if reason is not None:
            return reason

    for item in items:
        reason = _law_date_reason(item, as_of=as_of)
        if reason is not None:
            return reason

    revisions: dict[_LawRevision, list[RetrievedChunk]] = {}
    sequences_by_pair: dict[_LawPair, set[str]] = {}
    for item in items:
        try:
            revision = _law_revision(item)
        except ValueError:
            return AbstentionReason.NO_EVIDENCE
        revisions.setdefault(revision, []).append(item)
        sequences_by_pair.setdefault(revision[:2], set()).add(revision[2])

    if any(len(sequences) > 1 for sequences in sequences_by_pair.values()):
        return AbstentionReason.CONFLICT

    for revision_items in revisions.values():
        parent_payload = _law_parent_payload(revision_items[0])
        parts: dict[int, RetrievedChunk] = {}
        for item in revision_items:
            if _law_parent_payload(item) != parent_payload:
                return AbstentionReason.CONFLICT
            part = item.chunk.metadata.get("chunk_part")
            if type(part) is int:
                previous = parts.get(part)
                if previous is not None and previous.chunk != item.chunk:
                    return AbstentionReason.CONFLICT
                parts.setdefault(part, item)

    for item in items:
        reason = _strict_law_payload_reason(item)
        if reason is not None:
            return reason
    return None


def _terminal_resolution(
    reason: AbstentionReason,
    *,
    evidence_chunk_ids: tuple[str, ...] = (),
    missing_aspects: frozenset[str] = frozenset(),
) -> _EvidenceResolution:
    return _EvidenceResolution(
        evidence_chunk_ids=evidence_chunk_ids,
        missing_aspects=missing_aspects,
        terminal_reason=reason,
    )


def _resolve_evidence(
    *,
    query_id: str,
    claims: list[ClaimDraft],
    subsidy_chunks: list[RetrievedChunk],
    law_chunks: list[RetrievedChunk],
    as_of: date,
) -> _EvidenceResolution:
    occurrences: dict[str, list[tuple[SourceType, RetrievedChunk]]] = {}
    for pool, chunks in (
        (SourceType.SUBSIDY, subsidy_chunks),
        (SourceType.LAW, law_chunks),
    ):
        for item in chunks:
            occurrences.setdefault(item.chunk.chunk_id, []).append((pool, item))

    resolved: list[
        tuple[ClaimDraft, tuple[RetrievedChunk, ...], tuple[RetrievedChunk, ...]]
    ] = []
    evidence_ids: list[str] = []
    for claim in claims:
        subsidy_evidence: list[RetrievedChunk] = []
        law_evidence: list[RetrievedChunk] = []
        for chunk_id in claim.get("evidence_chunk_ids", []):
            matches = occurrences.get(chunk_id, [])
            if len(matches) != 1:
                return _terminal_resolution(AbstentionReason.NO_EVIDENCE)
            pool, item = matches[0]
            if (
                item.query_id != query_id
                or item.chunk.source_type is not pool
                or item.index_name != pool.value
            ):
                return _terminal_resolution(AbstentionReason.NO_EVIDENCE)
            if pool is SourceType.SUBSIDY:
                metadata = item.chunk.metadata
                source_id = (
                    metadata.get("source_id")
                    if isinstance(metadata, Mapping)
                    else None
                )
                if (
                    not isinstance(source_id, str)
                    or not source_id.strip()
                    or source_id != claim["policy_id"]
                    or not _subsidy_parent_id_is_canonical(item)
                ):
                    return _terminal_resolution(AbstentionReason.NO_EVIDENCE)
                subsidy_evidence.append(item)
            else:
                law_evidence.append(item)
            evidence_ids.append(chunk_id)
        resolved.append((claim, tuple(subsidy_evidence), tuple(law_evidence)))

    unique_evidence_ids = tuple(dict.fromkeys(evidence_ids))
    resolved_law_evidence = [
        item for _, _, law_evidence in resolved for item in law_evidence
    ]
    law_pairs: dict[str, _LawPair] = {}
    for item in resolved_law_evidence:
        if _law_structure_reason(item) is not None:
            return _terminal_resolution(
                AbstentionReason.NO_EVIDENCE,
                evidence_chunk_ids=unique_evidence_ids,
            )
        try:
            law_pairs[item.chunk.chunk_id] = _law_pair(item)
        except ValueError:
            return _terminal_resolution(
                AbstentionReason.NO_EVIDENCE,
                evidence_chunk_ids=unique_evidence_ids,
            )

    for claim, _, law_evidence in resolved:
        required_sources = set(_required_source_pairs(claim))
        if any(
            law_pairs[item.chunk.chunk_id] not in required_sources
            for item in law_evidence
        ):
            return _terminal_resolution(
                AbstentionReason.NO_EVIDENCE,
                evidence_chunk_ids=unique_evidence_ids,
            )

    for _, subsidy_evidence, _ in resolved:
        for item in subsidy_evidence:
            if not chunk_matches_filter(
                item.chunk,
                MetadataFilter(source_type=SourceType.SUBSIDY, as_of=as_of),
            ):
                return _terminal_resolution(
                    AbstentionReason.STALE,
                    evidence_chunk_ids=unique_evidence_ids,
                )

    law_reason = _strict_law_evidence_reason(
        tuple(resolved_law_evidence), as_of=as_of
    )
    if law_reason is not None:
        return _terminal_resolution(
            law_reason,
            evidence_chunk_ids=unique_evidence_ids,
        )

    coverages: list[_ClaimCoverage] = []
    missing_documents: list[str] = []
    missing_laws: list[str] = []
    all_missing_aspects: set[str] = set()
    terminal_capability = False
    for claim, subsidy_evidence, law_evidence in resolved:
        claim_id = claim["claim_id"]
        required_sources = _required_source_pairs(claim)
        covered_sources = frozenset(
            law_pairs[item.chunk.chunk_id] for item in law_evidence
        )
        missing_sources = tuple(
            pair for pair in required_sources if pair not in covered_sources
        )
        document_supported = bool(
            _claim_status(claim) is EvidenceStatus.SUPPORTED
            and claim.get("reasons")
            and subsidy_evidence
        )
        if not document_supported:
            missing_documents.append(claim_id)

        aspects = claim.get("required_aspects", [])
        supported_aspects: set[str] = set()
        if (
            LEGAL_METADATA_ASPECT in aspects
            and required_sources
            and not missing_sources
        ):
            supported_aspects.add(LEGAL_METADATA_ASPECT)
        missing_aspects = frozenset(set(aspects) - supported_aspects)
        all_missing_aspects.update(missing_aspects)

        if claim["law_check_required"]:
            if not aspects:
                terminal_capability = True
            elif aspects == [LEGAL_METADATA_ASPECT]:
                if not required_sources:
                    terminal_capability = True
                elif missing_sources and document_supported:
                    missing_laws.append(claim_id)
            if missing_aspects & _UNSUPPORTED_LEGAL_ASPECTS:
                terminal_capability = True

        coverages.append(
            _ClaimCoverage(
                claim=claim,
                subsidy_chunks=subsidy_evidence,
                missing_sources=missing_sources,
            )
        )

    if terminal_capability:
        return _EvidenceResolution(
            claims=tuple(coverages),
            evidence_chunk_ids=unique_evidence_ids,
            missing_aspects=frozenset(all_missing_aspects),
            terminal_reason=AbstentionReason.NO_EVIDENCE,
        )
    return _EvidenceResolution(
        claims=tuple(coverages),
        evidence_chunk_ids=unique_evidence_ids,
        missing_document_claim_ids=tuple(missing_documents),
        missing_law_claim_ids=tuple(missing_laws),
        missing_aspects=frozenset(all_missing_aspects),
    )


def _decision(
    reason: AbstentionReason,
    *,
    missing_aspects: tuple[str, ...] = (),
) -> AbstentionDecision:
    return AbstentionDecision(True, reason, missing_aspects)


def _no_evidence_decision(resolution: _EvidenceResolution) -> AbstentionDecision:
    return decide_abstention(
        EvidenceState(
            evidence_chunk_ids=(
                resolution.evidence_chunk_ids
                if resolution.missing_aspects
                else ()
            ),
            required_aspects=resolution.missing_aspects,
            supported_aspects=frozenset(),
        )
    )


def _output(
    verdict: EvidenceGateVerdict,
    decision: AbstentionDecision,
    *,
    missing_document_claim_ids: list[str],
    missing_law_claim_ids: list[str],
    doc_retry_count: int,
    law_retry_count: int,
) -> dict[str, Any]:
    return {
        "evidence_gate_verdict": verdict,
        "abstention_decision": decision,
        "missing_document_claim_ids": missing_document_claim_ids,
        "missing_law_claim_ids": missing_law_claim_ids,
        "doc_retry_count": doc_retry_count,
        "law_retry_count": law_retry_count,
    }


def route_evidence_gate(state: GraphState) -> str:
    """Return the exact next node for an N7 verdict."""

    routes = {
        "insufficient_document": "document_verification",
        "insufficient_law": "targeted_law_search",
        "pass": "eligibility_verdict",
        "fail": "terminal",
    }
    try:
        return routes[state["evidence_gate_verdict"]]
    except (KeyError, TypeError):
        raise ValueError("missing or unknown evidence_gate_verdict") from None


def evaluate_evidence(state: GraphState) -> dict[str, Any]:
    """Validate every declared evidence ID and return an N7 partial update."""

    doc_retry_count = _retry_count(state, "doc_retry_count")
    law_retry_count = _retry_count(state, "law_retry_count")

    safety_blocked = state.get("safety_blocked")
    if type(safety_blocked) is not bool or safety_blocked:
        decision = decide_abstention(
            EvidenceState(evidence_chunk_ids=(), safety_blocked=True)
        )
        return _output(
            "fail",
            decision,
            missing_document_claim_ids=[],
            missing_law_claim_ids=[],
            doc_retry_count=doc_retry_count,
            law_retry_count=law_retry_count,
        )

    query_id = _query_id(state)
    if state.get("claim_plan") == []:
        return _output(
            "fail",
            decide_abstention(EvidenceState(evidence_chunk_ids=())),
            missing_document_claim_ids=[],
            missing_law_claim_ids=[],
            doc_retry_count=doc_retry_count,
            law_retry_count=law_retry_count,
        )
    claims = _claim_plan(state)
    subsidy_chunks = _retrieved_chunks(state, "subsidy_chunks")
    law_chunks = _retrieved_chunks(state, "law_chunks")

    if any(_claim_status(claim) is EvidenceStatus.CONFLICT for claim in claims):
        return _output(
            "fail",
            _decision(AbstentionReason.CONFLICT),
            missing_document_claim_ids=[],
            missing_law_claim_ids=[],
            doc_retry_count=doc_retry_count,
            law_retry_count=law_retry_count,
        )

    try:
        as_of = _as_of_date(state)
    except ValueError:
        return _output(
            "fail",
            _decision(AbstentionReason.STALE),
            missing_document_claim_ids=[],
            missing_law_claim_ids=[],
            doc_retry_count=doc_retry_count,
            law_retry_count=law_retry_count,
        )

    resolution = _resolve_evidence(
        query_id=query_id,
        claims=claims,
        subsidy_chunks=subsidy_chunks,
        law_chunks=law_chunks,
        as_of=as_of,
    )
    if resolution.terminal_reason is not None:
        if resolution.terminal_reason is AbstentionReason.NO_EVIDENCE:
            decision = _no_evidence_decision(resolution)
        else:
            decision = _decision(resolution.terminal_reason)
        return _output(
            "fail",
            decision,
            missing_document_claim_ids=[],
            missing_law_claim_ids=[],
            doc_retry_count=doc_retry_count,
            law_retry_count=law_retry_count,
        )

    missing_documents = list(resolution.missing_document_claim_ids)
    missing_laws = list(resolution.missing_law_claim_ids)
    if missing_documents:
        decision = _no_evidence_decision(resolution)
        if doc_retry_count == 0:
            doc_retry_count = 1
            verdict: EvidenceGateVerdict = "insufficient_document"
        else:
            verdict = "fail"
        return _output(
            verdict,
            decision,
            missing_document_claim_ids=missing_documents,
            missing_law_claim_ids=missing_laws,
            doc_retry_count=doc_retry_count,
            law_retry_count=law_retry_count,
        )

    if missing_laws:
        decision = _no_evidence_decision(resolution)
        if law_retry_count == 0:
            law_retry_count = 1
            verdict = "insufficient_law"
        else:
            verdict = "fail"
        return _output(
            verdict,
            decision,
            missing_document_claim_ids=[],
            missing_law_claim_ids=missing_laws,
            doc_retry_count=doc_retry_count,
            law_retry_count=law_retry_count,
        )

    decision = decide_abstention(
        EvidenceState(evidence_chunk_ids=resolution.evidence_chunk_ids)
    )
    return _output(
        "pass",
        decision,
        missing_document_claim_ids=[],
        missing_law_claim_ids=[],
        doc_retry_count=doc_retry_count,
        law_retry_count=law_retry_count,
    )


__all__ = ["evaluate_evidence", "route_evidence_gate"]
