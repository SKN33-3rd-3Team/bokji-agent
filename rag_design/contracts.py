"""Serializable, model-independent contracts shared with collection and UI teams."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from math import isfinite
import re
from typing import Any, Mapping
import unicodedata

from .url_safety import contains_secret_query_name, sanitize_official_url


SCHEMA_VERSION = "1.0"
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SourceType(str, Enum):
    SUBSIDY = "subsidy"
    LAW = "law"


class SensitiveDataStatus(str, Enum):
    CLEAR = "clear"
    REVIEWED = "reviewed"
    PENDING = "pending"
    BLOCKED = "blocked"


class AbstentionReason(str, Enum):
    NO_EVIDENCE = "no_evidence"
    CONFLICT = "conflict"
    STALE = "stale"
    SAFETY = "safety"


class EvidenceStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    CONFLICT = "conflict"
    NOT_APPLICABLE = "not_applicable"


def compute_content_hash(content: str) -> str:
    """Hash NFC-normalized content with platform-independent line endings."""

    canonical = unicodedata.normalize("NFC", content).replace("\r\n", "\n").replace("\r", "\n")
    return sha256(canonical.encode("utf-8")).hexdigest()


def compute_document_id(
    *,
    source_type: SourceType | str,
    source_id: str,
    source_updated_at: str | None,
    effective_from: str | None,
    content_hash: str,
) -> str:
    """Build the reference ID from stable source and version fields."""

    source_value = SourceType(source_type).value
    version = (
        effective_from
        if source_value == SourceType.LAW.value and effective_from
        else source_updated_at or effective_from or content_hash[:16]
    )
    return f"{source_value}:{source_id}:{version}"


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_schema_version(value: str) -> None:
    if value != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {value!r}; expected {SCHEMA_VERSION!r}"
        )


def _validate_hash(value: str, field_name: str) -> None:
    if not _HASH_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _validate_temporal(value: str | None, field_name: str, *, timezone: bool) -> None:
    if value is None:
        return
    _required(value, field_name)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be ISO 8601") from exc
        if timezone:
            raise ValueError(f"{field_name} must include a timezone")
        return
    if parsed.tzinfo is None and (
        timezone or "T" in value or " " in value
    ):
        raise ValueError(f"{field_name} must include a timezone")


@dataclass(frozen=True, slots=True)
class Section:
    """A source section with its hierarchical heading path preserved."""

    heading_path: tuple[str, ...]
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.heading_path or any(not item.strip() for item in self.heading_path):
            raise ValueError("section heading_path must contain non-empty headings")
        _required(self.content, "section.content")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Section":
        return cls(
            heading_path=tuple(value["heading_path"]),
            content=value["content"],
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading_path": list(self.heading_path),
            "content": self.content,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Document:
    """A normalized, versioned source document accepted by the RAG pipeline."""

    schema_version: str
    doc_id: str
    source_type: SourceType
    source_name: str
    source_id: str
    source_url: str
    title: str
    content: str
    sections: tuple[Section, ...]
    collected_at: str
    source_updated_at: str | None
    effective_from: str | None
    effective_to: str | None
    license: str
    content_hash: str
    metadata: Mapping[str, Any]
    parse_warnings: tuple[str, ...]
    sensitive_data_status: SensitiveDataStatus

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for field_name in ("doc_id", "source_name", "source_id", "title", "content", "license"):
            _required(getattr(self, field_name), field_name)
        sanitized = sanitize_official_url(self.source_url)
        if contains_secret_query_name(self.source_url) or sanitized != self.source_url:
            raise ValueError("source_url must already be a sanitized HTTPS official URL")
        _validate_temporal(self.collected_at, "collected_at", timezone=True)
        _validate_temporal(self.source_updated_at, "source_updated_at", timezone=False)
        _validate_temporal(self.effective_from, "effective_from", timezone=False)
        _validate_temporal(self.effective_to, "effective_to", timezone=False)
        if self.effective_from and self.effective_to:
            if date.fromisoformat(self.effective_to[:10]) <= date.fromisoformat(
                self.effective_from[:10]
            ):
                raise ValueError("effective_to must be later than effective_from")
        _validate_hash(self.content_hash, "content_hash")
        if any(not isinstance(warning, str) or not warning.strip() for warning in self.parse_warnings):
            raise ValueError("parse_warnings must contain non-empty strings")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Document":
        return cls(
            schema_version=value["schema_version"],
            doc_id=value["doc_id"],
            source_type=SourceType(value["source_type"]),
            source_name=value["source_name"],
            source_id=value["source_id"],
            source_url=value["source_url"],
            title=value["title"],
            content=value["content"],
            sections=tuple(Section.from_dict(item) for item in value.get("sections", ())),
            collected_at=value["collected_at"],
            source_updated_at=value.get("source_updated_at"),
            effective_from=value.get("effective_from"),
            effective_to=value.get("effective_to"),
            license=value["license"],
            content_hash=value["content_hash"],
            metadata=dict(value.get("metadata", {})),
            parse_warnings=tuple(value.get("parse_warnings", ())),
            sensitive_data_status=SensitiveDataStatus(value["sensitive_data_status"]),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_type"] = self.source_type.value
        result["sensitive_data_status"] = self.sensitive_data_status.value
        result["sections"] = [section.to_dict() for section in self.sections]
        result["parse_warnings"] = list(self.parse_warnings)
        result["metadata"] = dict(self.metadata)
        return result


@dataclass(frozen=True, slots=True)
class Chunk:
    """A deterministic retrieval unit derived from one document section."""

    schema_version: str
    chunk_id: str
    doc_id: str
    source_type: SourceType
    text: str
    heading_path: tuple[str, ...]
    ordinal: int
    citation_locator: str
    content_hash: str
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for field_name in ("chunk_id", "doc_id", "text", "citation_locator"):
            _required(getattr(self, field_name), field_name)
        if not self.heading_path or any(not item.strip() for item in self.heading_path):
            raise ValueError("heading_path must contain non-empty headings")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        _validate_hash(self.content_hash, "content_hash")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Chunk":
        return cls(
            schema_version=value["schema_version"],
            chunk_id=value["chunk_id"],
            doc_id=value["doc_id"],
            source_type=SourceType(value["source_type"]),
            text=value["text"],
            heading_path=tuple(value["heading_path"]),
            ordinal=int(value["ordinal"]),
            citation_locator=value["citation_locator"],
            content_hash=value["content_hash"],
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_type"] = self.source_type.value
        result["heading_path"] = list(self.heading_path)
        result["metadata"] = dict(self.metadata)
        return result


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A ranked retrieval result with score and index provenance."""

    query_id: str
    chunk: Chunk
    rank: int
    score: float
    score_type: str
    retriever_version: str
    index_name: str

    def __post_init__(self) -> None:
        for field_name in ("query_id", "score_type", "retriever_version", "index_name"):
            _required(getattr(self, field_name), field_name)
        if self.rank < 1:
            raise ValueError("rank must start at 1")
        if not isfinite(self.score):
            raise ValueError("score must be finite")
        if self.index_name not in {"subsidy", "law"}:
            raise ValueError("index_name must be a configured logical index")
        if self.index_name != self.chunk.source_type.value:
            raise ValueError("index_name must match the chunk source_type")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RetrievedChunk":
        return cls(
            query_id=value["query_id"],
            chunk=Chunk.from_dict(value["chunk"]),
            rank=int(value["rank"]),
            score=float(value["score"]),
            score_type=value["score_type"],
            retriever_version=value["retriever_version"],
            index_name=value["index_name"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "chunk": self.chunk.to_dict(),
            "rank": self.rank,
            "score": self.score,
            "score_type": self.score_type,
            "retriever_version": self.retriever_version,
            "index_name": self.index_name,
        }


@dataclass(frozen=True, slots=True)
class ClaimCheck:
    """The evidence verdict and supporting chunks for one answer claim."""

    claim_id: str
    status: EvidenceStatus
    evidence_chunk_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.claim_id, "claim_id")
        if len(set(self.evidence_chunk_ids)) != len(self.evidence_chunk_ids):
            raise ValueError("claim evidence_chunk_ids must not contain duplicates")
        if self.status in {
            EvidenceStatus.SUPPORTED,
            EvidenceStatus.PARTIAL,
            EvidenceStatus.CONFLICT,
        } and not self.evidence_chunk_ids:
            raise ValueError("supported, partial and conflict claim checks require evidence")
        if not self.reasons:
            raise ValueError("claim checks require at least one public reason")
        if any(not isinstance(reason, str) or not reason.strip() for reason in self.reasons):
            raise ValueError("claim reasons must contain non-empty strings")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ClaimCheck":
        return cls(
            claim_id=value["claim_id"],
            status=EvidenceStatus(value["status"]),
            evidence_chunk_ids=tuple(value.get("evidence_chunk_ids", ())),
            reasons=tuple(value.get("reasons", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "status": self.status.value,
            "evidence_chunk_ids": list(self.evidence_chunk_ids),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class EvidenceCheckResult:
    """The aggregate evidence verdict for all claims in one query."""

    query_id: str
    status: EvidenceStatus
    claim_checks: tuple[ClaimCheck, ...]
    evidence_chunk_ids: tuple[str, ...]
    checker_version: str

    def __post_init__(self) -> None:
        _required(self.query_id, "query_id")
        _required(self.checker_version, "checker_version")
        if len(set(self.evidence_chunk_ids)) != len(self.evidence_chunk_ids):
            raise ValueError("evidence_chunk_ids must not contain duplicates")
        if len({check.claim_id for check in self.claim_checks}) != len(self.claim_checks):
            raise ValueError("claim_checks must not contain duplicate claim IDs")
        evidence = set(self.evidence_chunk_ids)
        if any(
            chunk_id not in evidence
            for check in self.claim_checks
            for chunk_id in check.evidence_chunk_ids
        ):
            raise ValueError("claim checks must reference declared evidence chunks")
        # Keep the aggregate verdict consistent with every per-claim verdict.
        statuses = {check.status for check in self.claim_checks}
        if not self.claim_checks:
            raise ValueError("evidence checks require at least one claim check")
        if EvidenceStatus.CONFLICT in statuses and self.status is not EvidenceStatus.CONFLICT:
            raise ValueError("a conflicting claim requires overall conflict status")
        if self.status is EvidenceStatus.SUPPORTED:
            if not evidence or statuses != {EvidenceStatus.SUPPORTED}:
                raise ValueError(
                    "supported evidence checks require only supported claims and evidence"
                )
        elif self.status is EvidenceStatus.PARTIAL:
            if EvidenceStatus.SUPPORTED not in statuses or not (
                statuses
                & {
                    EvidenceStatus.PARTIAL,
                    EvidenceStatus.UNSUPPORTED,
                }
            ):
                raise ValueError(
                    "partial evidence checks require supported and incomplete claims"
                )
        elif self.status is EvidenceStatus.UNSUPPORTED:
            if statuses != {EvidenceStatus.UNSUPPORTED}:
                raise ValueError(
                    "unsupported evidence checks require only unsupported claims"
                )
        elif self.status is EvidenceStatus.CONFLICT:
            if EvidenceStatus.CONFLICT not in statuses:
                raise ValueError("conflict evidence checks require a conflicting claim")
        elif statuses != {EvidenceStatus.NOT_APPLICABLE}:
            raise ValueError(
                "not-applicable evidence checks require only not-applicable claims"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceCheckResult":
        return cls(
            query_id=value["query_id"],
            status=EvidenceStatus(value["status"]),
            claim_checks=tuple(
                ClaimCheck.from_dict(item) for item in value.get("claim_checks", ())
            ),
            evidence_chunk_ids=tuple(value.get("evidence_chunk_ids", ())),
            checker_version=value["checker_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "status": self.status.value,
            "claim_checks": [check.to_dict() for check in self.claim_checks],
            "evidence_chunk_ids": list(self.evidence_chunk_ids),
            "checker_version": self.checker_version,
        }


@dataclass(frozen=True, slots=True)
class Citation:
    """A public source reference for one evidence chunk."""

    chunk_id: str
    document_title: str
    locator: str
    source_url: str

    def __post_init__(self) -> None:
        for field_name in ("chunk_id", "document_title", "locator"):
            _required(getattr(self, field_name), field_name)
        sanitized = sanitize_official_url(self.source_url)
        if contains_secret_query_name(self.source_url) or sanitized != self.source_url:
            raise ValueError("citation source_url must already be sanitized")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Citation":
        return cls(**value)


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """A final answer, abstention, or error together with its provenance."""

    query_id: str
    answer: str
    abstained: bool
    abstention_reason: AbstentionReason | None
    citations: tuple[Citation, ...]
    evidence_chunk_ids: tuple[str, ...]
    latency_ms: int
    index_versions: tuple[str, ...]
    pipeline_version: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        _required(self.query_id, "query_id")
        _required(self.answer, "answer")
        _required(self.pipeline_version, "pipeline_version")
        if type(self.abstained) is not bool:
            raise ValueError("abstained must be a JSON boolean")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.abstained and self.abstention_reason is None:
            raise ValueError("abstained results require abstention_reason")
        if not self.abstained and self.abstention_reason is not None:
            raise ValueError("non-abstained results cannot have abstention_reason")
        if self.error_code and self.abstained:
            raise ValueError("pipeline errors must be distinct from evidence abstention")
        if not self.error_code and not self.abstained:
            if not self.evidence_chunk_ids or not self.citations:
                raise ValueError("answered results require evidence and citations")
        if not self.error_code and not self.index_versions:
            raise ValueError("non-error results require index_versions")
        evidence = set(self.evidence_chunk_ids)
        if any(citation.chunk_id not in evidence for citation in self.citations):
            raise ValueError("citations must reference an evidence chunk")
        if len(evidence) != len(self.evidence_chunk_ids):
            raise ValueError("evidence_chunk_ids must not contain duplicates")
        if len({citation.chunk_id for citation in self.citations}) != len(self.citations):
            raise ValueError("citations must not contain duplicate chunk IDs")
        if len(set(self.index_versions)) != len(self.index_versions):
            raise ValueError("index_versions must not contain duplicates")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AnswerResult":
        reason = value.get("abstention_reason")
        return cls(
            query_id=value["query_id"],
            answer=value["answer"],
            abstained=value["abstained"],
            abstention_reason=AbstentionReason(reason) if reason else None,
            citations=tuple(Citation.from_dict(item) for item in value.get("citations", ())),
            evidence_chunk_ids=tuple(value.get("evidence_chunk_ids", ())),
            latency_ms=int(value["latency_ms"]),
            index_versions=tuple(value.get("index_versions", ())),
            pipeline_version=value["pipeline_version"],
            error_code=value.get("error_code"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "answer": self.answer,
            "abstained": self.abstained,
            "abstention_reason": (
                self.abstention_reason.value if self.abstention_reason else None
            ),
            "citations": [asdict(citation) for citation in self.citations],
            "evidence_chunk_ids": list(self.evidence_chunk_ids),
            "latency_ms": self.latency_ms,
            "index_versions": list(self.index_versions),
            "pipeline_version": self.pipeline_version,
            "error_code": self.error_code,
        }
