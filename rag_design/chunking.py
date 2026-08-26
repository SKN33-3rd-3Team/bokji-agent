"""Deterministic, structure-first chunking for welfare services and statutes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

from .contracts import Chunk, Document, SCHEMA_VERSION, SourceType, compute_content_hash


CHUNKING_VERSION = "structure-v1"
_BOUNDARY_PATTERN = re.compile(r"(?<=\n)\s*\n+|(?<=[.!?。])\s+|(?<=다\.)\s+")


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_chars: int = 800
    overlap_chars: int = 100

    def __post_init__(self) -> None:
        if self.max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars must be non-negative")
        if self.overlap_chars >= self.max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")


def _split_with_overlap(text: str, limit: int, overlap: int) -> list[str]:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= limit:
        return [normalized]

    parts: list[str] = []
    start = 0
    while start < len(normalized):
        hard_end = min(start + limit, len(normalized))
        end = hard_end
        if hard_end < len(normalized):
            window = normalized[start:hard_end]
            boundaries = [match.end() for match in _BOUNDARY_PATTERN.finditer(window)]
            viable = [boundary for boundary in boundaries if boundary >= limit // 2]
            if viable:
                end = start + viable[-1]
        part = normalized[start:end].strip()
        if not part:
            raise ValueError("chunk splitting made no progress")
        parts.append(part)
        if end == len(normalized):
            break
        start = max(start + 1, end - overlap)
    return parts


def _config_version(config: ChunkingConfig) -> str:
    return (
        f"{CHUNKING_VERSION}:max_chars={config.max_chars}:"
        f"overlap_chars={config.overlap_chars}"
    )


def chunking_config_from_version(value: str) -> ChunkingConfig:
    prefix = f"{CHUNKING_VERSION}:max_chars="
    separator = ":overlap_chars="
    if not value.startswith(prefix) or separator not in value:
        raise ValueError("unsupported chunking_version")
    max_chars, overlap_chars = value[len(prefix) :].split(separator, maxsplit=1)
    try:
        return ChunkingConfig(int(max_chars), int(overlap_chars))
    except ValueError as exc:
        raise ValueError("invalid chunking_version parameters") from exc


def compute_chunk_id(
    document: Document,
    heading_path: tuple[str, ...],
    part: int,
    config_version: str,
) -> str:
    identity = "\x1f".join(
        (document.doc_id, *heading_path, str(part), config_version)
    )
    suffix = sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{document.doc_id}:chunk:{suffix}"


def _prefix(document: Document, heading_path: tuple[str, ...]) -> str:
    # A law chunk must remain meaningful when retrieved without its parent document.
    if document.source_type is SourceType.LAW:
        law_name = str(document.metadata.get("law_name", document.title))
        return f"{law_name}\n{' > '.join(heading_path)}"
    return f"{document.title}\n{' > '.join(heading_path)}"


def chunk_document(
    document: Document, config: ChunkingConfig = ChunkingConfig()
) -> tuple[Chunk, ...]:
    """Split only inside a section unless that section exceeds ``max_chars``.

    Law heading paths are copied verbatim, including 가지번호 and 조/항/호/목.
    Python's randomized ``hash()`` is intentionally not used for stable IDs.
    """

    if not document.sections:
        raise ValueError("structure-first chunking requires at least one section")

    chunks: list[Chunk] = []
    seen_ids: set[str] = set()
    ordinal = 0
    config_version = _config_version(config)
    for section in document.sections:
        prefix = _prefix(document, section.heading_path)
        body_limit = config.max_chars - len(prefix) - 2
        if body_limit < 50:
            raise ValueError("max_chars is too small for the document context prefix")
        body_parts = _split_with_overlap(
            section.content, body_limit, min(config.overlap_chars, body_limit - 1)
        )
        for part_index, body in enumerate(body_parts):
            text = f"{prefix}\n\n{body}"
            chunk_id = compute_chunk_id(
                document, section.heading_path, part_index, config_version
            )
            if chunk_id in seen_ids:
                raise ValueError("duplicate chunk_id generated from duplicate structure path")
            seen_ids.add(chunk_id)
            metadata = {
                "source_name": document.source_name,
                "source_id": document.source_id,
                "source_url": document.source_url,
                "source_updated_at": document.source_updated_at,
                "effective_from": document.effective_from,
                "effective_to": document.effective_to,
                "section_type": section.metadata.get("section_type"),
                "chunk_part": part_index,
                "chunk_part_count": len(body_parts),
                "chunking_version": config_version,
            }
            for key in ("organization", "region_codes", "service_category", "lsi_seq"):
                if key in document.metadata:
                    metadata[key] = document.metadata[key]
            chunks.append(
                Chunk(
                    schema_version=SCHEMA_VERSION,
                    chunk_id=chunk_id,
                    doc_id=document.doc_id,
                    source_type=document.source_type,
                    text=text,
                    heading_path=section.heading_path,
                    ordinal=ordinal,
                    citation_locator=" > ".join(section.heading_path),
                    content_hash=compute_content_hash(text),
                    metadata=metadata,
                )
            )
            ordinal += 1
    return tuple(chunks)
