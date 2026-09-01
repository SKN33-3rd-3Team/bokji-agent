from __future__ import annotations

from dataclasses import replace
from datetime import date
import gc
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, SourceType, compute_content_hash
from rag_design.embeddings import (
    EmbeddingProviderError,
    HashEmbeddingProvider,
    SentenceTransformerKoreanProvider,
)
from rag_design.vector_cli import build_parser
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionFingerprintMismatch,
    CollectionNotFoundError,
    VectorSearchFilter,
    VectorStoreConfig,
)


try:
    import chromadb as _chromadb  # noqa: F401
except Exception:
    CHROMA_AVAILABLE = False
else:
    CHROMA_AVAILABLE = True


FIXTURES = Path(__file__).parent / "fixtures"


def load_documents() -> tuple[Document, ...]:
    documents = tuple(
        Document.from_dict(json.loads(line))
        for line in (FIXTURES / "documents.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    if len(documents) != 4:
        raise AssertionError(
            "vector-store fixture must contain one subsidy and three legal documents"
        )
    return documents


class CountingHashEmbeddingProvider(HashEmbeddingProvider):
    def __init__(self, dimension: int = 64) -> None:
        super().__init__(dimension)
        self.document_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        return super().embed_documents(texts)


class FailingBatchEmbeddingProvider(CountingHashEmbeddingProvider):
    def __init__(self, dimension: int = 64) -> None:
        super().__init__(dimension)
        self.fail_on_document_call: int | None = None

    def embed_documents(self, texts):
        self.document_calls += 1
        if self.document_calls == self.fail_on_document_call:
            raise EmbeddingProviderError("injected batch failure")
        return HashEmbeddingProvider.embed_documents(self, texts)


class EmbeddingProviderTests(unittest.TestCase):
    def test_hash_embedding_is_deterministic_and_normalized(self) -> None:
        first = HashEmbeddingProvider(64)
        second = HashEmbeddingProvider(64)
        value = first.embed_query("유아학비 지원")
        self.assertEqual(value, second.embed_query("유아학비 지원"))
        self.assertEqual(len(value), 64)
        self.assertAlmostEqual(sum(item * item for item in value), 1.0)

    def test_korean_provider_reports_missing_optional_dependency(self) -> None:
        provider = SentenceTransformerKoreanProvider(local_files_only=True)
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            with self.assertRaisesRegex(
                EmbeddingProviderError, "sentence-transformers is required"
            ):
                provider.embed_query("복지 서비스")

    def test_vector_region_filter_requires_canonical_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "region names"):
            VectorSearchFilter(region_names=("1100000000",))
        with self.assertRaisesRegex(ValueError, "duplicates"):
            VectorSearchFilter(region_names=("서울특별시", "서울특별시"))

    def test_vector_age_filter_requires_plausible_integer(self) -> None:
        for value in (-1, 121, True, 65.0):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "age"):
                VectorSearchFilter(age=value)

    def test_vector_cli_accepts_canonical_region_names(self) -> None:
        args = build_parser().parse_args(
            [
                "search",
                "--source",
                "subsidy",
                "--query-id",
                "q-region",
                "--query",
                "지원",
                "--region-name",
                "서울특별시 강남구",
            ]
        )
        self.assertEqual(args.region_name, ["서울특별시 강남구"])


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class ChromaVectorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.persist_directory = Path(self.temporary_directory.name) / "index"
        self.config = VectorStoreConfig(
            persist_directory=self.persist_directory,
            collection_prefix="test_rag",
            batch_size=2,
        )
        documents = load_documents()
        self.subsidy = documents[0]
        self.law, self.admrul, self.ordin = documents[1:]
        self.legal_documents = (self.law, self.admrul, self.ordin)
        self.subsidy_chunks = chunk_document(self.subsidy)
        self.law_chunks = tuple(
            chunk
            for document in self.legal_documents
            for chunk in chunk_document(document)
        )

    def tearDown(self) -> None:
        gc.collect()
        self.temporary_directory.cleanup()

    def test_persistent_idempotent_sync_and_retrieved_chunk_conversion(self) -> None:
        provider = CountingHashEmbeddingProvider()
        store = ChromaVectorStore(provider, self.config)
        first = store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-001",
        )
        calls_after_first_sync = provider.document_calls
        repeated = store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-001",
        )
        self.assertEqual(first.upserted_count, len(self.subsidy_chunks))
        self.assertEqual(repeated.upserted_count, 0)
        self.assertEqual(repeated.deleted_count, 0)
        self.assertEqual(provider.document_calls, calls_after_first_sync)

        del store
        gc.collect()
        reopened = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        results = reopened.search(
            SourceType.SUBSIDY,
            self.subsidy_chunks[0].text,
            query_id="q-persistent",
            top_k=2,
            expected_collection_fingerprint=first.collection_fingerprint,
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].chunk.chunk_id, self.subsidy_chunks[0].chunk_id)
        self.assertEqual([item.rank for item in results], [1, 2])
        self.assertTrue(all(item.query_id == "q-persistent" for item in results))
        self.assertTrue(all(item.index_name == "subsidy" for item in results))
        self.assertTrue(
            all(item.score_type == "cosine_distance" for item in results)
        )

    def test_snapshot_replacement_and_delete_by_snapshot_are_idempotent(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        initial = store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks[:2],
            snapshot_id="subsidy-001",
        )
        replacement = store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks[:1],
            snapshot_id="subsidy-002",
        )
        self.assertEqual(initial.total_count, 2)
        self.assertEqual(replacement.upserted_count, 1)
        self.assertEqual(replacement.deleted_count, 1)
        self.assertEqual(replacement.total_count, 1)

        deleted = store.delete_snapshot(SourceType.SUBSIDY, "subsidy-002")
        repeated = store.delete_snapshot(SourceType.SUBSIDY, "subsidy-002")
        self.assertEqual(deleted.deleted_count, 1)
        self.assertEqual(repeated.deleted_count, 0)

    def test_source_date_region_and_metadata_filters(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        regional = replace(
            self.subsidy_chunks[0],
            metadata={
                **self.subsidy_chunks[0].metadata,
                "region_scope": "regional",
                "region_names": ["서울특별시", "서울특별시 강남구"],
            },
        )
        store.sync_snapshot(
            SourceType.SUBSIDY, (regional,), snapshot_id="subsidy-regional"
        )
        matching = store.search(
            SourceType.SUBSIDY,
            regional.text,
            query_id="q-region-match",
            search_filter=VectorSearchFilter(
                region_names=("서울특별시 강남구",),
                metadata_equals={"organization": "교육부"},
            ),
        )
        excluded = store.search(
            SourceType.SUBSIDY,
            regional.text,
            query_id="q-region-miss",
            search_filter=VectorSearchFilter(region_names=("부산광역시",)),
        )
        self.assertEqual(len(matching), 1)
        self.assertEqual(excluded, ())

        store.sync_snapshot(
            SourceType.LAW, self.law_chunks, snapshot_id="law-2025-10-01"
        )
        before = store.search(
            SourceType.LAW,
            self.law_chunks[0].text,
            query_id="q-law-before",
            search_filter=VectorSearchFilter(as_of=date(2025, 9, 30)),
        )
        effective = store.search(
            SourceType.LAW,
            self.law_chunks[0].text,
            query_id="q-law-effective",
            search_filter=VectorSearchFilter(as_of=date(2025, 10, 1)),
        )
        self.assertEqual(before, ())
        self.assertEqual(len(effective), 1)

    def test_legal_subtypes_keep_metadata_only_index_contract(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        synced = store.sync_snapshot(
            SourceType.LAW,
            self.law_chunks,
            snapshot_id="legal-metadata-001",
        )
        self.assertEqual(synced.total_count, 3)
        registry = store._get_registry(SourceType.LAW)
        self.assertEqual(
            registry.metadata["rag_legal_contract_version"], "legal-metadata-v1"
        )

        for document in self.legal_documents:
            law_type = document.metadata["law_type"]
            with self.subTest(law_type=law_type):
                results = store.search(
                    SourceType.LAW,
                    document.content,
                    query_id=f"q-{law_type}",
                    top_k=3,
                    search_filter=VectorSearchFilter(
                        metadata_equals={"law_type": law_type}
                    ),
                )
                self.assertEqual(len(results), 1)
                chunk = results[0].chunk
                self.assertEqual(chunk.metadata["content_level"], "metadata_only")
                self.assertEqual(
                    chunk.metadata["source_sequence"],
                    document.metadata["source_sequence"],
                )
                self.assertEqual(chunk.citation_locator, "기본정보")

    def test_exact_metadata_lookup_reads_every_page_without_query_embedding(self) -> None:
        provider = CountingHashEmbeddingProvider()
        store = ChromaVectorStore(provider, self.config)
        synced = store.sync_snapshot(
            SourceType.LAW,
            self.law_chunks,
            snapshot_id="legal-exact-lookup-001",
        )
        store._READ_RECORDS_BATCH_SIZE = 1

        with patch.object(
            provider,
            "embed_query",
            side_effect=AssertionError("exact lookup must not embed a query"),
        ):
            all_legal = store.get_chunks_by_metadata(
                SourceType.LAW,
                metadata_equals={"content_level": "metadata_only"},
                expected_collection_fingerprint=synced.collection_fingerprint,
            )
            exact = store.get_chunks_by_metadata(
                SourceType.LAW,
                metadata_equals={"law_name": self.law.metadata["law_name"]},
            )
            missing = store.get_chunks_by_metadata(
                SourceType.LAW,
                metadata_equals={"law_name": "존재하지 않는 법령"},
            )

        self.assertEqual(
            [chunk.chunk_id for chunk in all_legal],
            sorted(chunk.chunk_id for chunk in self.law_chunks),
        )
        self.assertEqual(exact, chunk_document(self.law))
        self.assertEqual(missing, ())

        with self.assertRaisesRegex(ValueError, "metadata_equals"):
            store.get_chunks_by_metadata(SourceType.LAW, metadata_equals={})

    def test_vector_rejects_noncanonical_legal_metadata_content(self) -> None:
        provider = CountingHashEmbeddingProvider()
        store = ChromaVectorStore(provider, self.config)
        chunk = self.law_chunks[0]
        disguised_body = (
            f"{self.law.title}\n기본정보\n"
            "제1조(목적) 이 법은 국민의 최저생활을 보장한다."
        )
        cases = (
            replace(
                chunk,
                text=disguised_body,
                content_hash=compute_content_hash(disguised_body),
            ),
            replace(
                chunk,
                metadata={**chunk.metadata, "law_name": "전혀 다른 법령명"},
            ),
        )

        for index, invalid_chunk in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    store.sync_snapshot(
                        SourceType.LAW,
                        (invalid_chunk,),
                        snapshot_id=f"invalid-legal-content-{index}",
                    )
        self.assertEqual(provider.document_calls, 0)

    def test_vector_rejects_recursive_secret_and_auth_query_material(self) -> None:
        provider = CountingHashEmbeddingProvider()
        store = ChromaVectorStore(provider, self.config)
        secret = "KNOWN_VECTOR_SECRET"
        cases = (
            (
                replace(
                    self.law_chunks[0],
                    metadata={
                        **self.law_chunks[0].metadata,
                        "collector_context": {
                            "request_urls": [
                                "https://open.law.go.kr/DRF/lawService.do?"
                                "OC=UNSUPPLIED_CREDENTIAL&target=law"
                            ]
                        },
                    },
                ),
                (),
            ),
            (
                replace(
                    self.law_chunks[0],
                    metadata={
                        **self.law_chunks[0].metadata,
                        "collector_context": {"debug_token": [secret]},
                    },
                ),
                (secret,),
            ),
        )

        for index, (invalid_chunk, secrets) in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    store.sync_snapshot(
                        SourceType.LAW,
                        (invalid_chunk,),
                        snapshot_id=f"credential-material-{index}",
                        secret_values=secrets,
                    )
        self.assertEqual(provider.document_calls, 0)

    def test_vector_rejects_noncanonical_or_invalid_legal_dates(self) -> None:
        provider = CountingHashEmbeddingProvider()
        store = ChromaVectorStore(provider, self.config)
        chunk = self.law_chunks[0]
        cases = (
            {**chunk.metadata, "issued_date": "20250318"},
            {**chunk.metadata, "effective_to": "not-a-date"},
            {**chunk.metadata, "effective_to": "2025-09-30"},
            {**chunk.metadata, "effective_to": chunk.metadata["effective_from"]},
        )

        for index, metadata in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    store.sync_snapshot(
                        SourceType.LAW,
                        (replace(chunk, metadata=metadata),),
                        snapshot_id=f"invalid-legal-date-{index}",
                    )
        self.assertEqual(provider.document_calls, 0)

    def test_source_isolation_and_collection_fingerprint_validation(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        with self.assertRaisesRegex(ValueError, "target source_type"):
            store.sync_snapshot(
                SourceType.SUBSIDY,
                (self.subsidy_chunks[0], self.law_chunks[0]),
                snapshot_id="mixed",
            )

        synced = store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-001",
        )
        with self.assertRaises(CollectionFingerprintMismatch):
            store.search(
                SourceType.SUBSIDY,
                "지원",
                query_id="q-wrong-fingerprint",
                expected_collection_fingerprint="0" * 64,
            )
        self.assertEqual(
            store.collection_fingerprint(SourceType.SUBSIDY),
            synced.collection_fingerprint,
        )

        incompatible = ChromaVectorStore(HashEmbeddingProvider(32), self.config)
        with self.assertRaises(CollectionNotFoundError):
            incompatible.collection_fingerprint(SourceType.SUBSIDY)

    def test_age_filter_rejects_mismatch_and_honors_missing_policy(self) -> None:
        aged_chunks = tuple(
            replace(
                chunk,
                metadata={
                    **chunk.metadata,
                    "age_start": 65,
                    "age_end": 120,
                    "age_basis": "international_age",
                    "age_source": "support_conditions_api",
                },
            )
            for chunk in self.subsidy_chunks
        )
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        store.sync_snapshot(SourceType.SUBSIDY, aged_chunks, snapshot_id="age-filter")

        rejected = store.search(
            SourceType.SUBSIDY,
            "유아학비",
            query_id="too-young",
            top_k=len(aged_chunks),
            search_filter=VectorSearchFilter(age=40),
        )
        accepted = store.search(
            SourceType.SUBSIDY,
            "유아학비",
            query_id="old-enough",
            top_k=len(aged_chunks),
            search_filter=VectorSearchFilter(age=70),
        )
        self.assertEqual(rejected, ())
        self.assertEqual(len(accepted), len(aged_chunks))

        store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="age-missing",
        )
        kept = store.search(
            SourceType.SUBSIDY,
            "유아학비",
            query_id="missing-kept",
            top_k=len(self.subsidy_chunks),
            search_filter=VectorSearchFilter(age=70),
        )
        dropped = store.search(
            SourceType.SUBSIDY,
            "유아학비",
            query_id="missing-dropped",
            top_k=len(self.subsidy_chunks),
            search_filter=VectorSearchFilter(age=70, allow_missing_age=False),
        )
        self.assertEqual(len(kept), len(self.subsidy_chunks))
        self.assertEqual(dropped, ())

    def test_provider_registries_coexist_and_matching_legacy_falls_back(self) -> None:
        legacy_store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        source = SourceType.SUBSIDY
        legacy_name = legacy_store._legacy_registry_name(source)
        legacy_store._client.get_or_create_collection(
            name=legacy_name,
            metadata=legacy_store._registry_metadata(source),
            embedding_function=None,
        )
        legacy_sync = legacy_store.sync_snapshot(
            source, self.subsidy_chunks, snapshot_id="legacy-hash-64"
        )
        self.assertEqual(legacy_store._get_registry(source).name, legacy_name)

        qualified_store = ChromaVectorStore(HashEmbeddingProvider(32), self.config)
        qualified_sync = qualified_store.sync_snapshot(
            source, self.subsidy_chunks, snapshot_id="qualified-hash-32"
        )
        qualified_name = qualified_store._registry_name(source)
        self.assertNotEqual(qualified_name, legacy_name)
        self.assertEqual(qualified_store._get_registry(source).name, qualified_name)
        self.assertEqual(
            qualified_store.collection_fingerprint(source),
            qualified_sync.collection_fingerprint,
        )
        self.assertEqual(
            legacy_store.collection_fingerprint(source),
            legacy_sync.collection_fingerprint,
        )

    def test_korean_registry_names_match_deployed_fingerprints(self) -> None:
        store = ChromaVectorStore(SentenceTransformerKoreanProvider(), self.config)
        self.assertEqual(
            store._registry_name(SourceType.SUBSIDY),
            "test_rag_subsidy_registry_f5423cb7327c4dcf",
        )
        self.assertEqual(
            store._registry_name(SourceType.LAW),
            "test_rag_law_registry_57f61b87c3f6cc78",
        )

    def test_chunking_version_mismatch_is_rejected(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-001",
        )
        changed = tuple(
            replace(
                chunk,
                metadata={**chunk.metadata, "chunking_version": "structure-v999"},
            )
            for chunk in self.subsidy_chunks
        )
        with self.assertRaises(CollectionFingerprintMismatch):
            store.sync_snapshot(
                SourceType.SUBSIDY, changed, snapshot_id="subsidy-002"
            )

    def test_legacy_vector_store_version_is_rejected(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-001",
        )
        registry = store._get_registry(SourceType.SUBSIDY)
        registry.modify(
            metadata={
                **registry.metadata,
                "rag_storage_version": "chroma-vector-store-v2",
            }
        )
        with self.assertRaises(CollectionFingerprintMismatch):
            store.collection_fingerprint(SourceType.SUBSIDY)

    def test_legacy_law_contract_profile_is_rejected(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        store.sync_snapshot(
            SourceType.LAW,
            self.law_chunks,
            snapshot_id="legal-metadata-001",
        )
        registry = store._get_registry(SourceType.LAW)
        registry.modify(
            metadata={
                **registry.metadata,
                "rag_legal_contract_version": "full-text-v0",
            }
        )
        with self.assertRaises(CollectionFingerprintMismatch):
            store.collection_fingerprint(SourceType.LAW)

    def test_invalid_chunk_hash_is_rejected_before_indexing(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        tampered = replace(self.subsidy_chunks[0], content_hash="0" * 64)
        with self.assertRaisesRegex(ValueError, "content_hash"):
            store.sync_snapshot(
                SourceType.SUBSIDY, (tampered,), snapshot_id="tampered"
            )
        missing_source_url = replace(
            self.subsidy_chunks[0],
            metadata={
                key: value
                for key, value in self.subsidy_chunks[0].metadata.items()
                if key != "source_url"
            },
        )
        with self.assertRaisesRegex(ValueError, "source_url"):
            store.sync_snapshot(
                SourceType.SUBSIDY,
                (missing_source_url,),
                snapshot_id="missing-metadata",
            )

    def test_authenticated_source_url_is_never_promoted(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        secret = "known-data-go-secret"
        authenticated = replace(
            self.subsidy_chunks[0],
            metadata={
                **self.subsidy_chunks[0].metadata,
                "source_url": (
                    f"{self.subsidy_chunks[0].metadata['source_url']}"
                    f"?serviceKey={secret}"
                ),
            },
        )
        with self.assertRaisesRegex(ValueError, "secret|authentication"):
            store.sync_snapshot(
                SourceType.SUBSIDY,
                (authenticated,),
                snapshot_id="authenticated-url",
                secret_values=(secret,),
            )
        with self.assertRaises(CollectionNotFoundError):
            store.collection_fingerprint(SourceType.SUBSIDY)

    def test_legal_source_url_must_match_subtype_sequence_and_date(self) -> None:
        store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        cases = (
            (
                self.law_chunks[0],
                "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=999&efYd=20251001",
            ),
            (
                self.law_chunks[0],
                "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=276653&efYd=19000101",
            ),
            (self.law_chunks[1], "https://www.law.go.kr/"),
            (
                self.law_chunks[2],
                "https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=999",
            ),
        )
        for chunk, source_url in cases:
            with self.subTest(source_url=source_url):
                mismatched = replace(
                    chunk,
                    metadata={**chunk.metadata, "source_url": source_url},
                )
                with self.assertRaisesRegex(ValueError, "legal subtype"):
                    store.sync_snapshot(
                        SourceType.LAW,
                        (mismatched,),
                        snapshot_id="mismatched-law-url",
                    )
        with self.assertRaises(CollectionNotFoundError):
            store.collection_fingerprint(SourceType.LAW)

    def test_failed_staging_batch_never_replaces_the_active_snapshot(self) -> None:
        atomic_config = VectorStoreConfig(
            persist_directory=self.persist_directory,
            collection_prefix="atomic_rag",
            batch_size=1,
        )
        provider = FailingBatchEmbeddingProvider()
        store = ChromaVectorStore(provider, atomic_config)
        first = store.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-001",
        )
        provider.fail_on_document_call = provider.document_calls + 2
        with self.assertRaisesRegex(EmbeddingProviderError, "injected batch failure"):
            store.sync_snapshot(
                SourceType.SUBSIDY,
                self.subsidy_chunks,
                snapshot_id="subsidy-002",
            )

        still_active = store.search(
            SourceType.SUBSIDY,
            self.subsidy_chunks[0].text,
            query_id="q-after-failure",
            top_k=len(self.subsidy_chunks),
            search_filter=VectorSearchFilter(snapshot_id="subsidy-001"),
        )
        incomplete = store.search(
            SourceType.SUBSIDY,
            self.subsidy_chunks[0].text,
            query_id="q-incomplete",
            search_filter=VectorSearchFilter(snapshot_id="subsidy-002"),
        )
        self.assertEqual(len(still_active), len(self.subsidy_chunks))
        self.assertEqual(incomplete, ())
        self.assertEqual(
            store.collection_fingerprint(SourceType.SUBSIDY),
            first.collection_fingerprint,
        )

        del store
        gc.collect()
        reopened = ChromaVectorStore(HashEmbeddingProvider(64), atomic_config)
        after_reopen = reopened.search(
            SourceType.SUBSIDY,
            self.subsidy_chunks[0].text,
            query_id="q-reopened",
            top_k=len(self.subsidy_chunks),
            search_filter=VectorSearchFilter(snapshot_id="subsidy-001"),
        )
        repeated = reopened.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-001",
        )
        self.assertEqual(len(after_reopen), len(self.subsidy_chunks))
        self.assertEqual(repeated.upserted_count, 0)
        self.assertEqual(repeated.deleted_count, 0)

        promoted = reopened.sync_snapshot(
            SourceType.SUBSIDY,
            self.subsidy_chunks,
            snapshot_id="subsidy-002",
        )
        visible_new = reopened.search(
            SourceType.SUBSIDY,
            self.subsidy_chunks[0].text,
            query_id="q-promoted",
            top_k=len(self.subsidy_chunks),
            search_filter=VectorSearchFilter(snapshot_id="subsidy-002"),
        )
        self.assertEqual(promoted.total_count, len(self.subsidy_chunks))
        self.assertEqual(len(visible_new), len(self.subsidy_chunks))


if __name__ == "__main__":
    unittest.main()
