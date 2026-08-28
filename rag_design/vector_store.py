"""Persistent Chroma index backed by the existing RAG contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .chunking import (
    chunking_config_from_version,
    render_legal_metadata_chunk_texts,
)
from .citation import legal_citation_url, sanitize_public_url
from .contracts import (
    LEGAL_CONTENT_LEVEL,
    LEGAL_METADATA_CONTRACT_VERSION,
    LEGAL_METADATA_FIELDS,
    LEGAL_SECTION_HEADING,
    LEGAL_SECTION_TYPE,
    Chunk,
    LegalDocumentType,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
    is_canonical_date,
    validate_region_metadata,
    validate_region_name,
)
from .embeddings import EmbeddingProvider
from .index_policy import MetadataFilter, chunk_matches_filter, subsidy_regions_match
from .url_safety import contains_credential_material


VECTOR_STORE_VERSION = "chroma-vector-store-v3"
_REGISTRY_VERSION = "atomic-active-generation-v1"
_COLLECTION_PREFIX_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,48}$")
_SCALAR = (str, int, float, bool)
_REQUIRED_CHUNK_METADATA = frozenset(
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


class VectorStoreError(RuntimeError):
    """Base error for explicit vector-store failures."""


class ChromaUnavailableError(VectorStoreError):
    pass


class CollectionNotFoundError(VectorStoreError):
    pass


class CollectionFingerprintMismatch(VectorStoreError):
    pass


class CorruptVectorRecordError(VectorStoreError):
    pass


@dataclass(frozen=True, slots=True)
class VectorStoreConfig:
    persist_directory: Path = Path(".runtime/vector_db")
    collection_prefix: str = "bokji_rag"
    distance_space: str = "cosine"
    batch_size: int = 500

    def __post_init__(self) -> None:
        object.__setattr__(self, "persist_directory", Path(self.persist_directory))
        if not _COLLECTION_PREFIX_PATTERN.fullmatch(self.collection_prefix):
            raise ValueError("collection_prefix must be 2-49 safe characters")
        if self.distance_space != "cosine":
            raise ValueError("the current RetrievedChunk adapter requires cosine distance")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")


@dataclass(frozen=True, slots=True)
class VectorSearchFilter:
    as_of: date | None = None
    region_names: tuple[str, ...] = ()
    metadata_equals: Mapping[str, str | int | float | bool] = field(
        default_factory=dict
    )
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        for name in self.region_names:
            validate_region_name(name)
        if len(set(self.region_names)) != len(self.region_names):
            raise ValueError("region_names must not contain duplicates")
        if self.snapshot_id is not None and not self.snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty")
        for key, value in self.metadata_equals.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("metadata filter keys must be non-empty strings")
            if not isinstance(value, _SCALAR):
                raise ValueError("metadata filter values must be scalar")


@dataclass(frozen=True, slots=True)
class SnapshotSyncResult:
    source_type: SourceType
    snapshot_id: str
    collection_name: str
    collection_fingerprint: str
    upserted_count: int
    deleted_count: int
    total_count: int


@dataclass(frozen=True, slots=True)
class SnapshotDeleteResult:
    source_type: SourceType
    snapshot_id: str
    deleted_count: int


@dataclass(frozen=True, slots=True)
class _ActiveGeneration:
    registry: Any
    collection: Any
    snapshot_id: str
    snapshot_digest: str
    chunking_version: str
    collection_fingerprint: str
    expected_count: int


def _load_chroma():
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as exc:
        raise ChromaUnavailableError(
            "chromadb is required; install requirements-vector.txt"
        ) from exc
    return chromadb, Settings


def _canonical_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class ChromaVectorStore:
    """Source-separated persistent collections with atomic snapshot promotion.

    A complete content-addressed generation is staged and verified before the
    registry pointer changes, so failed writes leave the active snapshot visible.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        config: VectorStoreConfig | None = None,
    ) -> None:
        if not isinstance(embedding_provider, EmbeddingProvider):
            raise TypeError("embedding_provider does not satisfy the provider contract")
        if embedding_provider.dimension < 1:
            raise ValueError("embedding provider dimension must be positive")
        self.embedding_provider = embedding_provider
        self.config = config or VectorStoreConfig()
        self.config.persist_directory.mkdir(parents=True, exist_ok=True)
        chromadb, settings_type = _load_chroma()
        try:
            self._client = chromadb.PersistentClient(
                path=str(self.config.persist_directory),
                settings=settings_type(anonymized_telemetry=False),
            )
        except Exception as exc:
            raise VectorStoreError("failed to open persistent Chroma storage") from exc

    def _registry_name(self, source_type: SourceType) -> str:
        return f"{self.config.collection_prefix}_{source_type.value}_registry"

    def _generation_name(
        self, source_type: SourceType, snapshot_digest: str
    ) -> str:
        return (
            f"{self.config.collection_prefix}_{source_type.value}_generation_"
            f"{snapshot_digest}"
        )

    def _base_payload(self, source_type: SourceType) -> dict[str, Any]:
        payload = {
            "storage_version": VECTOR_STORE_VERSION,
            "contract_schema_version": SCHEMA_VERSION,
            "layout": "source-separated-atomic-generations-v1",
            "source_type": source_type.value,
            "distance_space": self.config.distance_space,
            "embedding_provider": self.embedding_provider.provider_id,
            "embedding_dimension": self.embedding_provider.dimension,
        }
        if source_type is SourceType.LAW:
            payload["legal_contract_version"] = LEGAL_METADATA_CONTRACT_VERSION
        return payload

    def _base_fingerprint(self, source_type: SourceType) -> str:
        return _canonical_fingerprint(self._base_payload(source_type))

    def _collection_fingerprint(
        self, source_type: SourceType, chunking_version: str
    ) -> str:
        payload = self._base_payload(source_type)
        payload["chunking_version"] = chunking_version
        return _canonical_fingerprint(payload)

    def _collection_metadata(
        self, source_type: SourceType, chunking_version: str
    ) -> dict[str, str | int]:
        metadata: dict[str, str | int] = {
            "hnsw:space": self.config.distance_space,
            "rag_storage_version": VECTOR_STORE_VERSION,
            "rag_schema_version": SCHEMA_VERSION,
            "rag_source_type": source_type.value,
            "rag_embedding_provider": self.embedding_provider.provider_id,
            "rag_embedding_dimension": self.embedding_provider.dimension,
            "rag_chunking_version": chunking_version,
            "rag_config_fingerprint": self._base_fingerprint(source_type),
            "rag_collection_fingerprint": self._collection_fingerprint(
                source_type, chunking_version
            ),
        }
        if source_type is SourceType.LAW:
            metadata["rag_legal_contract_version"] = (
                LEGAL_METADATA_CONTRACT_VERSION
            )
        return metadata

    def _registry_metadata(self, source_type: SourceType) -> dict[str, str | int]:
        metadata: dict[str, str | int] = {
            "rag_registry_version": _REGISTRY_VERSION,
            "rag_storage_version": VECTOR_STORE_VERSION,
            "rag_schema_version": SCHEMA_VERSION,
            "rag_source_type": source_type.value,
            "rag_embedding_provider": self.embedding_provider.provider_id,
            "rag_embedding_dimension": self.embedding_provider.dimension,
            "rag_config_fingerprint": self._base_fingerprint(source_type),
        }
        if source_type is SourceType.LAW:
            metadata["rag_legal_contract_version"] = (
                LEGAL_METADATA_CONTRACT_VERSION
            )
        return metadata

    def _validate_registry(self, registry: Any, source_type: SourceType) -> None:
        metadata = registry.metadata or {}
        expected = self._registry_metadata(source_type)
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise CollectionFingerprintMismatch(
                "active-generation registry does not match the active configuration"
            )

    def _get_or_create_registry(self, source_type: SourceType) -> Any:
        try:
            registry = self._client.get_or_create_collection(
                name=self._registry_name(source_type),
                metadata=self._registry_metadata(source_type),
                embedding_function=None,
            )
        except Exception as exc:
            raise VectorStoreError(
                "failed to create or open the active-generation registry"
            ) from exc
        self._validate_registry(registry, source_type)
        return registry

    def _get_registry(self, source_type: SourceType) -> Any:
        try:
            registry = self._client.get_collection(
                name=self._registry_name(source_type), embedding_function=None
            )
        except Exception as exc:
            raise CollectionNotFoundError(
                f"collection for {source_type.value!r} does not exist"
            ) from exc
        self._validate_registry(registry, source_type)
        return registry

    def _validate_collection(
        self,
        collection: Any,
        source_type: SourceType,
        *,
        expected_chunking_version: str | None = None,
        expected_fingerprint: str | None = None,
    ) -> tuple[str, str]:
        metadata = collection.metadata or {}
        chunking_version = metadata.get("rag_chunking_version")
        if not isinstance(chunking_version, str) or not chunking_version:
            raise CollectionFingerprintMismatch("collection lacks a chunking version")
        actual_config = metadata.get("rag_config_fingerprint")
        actual_collection = metadata.get("rag_collection_fingerprint")
        calculated_config = self._base_fingerprint(source_type)
        calculated_collection = self._collection_fingerprint(
            source_type, chunking_version
        )
        expected_metadata = self._collection_metadata(source_type, chunking_version)
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise CollectionFingerprintMismatch(
                "collection metadata does not match the active configuration"
            )
        if actual_config != calculated_config or actual_collection != calculated_collection:
            raise CollectionFingerprintMismatch(
                "collection configuration does not match the active contract/provider"
            )
        if expected_chunking_version and chunking_version != expected_chunking_version:
            raise CollectionFingerprintMismatch(
                "collection chunking version differs from the incoming snapshot"
            )
        if expected_fingerprint and actual_collection != expected_fingerprint:
            raise CollectionFingerprintMismatch(
                "collection fingerprint differs from the caller expectation"
            )
        return chunking_version, str(actual_collection)

    def _active_generation(self, source_type: SourceType) -> _ActiveGeneration:
        registry = self._get_registry(source_type)
        registry_metadata = registry.metadata or {}
        active_name = registry_metadata.get("rag_active_collection")
        snapshot_id = registry_metadata.get("rag_active_snapshot_id")
        snapshot_digest = registry_metadata.get("rag_active_snapshot_digest")
        chunking_version = registry_metadata.get("rag_active_chunking_version")
        collection_fingerprint = registry_metadata.get(
            "rag_active_collection_fingerprint"
        )
        expected_count = registry_metadata.get("rag_active_expected_count")
        if active_name is None:
            raise CollectionNotFoundError(
                f"collection for {source_type.value!r} has no active snapshot"
            )
        if (
            not isinstance(active_name, str)
            or not isinstance(snapshot_id, str)
            or not snapshot_id
            or not isinstance(snapshot_digest, str)
            or len(snapshot_digest) != 64
            or active_name != self._generation_name(source_type, snapshot_digest)
            or not isinstance(chunking_version, str)
            or not chunking_version
            or not isinstance(collection_fingerprint, str)
            or not isinstance(expected_count, int)
            or isinstance(expected_count, bool)
            or expected_count < 0
        ):
            raise CollectionFingerprintMismatch(
                "active-generation registry contains an invalid pointer"
            )
        try:
            collection = self._client.get_collection(
                name=active_name, embedding_function=None
            )
        except Exception as exc:
            raise CollectionFingerprintMismatch(
                "active-generation registry points to a missing collection"
            ) from exc
        self._validate_collection(
            collection,
            source_type,
            expected_chunking_version=chunking_version,
            expected_fingerprint=collection_fingerprint,
        )
        metadata = collection.metadata or {}
        if (
            metadata.get("rag_snapshot_id") != snapshot_id
            or metadata.get("rag_snapshot_digest") != snapshot_digest
            or metadata.get("rag_expected_count") != expected_count
        ):
            raise CollectionFingerprintMismatch(
                "active generation does not match its registry pointer"
            )
        try:
            actual_count = collection.count()
        except Exception as exc:
            raise VectorStoreError("failed to validate the active generation") from exc
        if actual_count != expected_count:
            raise CorruptVectorRecordError(
                "active generation count differs from its completion marker"
            )
        return _ActiveGeneration(
            registry=registry,
            collection=collection,
            snapshot_id=snapshot_id,
            snapshot_digest=snapshot_digest,
            chunking_version=chunking_version,
            collection_fingerprint=collection_fingerprint,
            expected_count=expected_count,
        )

    def _get_collection(self, source_type: SourceType) -> Any:
        return self._active_generation(source_type).collection

    def collection_fingerprint(self, source_type: SourceType) -> str:
        return self._active_generation(source_type).collection_fingerprint

    def _record_metadata(self, chunk: Chunk, snapshot_id: str) -> dict[str, Any]:
        """Store the full contract plus scalar copies used by Chroma filters."""

        record: dict[str, Any] = {
            "chunk_json": json.dumps(
                chunk.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            "snapshot_id": snapshot_id,
            "source_type": chunk.source_type.value,
            "doc_id": chunk.doc_id,
            "ordinal": chunk.ordinal,
        }
        for key, value in chunk.metadata.items():
            if isinstance(value, _SCALAR) and value is not None:
                record[f"meta__{key}"] = value
        return record

    def _validate_chunk_contracts(
        self, chunks: Sequence[Chunk], *, secret_values: Iterable[str] = ()
    ) -> None:
        """Validate the complete snapshot before any embedding or write begins."""

        secrets = tuple(str(value) for value in secret_values if value)
        positions: set[tuple[str, int]] = set()
        ordinals: dict[str, set[int]] = {}
        parts: dict[tuple[str, tuple[str, ...]], list[tuple[int, int]]] = {}
        for index, chunk in enumerate(chunks):
            path = f"chunks[{index}]"
            if contains_credential_material(chunk.to_dict(), secrets):
                raise ValueError(
                    f"{path} contains a configured secret or authentication query name"
                )
            if chunk.content_hash != compute_content_hash(chunk.text):
                raise ValueError(f"{path}.content_hash does not match chunk text")
            missing = sorted(_REQUIRED_CHUNK_METADATA - chunk.metadata.keys())
            if missing:
                raise ValueError(
                    f"{path}.metadata is missing required field {missing[0]!r}"
                )
            for key in (
                "source_name",
                "source_id",
                "source_url",
                "section_type",
                "chunking_version",
            ):
                value = chunk.metadata.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"{path}.metadata.{key} must be a non-empty string"
                    )
            source_url = str(chunk.metadata["source_url"])
            try:
                sanitized_source_url = sanitize_public_url(
                    source_url, secret_values=secrets
                )
            except ValueError as exc:
                raise ValueError(
                    f"{path}.metadata.source_url is not a safe public URL"
                ) from exc
            if sanitized_source_url != source_url:
                raise ValueError(
                    f"{path}.metadata.source_url must be canonical, public, and credential-free"
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
                raise ValueError(f"{path}.metadata has an invalid chunk part")
            if chunk.citation_locator != " > ".join(chunk.heading_path):
                raise ValueError(f"{path}.citation_locator differs from heading_path")
            version = str(chunk.metadata["chunking_version"])
            try:
                chunking_config_from_version(version)
            except ValueError as exc:
                raise ValueError(
                    f"{path}.metadata.chunking_version is unsupported"
                ) from exc
            identity = "\x1f".join(
                (chunk.doc_id, *chunk.heading_path, str(part), version)
            )
            expected_id = (
                f"{chunk.doc_id}:chunk:"
                f"{sha256(identity.encode('utf-8')).hexdigest()[:20]}"
            )
            if chunk.chunk_id != expected_id:
                raise ValueError(f"{path}.chunk_id is not deterministic")
            position = (chunk.doc_id, chunk.ordinal)
            if position in positions:
                raise ValueError(f"{path}.ordinal duplicates a document position")
            positions.add(position)
            ordinals.setdefault(chunk.doc_id, set()).add(chunk.ordinal)
            parts.setdefault((chunk.doc_id, chunk.heading_path), []).append(
                (part, part_count)
            )

            for key in ("source_updated_at", "effective_from", "effective_to"):
                value = chunk.metadata.get(key)
                if value is not None and (
                    not isinstance(value, str) or not value.strip()
                ):
                    raise ValueError(
                        f"{path}.metadata.{key} must be null or a non-empty string"
                    )

            if chunk.source_type is SourceType.LAW:
                for key in LEGAL_METADATA_FIELDS:
                    if key not in chunk.metadata:
                        raise ValueError(
                            f"{path}.metadata.{key} is required for legal chunks"
                        )
                effective_from = chunk.metadata.get("effective_from")
                if not is_canonical_date(effective_from):
                    raise ValueError(
                        f"{path}.metadata.effective_from must use canonical YYYY-MM-DD form"
                    )
                source_updated_at = chunk.metadata.get("source_updated_at")
                if source_updated_at is not None and not is_canonical_date(
                    source_updated_at
                ):
                    raise ValueError(
                        f"{path}.metadata.source_updated_at must use canonical YYYY-MM-DD form"
                    )
                effective_to = chunk.metadata.get("effective_to")
                if effective_to is not None:
                    if not is_canonical_date(effective_to):
                        raise ValueError(
                            f"{path}.metadata.effective_to must use canonical YYYY-MM-DD form"
                        )
                    if date.fromisoformat(effective_to) <= date.fromisoformat(
                        effective_from
                    ):
                        raise ValueError(
                            f"{path}.metadata.effective_to must be later than effective_from"
                        )
                if chunk.metadata.get("content_level") != LEGAL_CONTENT_LEVEL:
                    raise ValueError(
                        f"{path}.metadata.content_level must be metadata_only"
                    )
                try:
                    legal_type = LegalDocumentType(chunk.metadata.get("law_type"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{path}.metadata.law_type is unsupported"
                    ) from exc
                for key in (
                    "document_kind",
                    "law_name",
                    "organization",
                    "revision_type",
                    "source_sequence",
                ):
                    value = chunk.metadata.get(key)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(
                            f"{path}.metadata.{key} must be a non-empty string"
                        )
                source_id = chunk.metadata.get("source_id")
                source_sequence = chunk.metadata.get("source_sequence")
                if (
                    not isinstance(source_id, str)
                    or not source_id.isascii()
                    or not source_id.isdigit()
                ):
                    raise ValueError(
                        f"{path}.metadata.source_id must contain decimal digits"
                    )
                if (
                    not isinstance(source_sequence, str)
                    or not source_sequence.isascii()
                    or not source_sequence.isdigit()
                ):
                    raise ValueError(
                        f"{path}.metadata.source_sequence must contain decimal digits"
                    )
                for key in ("issued_date", "effective_date"):
                    value = chunk.metadata.get(key)
                    if not is_canonical_date(value):
                        raise ValueError(
                            f"{path}.metadata.{key} must use canonical YYYY-MM-DD form"
                        )
                if chunk.metadata.get("effective_date") != effective_from:
                    raise ValueError(
                        f"{path}.metadata.effective_date differs from effective_from"
                    )
                if (
                    chunk.heading_path != LEGAL_SECTION_HEADING
                    or chunk.metadata.get("section_type") != LEGAL_SECTION_TYPE
                    or chunk.citation_locator != LEGAL_SECTION_HEADING[0]
                ):
                    raise ValueError(
                        f"{path} is not a metadata-only basic-info legal chunk"
                    )
                expected_doc_id = (
                    f"law:{legal_type.value}:{source_id}:{source_sequence}:"
                    f"{effective_from}"
                )
                if chunk.doc_id != expected_doc_id:
                    raise ValueError(
                        f"{path}.doc_id is not a deterministic legal revision ID"
                    )
                expected_source_url = legal_citation_url(
                    law_type=legal_type,
                    source_sequence=source_sequence,
                    effective_from=effective_from,
                    secret_values=secrets,
                )
                if source_url != expected_source_url:
                    raise ValueError(
                        f"{path}.metadata.source_url differs from its legal subtype "
                        "and source sequence"
                    )
                config = chunking_config_from_version(
                    str(chunk.metadata["chunking_version"])
                )
                expected_texts = render_legal_metadata_chunk_texts(
                    chunk.metadata, config
                )
                if (
                    part >= len(expected_texts)
                    or part_count != len(expected_texts)
                    or chunk.text != expected_texts[part]
                ):
                    raise ValueError(
                        f"{path}.text is not the canonical legal metadata summary"
                    )
            else:
                for key in ("organization", "service_category"):
                    value = chunk.metadata.get(key)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(
                            f"{path}.metadata.{key} is required for subsidy chunks"
                        )
                try:
                    validate_region_metadata(
                        chunk.metadata.get("region_scope"),
                        chunk.metadata.get("region_names"),
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"{path}.metadata has invalid subsidy region fields"
                    ) from exc

            try:
                json.dumps(chunk.to_dict(), ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path} is not JSON serializable") from exc

        for doc_id, values in ordinals.items():
            if sorted(values) != list(range(len(values))):
                raise ValueError(
                    f"chunks for {doc_id!r} must have contiguous ordinals from zero"
                )
        for (doc_id, heading_path), values in parts.items():
            declared_counts = {part_count for _, part_count in values}
            declared_count = next(iter(declared_counts))
            actual_parts = {part for part, _ in values}
            if (
                len(declared_counts) != 1
                or len(actual_parts) != len(values)
                or actual_parts != set(range(declared_count))
            ):
                raise ValueError(
                    "chunk parts must completely cover one section for "
                    f"{doc_id!r} at {' > '.join(heading_path)!r}"
                )

    def _snapshot_digest(
        self,
        source_type: SourceType,
        chunks: Sequence[Chunk],
        snapshot_id: str,
        chunking_version: str,
    ) -> str:
        """Build an order-independent digest for idempotency and generation naming."""

        return _canonical_fingerprint(
            {
                "source_type": source_type.value,
                "snapshot_id": snapshot_id,
                "chunking_version": chunking_version,
                "collection_fingerprint": self._collection_fingerprint(
                    source_type, chunking_version
                ),
                "chunks": [
                    chunk.to_dict()
                    for chunk in sorted(chunks, key=lambda item: item.chunk_id)
                ],
            }
        )

    def _generation_metadata(
        self,
        source_type: SourceType,
        chunking_version: str,
        snapshot_id: str,
        snapshot_digest: str,
        expected_count: int,
    ) -> dict[str, str | int]:
        return {
            **self._collection_metadata(source_type, chunking_version),
            "rag_snapshot_id": snapshot_id,
            "rag_snapshot_digest": snapshot_digest,
            "rag_expected_count": expected_count,
        }

    def _read_records(
        self, collection: Any
    ) -> dict[str, tuple[str, Mapping[str, Any]]]:
        try:
            result = collection.get(include=["metadatas", "documents"])
        except Exception as exc:
            raise VectorStoreError("failed to read a Chroma generation") from exc
        ids = list(result.get("ids") or [])
        documents = list(result.get("documents") or [])
        metadatas = list(result.get("metadatas") or [])
        if not (len(ids) == len(documents) == len(metadatas)):
            raise CorruptVectorRecordError(
                "Chroma generation returned inconsistent record arrays"
            )
        records: dict[str, tuple[str, Mapping[str, Any]]] = {}
        for record_id, document, metadata in zip(ids, documents, metadatas):
            if (
                not isinstance(record_id, str)
                or not isinstance(document, str)
                or not isinstance(metadata, Mapping)
                or record_id in records
            ):
                raise CorruptVectorRecordError(
                    "Chroma generation contains an invalid record"
                )
            self._decode_chunk(record_id, document, metadata)
            records[record_id] = (document, metadata)
        return records

    def _verify_generation(
        self,
        collection: Any,
        source_type: SourceType,
        chunks: Sequence[Chunk],
        snapshot_id: str,
        snapshot_digest: str,
        chunking_version: str,
    ) -> None:
        fingerprint = self._collection_fingerprint(source_type, chunking_version)
        self._validate_collection(
            collection,
            source_type,
            expected_chunking_version=chunking_version,
            expected_fingerprint=fingerprint,
        )
        metadata = collection.metadata or {}
        expected_metadata = self._generation_metadata(
            source_type,
            chunking_version,
            snapshot_id,
            snapshot_digest,
            len(chunks),
        )
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise CollectionFingerprintMismatch(
                "staged generation metadata differs from the snapshot contract"
            )
        records = self._read_records(collection)
        if set(records) != {chunk.chunk_id for chunk in chunks}:
            raise CorruptVectorRecordError(
                "staged generation IDs differ from the complete snapshot"
            )
        for chunk in chunks:
            document, record_metadata = records[chunk.chunk_id]
            expected_record = self._record_metadata(chunk, snapshot_id)
            if (
                document != chunk.text
                or record_metadata.get("snapshot_id") != snapshot_id
                or record_metadata.get("chunk_json")
                != expected_record["chunk_json"]
            ):
                raise CorruptVectorRecordError(
                    "staged generation differs from the complete snapshot"
                )

    def _discard_inactive_generation(
        self, source_type: SourceType, snapshot_digest: str
    ) -> None:
        name = self._generation_name(source_type, snapshot_digest)
        try:
            registry = self._get_registry(source_type)
        except CollectionNotFoundError:
            registry = None
        if (
            registry is not None
            and (registry.metadata or {}).get("rag_active_snapshot_digest")
            == snapshot_digest
        ):
            return
        try:
            self._client.get_collection(name=name, embedding_function=None)
        except Exception:
            return
        try:
            self._client.delete_collection(name=name)
        except Exception as exc:
            raise VectorStoreError("failed to reset an incomplete generation") from exc

    def _promote_generation(
        self,
        registry: Any,
        source_type: SourceType,
        collection: Any,
        snapshot_id: str,
        snapshot_digest: str,
        chunking_version: str,
        expected_count: int,
    ) -> None:
        metadata = {
            **self._registry_metadata(source_type),
            "rag_active_collection": collection.name,
            "rag_active_snapshot_id": snapshot_id,
            "rag_active_snapshot_digest": snapshot_digest,
            "rag_active_chunking_version": chunking_version,
            "rag_active_collection_fingerprint": self._collection_fingerprint(
                source_type, chunking_version
            ),
            "rag_active_expected_count": expected_count,
        }
        try:
            registry.modify(metadata=metadata)
        except Exception as exc:
            raise VectorStoreError(
                "failed to atomically promote the complete generation"
            ) from exc

    def _validate_embeddings(
        self, vectors: Sequence[Sequence[float]], expected_count: int
    ) -> list[list[float]]:
        if len(vectors) != expected_count:
            raise VectorStoreError("embedding provider returned the wrong vector count")
        result: list[list[float]] = []
        for vector in vectors:
            converted = [float(value) for value in vector]
            if len(converted) != self.embedding_provider.dimension:
                raise VectorStoreError("embedding provider returned the wrong dimension")
            if any(not isfinite(value) for value in converted):
                raise VectorStoreError("embedding provider returned a non-finite value")
            result.append(converted)
        return result

    def sync_snapshot(
        self,
        source_type: SourceType,
        chunks: Sequence[Chunk],
        *,
        snapshot_id: str,
        chunking_version: str | None = None,
        secret_values: Iterable[str] = (),
    ) -> SnapshotSyncResult:
        """Stage, verify, then atomically promote a complete source generation.

        If staging fails, the registry continues to point at the previous active
        generation and readers never observe a partially written snapshot.
        """

        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty")
        chunks = tuple(chunks)
        if any(chunk.source_type is not source_type for chunk in chunks):
            raise ValueError("all chunks must match the target source_type")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("snapshot contains duplicate chunk IDs")
        versions = {
            str(chunk.metadata.get("chunking_version", "")) for chunk in chunks
        }
        if chunks and (len(versions) != 1 or "" in versions):
            raise ValueError("snapshot must use one non-empty chunking_version")
        resolved_version = next(iter(versions)) if chunks else chunking_version
        if not resolved_version:
            raise ValueError("empty snapshots require chunking_version")
        if chunking_version and chunking_version != resolved_version:
            raise ValueError("explicit chunking_version differs from chunk metadata")
        registry = self._get_or_create_registry(source_type)
        try:
            active = self._active_generation(source_type)
        except CollectionNotFoundError:
            active = None
        if active and active.chunking_version != resolved_version:
            raise CollectionFingerprintMismatch(
                "collection chunking version differs from the incoming snapshot"
            )
        self._validate_chunk_contracts(chunks, secret_values=secret_values)

        snapshot_digest = self._snapshot_digest(
            source_type, chunks, snapshot_id, resolved_version
        )
        fingerprint = self._collection_fingerprint(source_type, resolved_version)
        if active and active.snapshot_digest == snapshot_digest:
            self._verify_generation(
                active.collection,
                source_type,
                chunks,
                snapshot_id,
                snapshot_digest,
                resolved_version,
            )
            return SnapshotSyncResult(
                source_type=source_type,
                snapshot_id=snapshot_id,
                collection_name=active.collection.name,
                collection_fingerprint=fingerprint,
                upserted_count=0,
                deleted_count=0,
                total_count=active.expected_count,
            )

        # Counts describe the logical diff; the new generation is still written whole.
        existing_by_id = (
            self._read_records(active.collection) if active is not None else {}
        )
        desired_ids = {chunk.chunk_id for chunk in chunks}
        stale_ids = set(existing_by_id) - desired_ids
        changed_count = 0
        for chunk in chunks:
            expected_record = self._record_metadata(chunk, snapshot_id)
            previous = existing_by_id.get(chunk.chunk_id)
            if (
                previous is None
                or previous[0] != chunk.text
                or previous[1].get("chunk_json") != expected_record["chunk_json"]
                or previous[1].get("snapshot_id") != snapshot_id
            ):
                changed_count += 1

        self._discard_inactive_generation(source_type, snapshot_digest)
        generation_name = self._generation_name(source_type, snapshot_digest)
        try:
            collection = self._client.get_or_create_collection(
                name=generation_name,
                metadata=self._generation_metadata(
                    source_type,
                    resolved_version,
                    snapshot_id,
                    snapshot_digest,
                    len(chunks),
                ),
                embedding_function=None,
            )
        except Exception as exc:
            raise VectorStoreError("failed to create a staging generation") from exc

        ordered_chunks = tuple(sorted(chunks, key=lambda item: item.chunk_id))
        try:
            for start in range(0, len(ordered_chunks), self.config.batch_size):
                batch = ordered_chunks[start : start + self.config.batch_size]
                texts = [chunk.text for chunk in batch]
                vectors = self._validate_embeddings(
                    self.embedding_provider.embed_documents(texts), len(batch)
                )
                collection.upsert(
                    ids=[chunk.chunk_id for chunk in batch],
                    embeddings=vectors,
                    documents=texts,
                    metadatas=[
                        self._record_metadata(chunk, snapshot_id) for chunk in batch
                    ],
                )
            self._verify_generation(
                collection,
                source_type,
                chunks,
                snapshot_id,
                snapshot_digest,
                resolved_version,
            )
            self._promote_generation(
                registry,
                source_type,
                collection,
                snapshot_id,
                snapshot_digest,
                resolved_version,
                len(chunks),
            )
        except Exception:
            try:
                self._discard_inactive_generation(source_type, snapshot_digest)
            except VectorStoreError:
                pass
            raise

        promoted = self._active_generation(source_type)
        if promoted.snapshot_digest != snapshot_digest:
            raise VectorStoreError(
                "active generation changed during snapshot promotion"
            )
        return SnapshotSyncResult(
            source_type=source_type,
            snapshot_id=snapshot_id,
            collection_name=promoted.collection.name,
            collection_fingerprint=fingerprint,
            upserted_count=changed_count,
            deleted_count=len(stale_ids),
            total_count=len(chunks),
        )

    def delete_snapshot(
        self, source_type: SourceType, snapshot_id: str
    ) -> SnapshotDeleteResult:
        """Logically delete the active snapshot by promoting an empty generation."""

        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty")
        try:
            active = self._active_generation(source_type)
        except CollectionNotFoundError:
            return SnapshotDeleteResult(source_type, snapshot_id, 0)
        if active.snapshot_id != snapshot_id:
            return SnapshotDeleteResult(source_type, snapshot_id, 0)
        result = self.sync_snapshot(
            source_type,
            (),
            snapshot_id=snapshot_id,
            chunking_version=active.chunking_version,
        )
        return SnapshotDeleteResult(source_type, snapshot_id, result.deleted_count)

    def _query_where(self, search_filter: VectorSearchFilter) -> dict[str, Any] | None:
        """Push snapshot and scalar equality filters down to Chroma."""

        clauses: list[dict[str, Any]] = []
        if search_filter.snapshot_id:
            clauses.append({"snapshot_id": search_filter.snapshot_id})
        for key, value in search_filter.metadata_equals.items():
            stored_key = (
                key
                if key in {"doc_id", "source_type", "ordinal"}
                else f"meta__{key}"
            )
            clauses.append({stored_key: value})
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def _decode_chunk(
        self, record_id: str, document: str, metadata: Mapping[str, Any]
    ) -> Chunk:
        serialized = metadata.get("chunk_json")
        if not isinstance(serialized, str):
            raise CorruptVectorRecordError("vector record lacks serialized Chunk data")
        try:
            chunk = Chunk.from_dict(json.loads(serialized))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CorruptVectorRecordError("vector record contains invalid Chunk data") from exc
        if (
            chunk.chunk_id != record_id
            or chunk.text != document
            or chunk.content_hash != compute_content_hash(chunk.text)
        ):
            raise CorruptVectorRecordError("vector record does not match its Chunk contract")
        return chunk

    def _matches_filter(
        self,
        chunk: Chunk,
        record_metadata: Mapping[str, Any],
        source_type: SourceType,
        search_filter: VectorSearchFilter,
    ) -> bool:
        """Reapply portable date, region, and metadata rules to decoded chunks."""

        if chunk.source_type is not source_type:
            return False
        if (
            search_filter.snapshot_id
            and record_metadata.get("snapshot_id") != search_filter.snapshot_id
        ):
            return False
        if search_filter.as_of is not None:
            policy = MetadataFilter(
                source_type=source_type,
                as_of=search_filter.as_of,
                region_names=(),
            )
            if not chunk_matches_filter(chunk, policy):
                return False
        if not subsidy_regions_match(chunk.metadata, search_filter.region_names):
            return False
        for key, expected in search_filter.metadata_equals.items():
            actual = {
                "doc_id": chunk.doc_id,
                "source_type": chunk.source_type.value,
                "ordinal": chunk.ordinal,
            }.get(key, chunk.metadata.get(key))
            if actual != expected:
                return False
        return True

    def search(
        self,
        source_type: SourceType,
        query: str,
        *,
        query_id: str,
        top_k: int = 5,
        search_filter: VectorSearchFilter | None = None,
        expected_collection_fingerprint: str | None = None,
    ) -> tuple[RetrievedChunk, ...]:
        """Return validated top-k chunks from one source-specific active index."""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        if not isinstance(query_id, str) or not query_id.strip():
            raise ValueError("query_id must be non-empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        search_filter = search_filter or VectorSearchFilter()
        if search_filter.region_names and source_type is not SourceType.SUBSIDY:
            raise ValueError("region filters apply only to subsidy chunks")
        collection = self._get_collection(source_type)
        _, fingerprint = self._validate_collection(
            collection,
            source_type,
            expected_fingerprint=expected_collection_fingerprint,
        )
        count = collection.count()
        if count == 0:
            return ()
        vector = self._validate_embeddings(
            [self.embedding_provider.embed_query(query)], 1
        )[0]
        try:
            # Fetch all pushdown matches so post-filtering can still return true top-k.
            result = collection.query(
                query_embeddings=[vector],
                n_results=count,
                where=self._query_where(search_filter),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise VectorStoreError("failed to query Chroma collection") from exc
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        retrieved: list[RetrievedChunk] = []
        for record_id, document, metadata, distance in zip(
            ids, documents, metadatas, distances
        ):
            if not isinstance(document, str) or not isinstance(metadata, Mapping):
                raise CorruptVectorRecordError("vector query returned an invalid record")
            chunk = self._decode_chunk(record_id, document, metadata)
            if not self._matches_filter(
                chunk, metadata, source_type, search_filter
            ):
                continue
            retrieved.append(
                RetrievedChunk(
                    query_id=query_id,
                    chunk=chunk,
                    rank=len(retrieved) + 1,
                    score=float(distance),
                    score_type="cosine_distance",
                    retriever_version=f"{VECTOR_STORE_VERSION}:{fingerprint}",
                    index_name=source_type.value,
                )
            )
            if len(retrieved) == top_k:
                break
        return tuple(retrieved)
