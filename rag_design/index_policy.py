"""Logical index routing and portable metadata filter semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping

from .contracts import (
    Chunk,
    RegionScope,
    SourceType,
    validate_region_metadata,
    validate_region_name,
)


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
    """Return the logical indexes selected for the requested query scope."""

    if scope is QueryScope.SUBSIDY:
        return (LOGICAL_INDEXES[SourceType.SUBSIDY],)
    if scope is QueryScope.LAW:
        return (LOGICAL_INDEXES[SourceType.LAW],)
    return (LOGICAL_INDEXES[SourceType.SUBSIDY], LOGICAL_INDEXES[SourceType.LAW])


def validate_cross_index_merge(strategy: str) -> None:
    """Reject unsupported or incomparable cross-index merge strategies."""

    if strategy == "raw_score":
        raise ValueError("raw scores from different logical indexes are not comparable")
    if strategy not in ALLOWED_CROSS_INDEX_MERGE:
        raise ValueError(f"unsupported cross-index merge strategy: {strategy}")


@dataclass(frozen=True, slots=True)
class MetadataFilter:
    """Portable source, effective-date, and region retrieval constraints."""

    source_type: SourceType
    as_of: date
    region_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.region_names and self.source_type is not SourceType.SUBSIDY:
            raise ValueError("region filters apply only to subsidy chunks")
        for value in self.region_names:
            validate_region_name(value)
        if len(set(self.region_names)) != len(self.region_names):
            raise ValueError("region_names must not contain duplicates")

    def to_portable_dict(self) -> dict[str, Any]:
        """Describe semantics without tying Gate 1 design to a Vector DB dialect."""

        result: dict[str, Any] = {
            "source_type": self.source_type.value,
            "effective_interval": {
                "contains": self.as_of.isoformat(),
                "bounds": "[from,to)",
            },
        }
        if self.region_names and self.source_type is SourceType.SUBSIDY:
            result["region_names_any"] = list(self.region_names)
        return result


def subsidy_regions_match(
    metadata: Mapping[str, Any], requested_region_names: tuple[str, ...]
) -> bool:
    """Apply exact-name matching with national wildcard and unknown fail-closed."""

    if not requested_region_names:
        return True
    try:
        validate_region_metadata(
            metadata.get("region_scope"),
            metadata.get("region_names"),
        )
    except ValueError:
        return False
    scope = RegionScope(metadata["region_scope"])
    if scope is RegionScope.NATIONAL:
        return True
    if scope is RegionScope.UNKNOWN:
        return False
    return bool(set(metadata["region_names"]).intersection(requested_region_names))


def chunk_matches_filter(chunk: Chunk, policy: MetadataFilter) -> bool:
    """Apply source, half-open effective-date, and subsidy-region filters."""

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

    if policy.source_type is SourceType.SUBSIDY and not subsidy_regions_match(
        chunk.metadata, policy.region_names
    ):
        return False
    return True
