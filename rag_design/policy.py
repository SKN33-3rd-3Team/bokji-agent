"""Evidence-based abstention policy; retrieval score alone is never sufficient."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import AbstentionReason


@dataclass(frozen=True, slots=True)
class EvidenceState:
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
