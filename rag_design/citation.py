"""Public citation construction, separate from authenticated collection URLs."""

from __future__ import annotations

from datetime import date
import re
from typing import Iterable

from .contracts import Chunk, Citation, Document, SourceType
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
    parsed = date.fromisoformat(value[:10])
    return parsed.strftime("%Y%m%d")


def citation_url_for_document(
    document: Document, *, secret_values: Iterable[str] = ()
) -> str:
    if document.source_type is SourceType.LAW:
        lsi_seq = str(document.metadata.get("lsi_seq", ""))
        if not _DIGITS.fullmatch(lsi_seq):
            raise ValueError("law citations require a numeric lsi_seq")
        if not document.effective_from:
            raise ValueError("law citations require effective_from")
        public_url = (
            "https://www.law.go.kr/lsInfoP.do?"
            f"lsiSeq={lsi_seq}&efYd={_compact_date(document.effective_from)}"
        )
        return sanitize_public_url(public_url, secret_values=secret_values)

    candidate = str(document.metadata.get("public_detail_url", document.source_url))
    return sanitize_public_url(candidate, secret_values=secret_values)


def build_citation(
    chunk: Chunk,
    document: Document,
    *,
    secret_values: Iterable[str] = (),
) -> Citation:
    if chunk.doc_id != document.doc_id:
        raise ValueError("chunk and document IDs do not match")
    if chunk.source_type is not document.source_type:
        raise ValueError("chunk and document source types do not match")
    return Citation(
        chunk_id=chunk.chunk_id,
        document_title=document.title,
        locator=chunk.citation_locator,
        source_url=citation_url_for_document(document, secret_values=secret_values),
    )
