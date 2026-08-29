"""Evidence-based abstention policy; retrieval score alone is never sufficient."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .chunking import (
    chunking_config_from_version,
    render_legal_metadata_chunk_texts,
)
from .contracts import (
    LEGAL_CONTENT_LEVEL,
    LEGAL_SECTION_HEADING,
    LEGAL_SECTION_TYPE,
    AbstentionReason,
    Chunk,
    SourceType,
)


LEGAL_METADATA_ASPECT = "legal_metadata"
LEGAL_ARTICLE_BODY_ASPECT = "legal_article_body"
LEGAL_INTERPRETATION_ASPECT = "legal_interpretation"


def supported_legal_evidence_aspects(chunks: Iterable[Chunk]) -> frozenset[str]:
    """Expose only catalog metadata from validated metadata-only legal chunks."""

    legal_chunks = tuple(chunks)
    if not legal_chunks:
        return frozenset()
    for chunk in legal_chunks:
        if (
            chunk.source_type is not SourceType.LAW
            or chunk.metadata.get("content_level") != LEGAL_CONTENT_LEVEL
            or chunk.heading_path != LEGAL_SECTION_HEADING
            or chunk.metadata.get("section_type") != LEGAL_SECTION_TYPE
            or chunk.citation_locator != LEGAL_SECTION_HEADING[0]
        ):
            return frozenset()
        try:
            config = chunking_config_from_version(
                str(chunk.metadata.get("chunking_version", ""))
            )
            expected_texts = render_legal_metadata_chunk_texts(chunk.metadata, config)
        except (TypeError, ValueError):
            return frozenset()
        part = chunk.metadata.get("chunk_part")
        part_count = chunk.metadata.get("chunk_part_count")
        if (
            not isinstance(part, int)
            or isinstance(part, bool)
            or part < 0
            or part >= len(expected_texts)
            or part_count != len(expected_texts)
            or chunk.text != expected_texts[part]
        ):
            return frozenset()
    return frozenset({LEGAL_METADATA_ASPECT})


@dataclass(frozen=True, slots=True)
class EvidenceState:
    """Evidence signals used to decide whether answer generation must abstain."""

    evidence_chunk_ids: tuple[str, ...]
    required_aspects: frozenset[str] = frozenset()
    supported_aspects: frozenset[str] = frozenset()
    conflict_detected: bool = False
    conflict_resolved: bool = False
    freshness_required: bool = False
    freshness_verified: bool = True
    safety_blocked: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "conflict_detected",
            "conflict_resolved",
            "freshness_required",
            "freshness_verified",
            "safety_blocked",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")
        if len(set(self.evidence_chunk_ids)) != len(self.evidence_chunk_ids):
            raise ValueError("evidence_chunk_ids must not contain duplicates")
        if self.conflict_resolved and not self.conflict_detected:
            raise ValueError("a conflict cannot be resolved unless one was detected")


@dataclass(frozen=True, slots=True)
class AbstentionDecision:
    """An abstention reason and any unsupported required aspects."""

    abstain: bool
    reason: AbstentionReason | None
    missing_aspects: tuple[str, ...] = ()


def decide_abstention(state: EvidenceState) -> AbstentionDecision:
    """Apply stable precedence: safety, no evidence/coverage, conflict, stale."""

    if state.safety_blocked:
        return AbstentionDecision(True, AbstentionReason.SAFETY)
    if not state.evidence_chunk_ids:
        return AbstentionDecision(True, AbstentionReason.NO_EVIDENCE)
    missing = tuple(sorted(state.required_aspects - state.supported_aspects))
    if missing:
        return AbstentionDecision(True, AbstentionReason.NO_EVIDENCE, missing)
    if state.conflict_detected and not state.conflict_resolved:
        return AbstentionDecision(True, AbstentionReason.CONFLICT)
    if state.freshness_required and not state.freshness_verified:
        return AbstentionDecision(True, AbstentionReason.STALE)
    return AbstentionDecision(False, None)
