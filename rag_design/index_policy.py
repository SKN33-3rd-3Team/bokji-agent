"""Logical index routing and portable metadata filter semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping

from .contracts import Chunk, SourceType


class QueryScope(str, Enum):
    SUBSIDY = "subsidy"
    LAW = "law"
    BOTH = "both"


LOGICAL_INDEXES: Mapping[SourceType, str] = {
    SourceType.SUBSIDY: "subsidy",
    SourceType.LAW: "law",
}
ALLOWED_CROSS_INDEX_MERGE = frozenset({"interleave", "reciprocal_rank"})


def route_indexes(scope: QueryScope) -> tuple[str, ...]:
    if scope is QueryScope.SUBSIDY:
        return (LOGICAL_INDEXES[SourceType.SUBSIDY],)
    if scope is QueryScope.LAW:
        return (LOGICAL_INDEXES[SourceType.LAW],)
    return (LOGICAL_INDEXES[SourceType.SUBSIDY], LOGICAL_INDEXES[SourceType.LAW])


def validate_cross_index_merge(strategy: str) -> None:
    if strategy == "raw_score":
        raise ValueError("raw scores from different logical indexes are not comparable")
    if strategy not in ALLOWED_CROSS_INDEX_MERGE:
        raise ValueError(f"unsupported cross-index merge strategy: {strategy}")


@dataclass(frozen=True, slots=True)
class MetadataFilter:
    source_type: SourceType
    as_of: date
    region_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not value.strip() for value in self.region_codes):
            raise ValueError("region_codes must contain non-empty strings")

    def to_portable_dict(self) -> dict[str, Any]:
        """Describe semantics without tying Gate 1 design to a Vector DB dialect."""

        result: dict[str, Any] = {
            "source_type": self.source_type.value,
            "effective_interval": {
                "contains": self.as_of.isoformat(),
                "bounds": "[from,to)",
            },
        }
        if self.region_codes and self.source_type is SourceType.SUBSIDY:
            result["region_codes_any"] = list(self.region_codes)
        return result


def chunk_matches_filter(chunk: Chunk, policy: MetadataFilter) -> bool:
    if chunk.source_type is not policy.source_type:
        return False

    effective_from = chunk.metadata.get("effective_from")
    effective_to = chunk.metadata.get("effective_to")
    if chunk.source_type is SourceType.LAW and not effective_from:
        return False
    try:
        if effective_from and policy.as_of < date.fromisoformat(str(effective_from)[:10]):
            return False
        # Effective intervals are half-open: valid at from, invalid at to.
        if effective_to and policy.as_of >= date.fromisoformat(str(effective_to)[:10]):
            return False
    except ValueError:
        return False

    if policy.source_type is SourceType.SUBSIDY and policy.region_codes:
        chunk_regions = set(chunk.metadata.get("region_codes") or ())
        if not chunk_regions:
            return False
        if "ALL" not in chunk_regions and not chunk_regions.intersection(
            policy.region_codes
        ):
            return False
    return True
