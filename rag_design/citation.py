"""Public citation construction, separate from authenticated collection URLs."""

from __future__ import annotations

from datetime import date
import re
from typing import Iterable

from .contracts import (
    LEGAL_CONTENT_LEVEL,
    LEGAL_SECTION_HEADING,
    LEGAL_SECTION_TYPE,
    Chunk,
    Citation,
    Document,
    LegalDocumentType,
    SourceType,
    is_canonical_date,
)
from .url_safety import contains_secret_value, sanitize_official_url


_DIGITS = re.compile(r"^[0-9]+$")


def sanitize_public_url(url: str, *, secret_values: Iterable[str] = ()) -> str:
    """Strip authentication query keys and reject any supplied actual secret value."""

    if contains_secret_value(url, secret_values):
        raise ValueError("public URL contains a configured secret value")
    sanitized = sanitize_official_url(url)
    if contains_secret_value(sanitized, secret_values):
        raise ValueError("sanitized public URL contains a configured secret value")
    return sanitized


def _compact_date(value: str) -> str:
    if not is_canonical_date(value):
        raise ValueError("legal citation dates must use canonical YYYY-MM-DD form")
    parsed = date.fromisoformat(value)
    return parsed.strftime("%Y%m%d")


def legal_citation_url(
    *,
    law_type: LegalDocumentType | str,
    source_sequence: str,
    effective_from: str | None,
    secret_values: Iterable[str] = (),
) -> str:
    """Build a public detail URL for one legal-information source subtype."""

    try:
        legal_type = LegalDocumentType(law_type)
    except (TypeError, ValueError) as exc:
        raise ValueError("law citations require a supported law_type") from exc
    if not isinstance(source_sequence, str) or not _DIGITS.fullmatch(
        source_sequence
    ):
        raise ValueError("law citations require a numeric source_sequence")

    if legal_type is LegalDocumentType.LAW:
        if not effective_from:
            raise ValueError("statute citations require effective_from")
        public_url = (
            "https://www.law.go.kr/LSW/lsInfoP.do?"
            f"lsiSeq={source_sequence}&efYd={_compact_date(effective_from)}"
        )
    elif legal_type is LegalDocumentType.ADMINISTRATIVE_RULE:
        public_url = (
            "https://www.law.go.kr/LSW/admRulInfoP.do?"
            f"admRulSeq={source_sequence}"
        )
    else:
        public_url = (
            "https://www.law.go.kr/LSW/ordinInfoP.do?"
            f"ordinSeq={source_sequence}"
        )
    return sanitize_public_url(public_url, secret_values=secret_values)


def citation_url_for_document(
    document: Document, *, secret_values: Iterable[str] = ()
) -> str:
    """Build a sanitized public citation URL for a legal or subsidy document."""

    if document.source_type is SourceType.LAW:
        if document.metadata.get("content_level") != LEGAL_CONTENT_LEVEL:
            raise ValueError("law citations require metadata_only content")
        return legal_citation_url(
            law_type=str(document.metadata.get("law_type", "")),
            source_sequence=document.metadata.get("source_sequence", ""),
            effective_from=document.effective_from,
            secret_values=secret_values,
        )

    candidate = str(document.metadata.get("public_detail_url", document.source_url))
    return sanitize_public_url(candidate, secret_values=secret_values)


def build_citation(
    chunk: Chunk,
    document: Document,
    *,
    secret_values: Iterable[str] = (),
) -> Citation:
    """Create a citation after verifying the chunk belongs to the document."""

    if chunk.doc_id != document.doc_id:
        raise ValueError("chunk and document IDs do not match")
    if chunk.source_type is not document.source_type:
        raise ValueError("chunk and document source types do not match")
    if document.source_type is SourceType.LAW and (
        chunk.metadata.get("content_level") != LEGAL_CONTENT_LEVEL
        or chunk.heading_path != LEGAL_SECTION_HEADING
        or chunk.metadata.get("section_type") != LEGAL_SECTION_TYPE
        or chunk.citation_locator != LEGAL_SECTION_HEADING[0]
    ):
        raise ValueError(
            "metadata-only legal citations require the 기본정보 locator"
        )
    return Citation(
        chunk_id=chunk.chunk_id,
        document_title=document.title,
        locator=chunk.citation_locator,
        source_url=citation_url_for_document(document, secret_values=secret_values),
    )
