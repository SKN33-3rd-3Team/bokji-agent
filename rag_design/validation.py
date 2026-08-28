"""Collection-to-RAG handoff checks for Gate 1 design artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Iterable, Mapping, Sequence

from .chunking import chunk_document, chunking_config_from_version, compute_chunk_id
from .citation import citation_url_for_document
from .contracts import (
    SCHEMA_VERSION,
    AnswerResult,
    Chunk,
    Document,
    EvidenceCheckResult,
    RetrievedChunk,
    SensitiveDataStatus,
    SourceType,
    compute_content_hash,
    compute_document_id,
    validate_region_metadata,
)
from .url_safety import contains_secret_value, sanitize_official_url


_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "source_type",
        "document_count",
        "collected_at",
        "source_dataset_url",
        "license",
        "excluded_count",
        "parse_error_count",
    }
)
_CARD_FIELDS = frozenset(
    {
        "source_type",
        "source_name",
        "source_dataset_url",
        "license",
        "document_count",
        "collection_scope",
        "cleaning_method",
        "chunking_method",
        "exclusion_criteria",
        "update_policy",
        "rights_reviewed",
        "sensitive_data_reviewed",
    }
)
_SUBSIDY_METADATA = frozenset({"organization", "region_scope", "service_category"})
_LAW_METADATA = frozenset(
    {"law_name", "lsi_seq", "promulgation_date", "effective_date", "revision_status"}
)
_FATAL_WARNING_PREFIXES = ("fatal:", "missing_content", "secret_detected")
_CHUNK_METADATA_FIELDS = frozenset(
    {
        "source_name",
        "source_id",
        "source_url",
        "source_updated_at",
        "effective_from",
        "effective_to",
        "section_type",
        "chunk_part",
        "chunk_part_count",
        "chunking_version",
    }
)
_ARTICLE_PATTERN = re.compile(r"^제\d+조(?:의\d+)?(?:\([^\r\n]+\))?$")
_PARAGRAPH_PATTERN = re.compile(r"^제\d+항$")
_ITEM_PATTERN = re.compile(r"^제\d+호$")
_SUBITEM_LABELS = frozenset(
    "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허"
    "고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후"
    "그느드르므브스으즈츠크트프트흐기니디리미비시이지치키티피히"
)
_LAW_HIERARCHY_TRANSITIONS = frozenset({(0, 1), (0, 2), (1, 2), (2, 3)})
_LAW_SECTION_TYPE_RANK = {
    "article": 0,
    "paragraph": 1,
    "item": 2,
    "subitem": 3,
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A machine-readable validation diagnostic tied to a contract path."""

    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class HandoffReport:
    """Blocking issues and non-blocking warnings for a collection handoff."""

    issues: tuple[ValidationIssue, ...]
    accepted_document_ids: tuple[str, ...]
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.issues

    def require_accepted(self) -> None:
        """Raise a compact error when the handoff contains a blocking issue."""

        if self.issues:
            summary = "; ".join(f"{issue.path}: {issue.code}" for issue in self.issues)
            raise ValueError(f"collection handoff rejected by blocking issues: {summary}")


def _issue(
    issues: list[ValidationIssue], code: str, path: str, message: str
) -> None:
    issues.append(ValidationIssue(code=code, path=path, message=message))


def _missing_or_blank(mapping: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for key in fields:
        if key not in mapping:
            missing.append(key)
            continue
        value = mapping[key]
        if (
            value is None
            or (isinstance(value, str) and not value.strip())
            or (
                isinstance(value, (list, tuple, set, frozenset, dict))
                and not value
            )
        ):
            missing.append(key)
    return sorted(missing)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_timezone_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not ("T" in value or " " in value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _contains_known_secret(value: Any, secret_values: Sequence[str]) -> bool:
    if isinstance(value, str):
        return contains_secret_value(value, secret_values)
    if isinstance(value, Mapping):
        return any(
            _contains_known_secret(key, secret_values)
            or _contains_known_secret(item, secret_values)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_known_secret(item, secret_values) for item in value)
    return False


def _law_heading_ranks(heading_path: tuple[str, ...]) -> tuple[int, ...] | None:
    """Map a valid 조·항·호·목 heading path to hierarchy ranks."""

    if not heading_path or not _ARTICLE_PATTERN.fullmatch(heading_path[0]):
        return None
    ranks: list[int] = [0]
    for heading in heading_path[1:]:
        if _PARAGRAPH_PATTERN.fullmatch(heading):
            ranks.append(1)
        elif _ITEM_PATTERN.fullmatch(heading):
            ranks.append(2)
        elif heading.endswith("목") and heading[:-1] in _SUBITEM_LABELS:
            ranks.append(3)
        else:
            return None
    if any(
        (left, right) not in _LAW_HIERARCHY_TRANSITIONS
        for left, right in zip(ranks, ranks[1:])
    ):
        return None
    return tuple(ranks)


def _validate_official_url(
    issues: list[ValidationIssue],
    value: Any,
    path: str,
    secret_values: Sequence[str],
) -> None:
    if not isinstance(value, str):
        _issue(issues, "invalid_url", path, "URL must be a string")
        return
    if contains_secret_value(value, secret_values):
        _issue(issues, "secret_exposure", path, "URL contains a configured secret")
        return
    try:
        sanitized = sanitize_official_url(value)
    except ValueError as exc:
        _issue(issues, "invalid_url", path, str(exc))
        return
    if sanitized != value:
        _issue(
            issues,
            "unsafe_url",
            path,
            "URL must be canonical HTTPS and contain no API/authentication parameters",
        )


def _validate_document(
    document: Document,
    index: int,
    issues: list[ValidationIssue],
    warnings: list[ValidationIssue],
    secret_values: Sequence[str],
) -> None:
    """Append document-level integrity, provenance, and structure issues."""

    path = f"documents[{index}]"
    if _contains_known_secret(document.to_dict(), secret_values):
        _issue(
            issues,
            "secret_exposure",
            path,
            "document contract contains a configured secret",
        )
    if document.content_hash != compute_content_hash(document.content):
        _issue(issues, "hash_mismatch", f"{path}.content_hash", "content hash differs")
    expected_doc_id = compute_document_id(
        source_type=document.source_type,
        source_id=document.source_id,
        source_updated_at=document.source_updated_at,
        effective_from=document.effective_from,
        content_hash=document.content_hash,
    )
    if document.doc_id != expected_doc_id:
        _issue(
            issues,
            "non_deterministic_doc_id",
            f"{path}.doc_id",
            "doc_id does not match the reference source/version identity",
        )
    if document.sensitive_data_status not in {
        SensitiveDataStatus.CLEAR,
        SensitiveDataStatus.REVIEWED,
    }:
        _issue(
            issues,
            "sensitive_data_not_cleared",
            f"{path}.sensitive_data_status",
            "document must be cleared or reviewed before indexing",
        )
    _validate_official_url(issues, document.source_url, f"{path}.source_url", secret_values)
    for warning in document.parse_warnings:
        if warning.casefold().startswith(_FATAL_WARNING_PREFIXES):
            _issue(
                issues,
                "fatal_parse_warning",
                f"{path}.parse_warnings",
                "document contains a fatal parse warning",
            )
    if not document.sections:
        _issue(issues, "missing_structure", f"{path}.sections", "no sections provided")
    comparable_content = " ".join(document.content.split())
    for section_index, section in enumerate(document.sections):
        if " ".join(section.content.split()) not in comparable_content:
            _issue(
                issues,
                "section_content_mismatch",
                f"{path}.sections[{section_index}].content",
                "section content is not traceable to the normalized document body",
            )

    required_metadata = (
        _SUBSIDY_METADATA if document.source_type is SourceType.SUBSIDY else _LAW_METADATA
    )
    for key in _missing_or_blank(document.metadata, required_metadata):
        _issue(
            issues,
            "missing_source_metadata",
            f"{path}.metadata.{key}",
            "required source-specific metadata is missing",
        )

    if document.source_type is SourceType.SUBSIDY:
        for key in ("organization", "service_category"):
            if not _is_non_empty_string(document.metadata.get(key)):
                _issue(
                    issues,
                    "invalid_source_metadata",
                    f"{path}.metadata.{key}",
                    "subsidy metadata value must be a non-empty string",
                )
        if "region_names" not in document.metadata:
            _issue(
                issues,
                "missing_source_metadata",
                f"{path}.metadata.region_names",
                "required source-specific metadata is missing",
            )
        if "region_scope" in document.metadata and "region_names" in document.metadata:
            try:
                validate_region_metadata(
                    document.metadata["region_scope"],
                    document.metadata["region_names"],
                )
            except ValueError as exc:
                _issue(
                    issues,
                    "invalid_region_metadata",
                    f"{path}.metadata",
                    str(exc),
                )
        public_detail_url = document.metadata.get("public_detail_url")
        if public_detail_url:
            _validate_official_url(
                issues,
                public_detail_url,
                f"{path}.metadata.public_detail_url",
                secret_values,
            )
        section_types = {
            section.metadata.get("section_type") for section in document.sections
        }
        for required in ("support_target", "support_details"):
            if required not in section_types:
                _issue(
                    issues,
                    "missing_subsidy_section",
                    f"{path}.sections",
                    f"required section_type {required!r} is missing",
                )
        if "application_method" not in section_types:
            _issue(
                warnings,
                "missing_recommended_subsidy_section",
                f"{path}.sections",
                "recommended section_type 'application_method' is missing",
            )
    else:
        for key in ("law_name", "lsi_seq", "revision_status"):
            if not _is_non_empty_string(document.metadata.get(key)):
                _issue(
                    issues,
                    "invalid_source_metadata",
                    f"{path}.metadata.{key}",
                    "law metadata value must be a non-empty string",
                )
        for key in ("promulgation_date", "effective_date"):
            if not _is_iso_date(document.metadata.get(key)):
                _issue(
                    issues,
                    "invalid_source_metadata",
                    f"{path}.metadata.{key}",
                    "law metadata date must be an ISO 8601 date string",
                )
        if not str(document.metadata.get("lsi_seq", "")).isdigit():
            _issue(
                issues,
                "invalid_source_metadata",
                f"{path}.metadata.lsi_seq",
                "lsi_seq must contain decimal digits",
            )
        if not document.effective_from:
            _issue(
                issues,
                "missing_effective_date",
                f"{path}.effective_from",
                "law documents require an effective date",
            )
        lsi_seq = str(document.metadata.get("lsi_seq", ""))
        metadata_effective_date = str(document.metadata.get("effective_date", ""))
        if lsi_seq != document.source_id or metadata_effective_date != document.effective_from:
            _issue(
                issues,
                "law_source_reference_mismatch",
                f"{path}.metadata",
                "law ID and effective date must agree across source fields",
            )
        try:
            expected_source_url = citation_url_for_document(document)
        except ValueError:
            expected_source_url = None
        if expected_source_url is None or document.source_url != expected_source_url:
            _issue(
                issues,
                "law_source_reference_mismatch",
                f"{path}.source_url",
                "law source URL must match its public law ID and effective date",
            )
        for section_index, section in enumerate(document.sections):
            heading_ranks = _law_heading_ranks(section.heading_path)
            if heading_ranks is None:
                _issue(
                    issues,
                    "missing_article_locator",
                    f"{path}.sections[{section_index}].heading_path",
                    "law section must retain an ordered 조·항·호·목 locator",
                )
                continue
            section_type = section.metadata.get("section_type")
            if _LAW_SECTION_TYPE_RANK.get(section_type) != heading_ranks[-1]:
                _issue(
                    issues,
                    "law_section_type_mismatch",
                    f"{path}.sections[{section_index}].metadata.section_type",
                    "law section_type must match the terminal locator level",
                )
            # Locators and body must follow source order, not merely exist somewhere.
            section_body = " ".join(section.content.split())
            search_from = 0
            locator_matches = True
            for locator in section.heading_path:
                normalized_locator = " ".join(locator.split())
                locator_position = comparable_content.find(
                    normalized_locator, search_from
                )
                if locator_position < 0:
                    locator_matches = False
                    break
                search_from = locator_position + len(normalized_locator)
            body_position = comparable_content.find(section_body, search_from)
            if not locator_matches or body_position < 0:
                _issue(
                    issues,
                    "law_locator_content_mismatch",
                    f"{path}.sections[{section_index}].heading_path",
                    "law article locator and section body are not ordered in the source body",
                )


def validate_collection_handoff(
    documents: Sequence[Document],
    manifest: Mapping[str, Any],
    document_card: Mapping[str, Any],
    *,
    secret_values: Iterable[str] = (),
) -> HandoffReport:
    """Validate a complete, public collection handoff without fetching external data."""

    secrets = tuple(str(value) for value in secret_values if value)
    issues: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    if _contains_known_secret(manifest, secrets):
        _issue(
            issues,
            "secret_exposure",
            "manifest",
            "manifest contains a configured secret",
        )
    if _contains_known_secret(document_card, secrets):
        _issue(
            issues,
            "secret_exposure",
            "document_card",
            "Document Card contains a configured secret",
        )
    for key in _missing_or_blank(manifest, _MANIFEST_FIELDS):
        _issue(issues, "missing_manifest_field", f"manifest.{key}", "field is required")
    for key in _missing_or_blank(document_card, _CARD_FIELDS):
        _issue(issues, "missing_card_field", f"document_card.{key}", "field is required")

    if manifest.get("schema_version") != SCHEMA_VERSION:
        _issue(
            issues,
            "schema_version_mismatch",
            "manifest.schema_version",
            f"expected {SCHEMA_VERSION}",
        )
    if "collected_at" in manifest and not _is_timezone_datetime(
        manifest.get("collected_at")
    ):
        _issue(
            issues,
            "invalid_collected_at",
            "manifest.collected_at",
            "collected_at must be an ISO 8601 datetime with timezone",
        )
    for prefix, record in (("manifest", manifest), ("document_card", document_card)):
        if "source_dataset_url" in record:
            _validate_official_url(
                issues,
                record["source_dataset_url"],
                f"{prefix}.source_dataset_url",
                secrets,
            )

    expected_count = len(documents)
    for prefix, record in (("manifest", manifest), ("document_card", document_card)):
        value = record.get("document_count")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _issue(
                issues,
                "invalid_count",
                f"{prefix}.document_count",
                "count must be a non-negative integer",
            )
    if manifest.get("document_count") != expected_count:
        _issue(issues, "count_mismatch", "manifest.document_count", "count differs")
    if document_card.get("document_count") != expected_count:
        _issue(issues, "count_mismatch", "document_card.document_count", "count differs")
    for field_name in ("rights_reviewed", "sensitive_data_reviewed"):
        if field_name in document_card and type(document_card[field_name]) is not bool:
            _issue(
                issues,
                "invalid_boolean",
                f"document_card.{field_name}",
                "value must be a JSON boolean",
            )
    if document_card.get("rights_reviewed") is not True:
        _issue(issues, "rights_not_reviewed", "document_card.rights_reviewed", "must be true")
    if document_card.get("sensitive_data_reviewed") is not True:
        _issue(
            issues,
            "sensitive_data_not_reviewed",
            "document_card.sensitive_data_reviewed",
            "must be true",
        )

    manifest_source = manifest.get("source_type")
    card_source = document_card.get("source_type")
    if manifest_source != card_source:
        _issue(issues, "source_type_mismatch", "document_card.source_type", "sources differ")
    if manifest_source not in {item.value for item in SourceType}:
        _issue(issues, "invalid_source_type", "manifest.source_type", "unsupported source")
    if manifest.get("source_dataset_url") != document_card.get("source_dataset_url"):
        _issue(
            issues,
            "dataset_url_mismatch",
            "document_card.source_dataset_url",
            "manifest and Document Card URLs differ",
        )
    if manifest.get("license") != document_card.get("license"):
        _issue(
            issues,
            "license_mismatch",
            "document_card.license",
            "manifest and Document Card licenses differ",
        )
    for count_field in ("excluded_count", "parse_error_count"):
        value = manifest.get(count_field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _issue(
                issues,
                "invalid_count",
                f"manifest.{count_field}",
                "count must be a non-negative integer",
            )

    seen_doc_ids: set[str] = set()
    seen_source_versions: set[tuple[str, str | None, str | None]] = set()
    source_ids_by_hash: dict[str, set[str]] = {}
    accepted_ids: list[str] = []
    for index, document in enumerate(documents):
        _validate_document(document, index, issues, warnings, secrets)
        if document.source_type.value != manifest_source:
            _issue(
                issues,
                "source_type_mismatch",
                f"documents[{index}].source_type",
                "document differs from manifest",
            )
        if document.source_name != document_card.get("source_name"):
            _issue(
                issues,
                "source_name_mismatch",
                f"documents[{index}].source_name",
                "document differs from Document Card",
            )
        if document.license != manifest.get("license"):
            _issue(
                issues,
                "license_mismatch",
                f"documents[{index}].license",
                "document differs from manifest",
            )
        if document.doc_id in seen_doc_ids:
            _issue(issues, "duplicate_doc_id", f"documents[{index}].doc_id", "duplicate")
        seen_doc_ids.add(document.doc_id)
        source_version = (
            document.source_id,
            document.source_updated_at,
            document.effective_from,
        )
        if source_version in seen_source_versions:
            _issue(
                issues,
                "duplicate_source_version",
                f"documents[{index}].source_id",
                "source ID and version dates duplicate another document",
            )
        seen_source_versions.add(source_version)
        source_ids = source_ids_by_hash.setdefault(document.content_hash, set())
        if source_ids:
            if document.source_id in source_ids:
                _issue(
                    issues,
                    "duplicate_content",
                    f"documents[{index}].content_hash",
                    "content duplicates another version of the same source",
                )
            else:
                _issue(
                    warnings,
                    "duplicate_content_candidate",
                    f"documents[{index}].content_hash",
                    "content matches a document from a different source_id",
                )
        source_ids.add(document.source_id)
        accepted_ids.append(document.doc_id)

    return HandoffReport(
        tuple(issues),
        tuple(accepted_ids) if not issues else (),
        tuple(warnings),
    )


def validate_chunk_batch(
    chunks: Sequence[Chunk],
    documents: Sequence[Document],
    *,
    secret_values: Iterable[str] = (),
) -> tuple[ValidationIssue, ...]:
    """Validate chunks against their accepted parent documents before embedding."""

    secrets = tuple(str(value) for value in secret_values if value)
    issues: list[ValidationIssue] = []
    parents: dict[str, Document] = {}
    for index, document in enumerate(documents):
        if document.doc_id in parents:
            _issue(
                issues,
                "duplicate_parent_doc_id",
                f"documents[{index}].doc_id",
                "parent document ID is duplicated",
            )
        parents[document.doc_id] = document

    seen_ids: set[str] = set()
    seen_positions: set[tuple[str, int]] = set()
    ordinals_by_document: dict[str, set[int]] = {}
    for index, chunk in enumerate(chunks):
        path = f"chunks[{index}]"
        if _contains_known_secret(chunk.to_dict(), secrets):
            _issue(
                issues,
                "secret_exposure",
                path,
                "chunk contract contains a configured secret",
            )
        if chunk.chunk_id in seen_ids:
            _issue(issues, "duplicate_chunk_id", f"{path}.chunk_id", "duplicate")
        seen_ids.add(chunk.chunk_id)
        position = (chunk.doc_id, chunk.ordinal)
        if position in seen_positions:
            _issue(
                issues,
                "duplicate_chunk_ordinal",
                f"{path}.ordinal",
                "ordinal duplicates another chunk in the document",
            )
        seen_positions.add(position)
        ordinals_by_document.setdefault(chunk.doc_id, set()).add(chunk.ordinal)
        if chunk.content_hash != compute_content_hash(chunk.text):
            _issue(issues, "hash_mismatch", f"{path}.content_hash", "text hash differs")

        for key in sorted(_CHUNK_METADATA_FIELDS - chunk.metadata.keys()):
            _issue(
                issues,
                "missing_chunk_metadata",
                f"{path}.metadata.{key}",
                "required parent or chunking metadata is missing",
            )
        for key in ("source_name", "source_id", "source_url", "section_type", "chunking_version"):
            value = chunk.metadata.get(key)
            if not isinstance(value, str) or not value.strip():
                _issue(
                    issues,
                    "invalid_chunk_metadata",
                    f"{path}.metadata.{key}",
                    "metadata value must be a non-empty string",
                )
        if "source_url" in chunk.metadata:
            _validate_official_url(
                issues,
                chunk.metadata["source_url"],
                f"{path}.metadata.source_url",
                secrets,
            )

        part = chunk.metadata.get("chunk_part")
        part_count = chunk.metadata.get("chunk_part_count")
        if (
            not isinstance(part, int)
            or isinstance(part, bool)
            or part < 0
            or not isinstance(part_count, int)
            or isinstance(part_count, bool)
            or part_count < 1
            or part >= part_count
        ):
            _issue(
                issues,
                "invalid_chunk_part",
                f"{path}.metadata",
                "chunk_part must identify one position inside chunk_part_count",
            )

        if chunk.source_type is SourceType.LAW:
            if not chunk.metadata.get("effective_from"):
                _issue(
                    issues,
                    "missing_effective_date",
                    f"{path}.metadata.effective_from",
                    "law chunks require an effective date",
                )
            if not chunk.metadata.get("lsi_seq"):
                _issue(
                    issues,
                    "missing_chunk_metadata",
                    f"{path}.metadata.lsi_seq",
                    "law chunks require lsi_seq",
                )
        else:
            for key in ("organization", "service_category"):
                if key not in chunk.metadata or not chunk.metadata[key]:
                    _issue(
                        issues,
                        "missing_chunk_metadata",
                        f"{path}.metadata.{key}",
                        "subsidy chunk metadata is missing",
                    )
            for key in ("region_scope", "region_names"):
                if key not in chunk.metadata:
                    _issue(
                        issues,
                        "missing_chunk_metadata",
                        f"{path}.metadata.{key}",
                        "subsidy chunk metadata is missing",
                    )
            if "region_scope" in chunk.metadata and "region_names" in chunk.metadata:
                try:
                    validate_region_metadata(
                        chunk.metadata["region_scope"],
                        chunk.metadata["region_names"],
                    )
                except ValueError as exc:
                    _issue(
                        issues,
                        "invalid_region_metadata",
                        f"{path}.metadata",
                        str(exc),
                    )

        parent = parents.get(chunk.doc_id)
        if parent is None:
            _issue(
                issues,
                "missing_parent_document",
                f"{path}.doc_id",
                "chunk has no accepted parent document",
            )
            continue
        if chunk.source_type is not parent.source_type:
            _issue(
                issues,
                "parent_source_type_mismatch",
                f"{path}.source_type",
                "chunk source type differs from its parent",
            )
        parent_metadata = {
            "source_name": parent.source_name,
            "source_id": parent.source_id,
            "source_url": parent.source_url,
            "source_updated_at": parent.source_updated_at,
            "effective_from": parent.effective_from,
            "effective_to": parent.effective_to,
        }
        for key in (
            "organization",
            "region_scope",
            "region_names",
            "service_category",
            "lsi_seq",
        ):
            if key in parent.metadata:
                parent_metadata[key] = parent.metadata[key]
        for key, expected in parent_metadata.items():
            if chunk.metadata.get(key) != expected:
                _issue(
                    issues,
                    "parent_metadata_mismatch",
                    f"{path}.metadata.{key}",
                    "chunk metadata differs from its parent document",
                )

        sections = [
            section
            for section in parent.sections
            if section.heading_path == chunk.heading_path
        ]
        if len(sections) != 1:
            _issue(
                issues,
                "parent_section_mismatch",
                f"{path}.heading_path",
                "chunk heading path must identify exactly one parent section",
            )
        elif chunk.metadata.get("section_type") != sections[0].metadata.get(
            "section_type"
        ):
            _issue(
                issues,
                "parent_section_mismatch",
                f"{path}.metadata.section_type",
                "chunk section type differs from its parent section",
            )
        if chunk.citation_locator != " > ".join(chunk.heading_path):
            _issue(
                issues,
                "citation_locator_mismatch",
                f"{path}.citation_locator",
                "citation locator differs from heading_path",
            )
        version = chunk.metadata.get("chunking_version")
        if isinstance(part, int) and not isinstance(part, bool) and isinstance(version, str):
            expected_chunk_id = compute_chunk_id(
                parent, chunk.heading_path, part, version
            )
            if chunk.chunk_id != expected_chunk_id:
                _issue(
                    issues,
                    "non_deterministic_chunk_id",
                    f"{path}.chunk_id",
                    "chunk ID does not match its parent, position and config version",
                )

    for doc_id, ordinals in ordinals_by_document.items():
        if sorted(ordinals) != list(range(len(ordinals))):
            _issue(
                issues,
                "non_contiguous_chunk_ordinals",
                f"chunks[{doc_id}].ordinal",
                "chunk ordinals must be contiguous from zero",
            )
    for doc_id, parent in parents.items():
        actual = sorted(
            (chunk for chunk in chunks if chunk.doc_id == doc_id),
            key=lambda chunk: chunk.ordinal,
        )
        if not actual:
            continue
        versions = {str(chunk.metadata.get("chunking_version", "")) for chunk in actual}
        if len(versions) != 1:
            _issue(
                issues,
                "mixed_chunking_versions",
                f"chunks[{doc_id}].metadata.chunking_version",
                "one parent batch must use exactly one chunking config version",
            )
            continue
        try:
            config = chunking_config_from_version(next(iter(versions)))
            expected = chunk_document(parent, config)
        except ValueError:
            _issue(
                issues,
                "invalid_chunking_version",
                f"chunks[{doc_id}].metadata.chunking_version",
                "chunking config version cannot be reproduced",
            )
            continue
        # Rebuild the batch to prove every stored chunk is reproducible.
        if [chunk.to_dict() for chunk in actual] != [
            chunk.to_dict() for chunk in expected
        ]:
            _issue(
                issues,
                "chunk_batch_not_reproducible",
                f"chunks[{doc_id}]",
                "chunk batch differs from deterministic parent transformation",
            )
    return tuple(issues)


def _validate_result_evidence(
    *,
    query_id: str,
    evidence_chunk_ids: Sequence[str],
    retrieved_chunks: Sequence[RetrievedChunk],
    path: str,
) -> list[ValidationIssue]:
    """Reject cross-query, duplicate, or non-retrieved evidence references."""

    issues: list[ValidationIssue] = []
    retrieved_ids: set[str] = set()
    for index, retrieved in enumerate(retrieved_chunks):
        if retrieved.query_id != query_id:
            _issue(
                issues,
                "query_id_mismatch",
                f"retrieved_chunks[{index}].query_id",
                "retrieved chunk belongs to a different query",
            )
        if retrieved.chunk.chunk_id in retrieved_ids:
            _issue(
                issues,
                "duplicate_retrieved_chunk",
                f"retrieved_chunks[{index}].chunk.chunk_id",
                "retrieved evidence is duplicated",
            )
        retrieved_ids.add(retrieved.chunk.chunk_id)
    if any(chunk_id not in retrieved_ids for chunk_id in evidence_chunk_ids):
        _issue(
            issues,
            "unretrieved_evidence",
            path,
            "declared evidence must come from the actual retrieval result",
        )
    return issues


def validate_answer_evidence(
    answer: AnswerResult,
    retrieved_chunks: Sequence[RetrievedChunk],
) -> tuple[ValidationIssue, ...]:
    """Ensure an answer cannot self-declare fabricated evidence or citations."""

    return tuple(
        _validate_result_evidence(
            query_id=answer.query_id,
            evidence_chunk_ids=answer.evidence_chunk_ids,
            retrieved_chunks=retrieved_chunks,
            path="answer.evidence_chunk_ids",
        )
    )


def validate_evidence_check_result(
    result: EvidenceCheckResult,
    retrieved_chunks: Sequence[RetrievedChunk],
) -> tuple[ValidationIssue, ...]:
    """Ensure a claim checker only cites chunks returned for the same query."""

    return tuple(
        _validate_result_evidence(
            query_id=result.query_id,
            evidence_chunk_ids=result.evidence_chunk_ids,
            retrieved_chunks=retrieved_chunks,
            path="evidence_check.evidence_chunk_ids",
        )
    )
