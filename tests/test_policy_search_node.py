from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
import gc
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import (
    Chunk,
    Document,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from rag_chatbot.graph.nodes.policy_search import (
    SEMANTIC_CANDIDATE_LIMIT,
    _build_query,
    search_policies,
)

try:
    import chromadb as _chromadb  # noqa: F401
except Exception:
    CHROMA_AVAILABLE = False
else:
    CHROMA_AVAILABLE = True

FIXTURES = Path(__file__).parent / "fixtures"


def load_documents() -> tuple[Document, ...]:
    return tuple(
        Document.from_dict(json.loads(line))
        for line in (FIXTURES / "documents.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class SearchPoliciesNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        config = VectorStoreConfig(
            persist_directory=Path(self.temporary_directory.name) / "index",
            collection_prefix="test_n4",
        )
        self.store = ChromaVectorStore(HashEmbeddingProvider(64), config)

        documents = load_documents()
        self.subsidy = next(d for d in documents if d.source_type is SourceType.SUBSIDY)
        subsidy_chunks = chunk_document(self.subsidy)
        self.store.sync_snapshot(
            SourceType.SUBSIDY, subsidy_chunks, snapshot_id="subsidy-001"
        )

    def tearDown(self) -> None:
        del self.store
        gc.collect()
        self.temporary_directory.cleanup()

    def test_search_policies_returns_subsidy_chunks(self) -> None:
        state = {
            "query_id": "q-1",
            "as_of": date(2026, 1, 1),
            "slots": {"interests": ["유아학비"], "region_names": []},
        }

        update = search_policies(state, self.store, top_k=3)

        self.assertIn("subsidy_chunks", update)
        chunks = update["subsidy_chunks"]
        self.assertGreater(len(chunks), 0)
        self.assertTrue(all(c.index_name == "subsidy" for c in chunks))
        self.assertTrue(all(c.query_id == "q-1" for c in chunks))

    def test_search_policies_falls_back_to_broad_query_when_no_interests(self) -> None:
        state = {
            "query_id": "q-2",
            "as_of": date(2026, 1, 1),
            "slots": {"region_names": []},
        }

        update = search_policies(state, self.store, top_k=3)

        self.assertIn("subsidy_chunks", update)
        self.assertGreater(len(update["subsidy_chunks"]), 0)

    def test_search_policies_requires_query_id(self) -> None:
        with self.assertRaises(ValueError):
            search_policies({"as_of": date(2026, 1, 1), "slots": {}}, self.store)

    def test_search_policies_requires_as_of(self) -> None:
        with self.assertRaises(ValueError):
            search_policies({"query_id": "q-3", "slots": {}}, self.store)

    def test_search_policies_rejects_non_date_as_of(self) -> None:
        for value in ("", 0, False, "2026-01-01", datetime(2026, 1, 1)):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, r"state\['as_of'\] must be a date"
                ):
                    search_policies(
                        {"query_id": "q-invalid-as-of", "as_of": value, "slots": {}},
                        self.store,
                    )


class BuildQueryTests(unittest.TestCase):
    """검색 질의 조립 (2026-08-31 변경).

    예전에는 interests 키워드만 썼고 사용자의 실제 문장은 검색에 전혀
    반영되지 않았다. "안녕"으로 시작한 대화가 고정 fallback 질의로 넘어가
    관계없는 정책 5건이 추천된 일이 있었다.
    """

    def test_interests_and_question_are_combined(self) -> None:
        query = _build_query({"interests": ["육아"]}, "아이 키우는데 지원 뭐 있나요")
        self.assertIn("육아", query)
        self.assertIn("아이 키우는데", query)

    def test_question_alone_is_enough_when_no_interest_keyword_matched(self) -> None:
        # 관심사 키워드 표에 없는 표현이어도 질문 자체로 검색할 수 있어야 한다.
        query = _build_query({"interests": []}, "혼자 사는데 월세가 너무 부담돼요")
        self.assertIn("월세", query)

    def test_greeting_too_short_to_be_query_material_uses_fallback(self) -> None:
        self.assertEqual(_build_query({"interests": []}, "안녕"), "생활 지원 복지 서비스")

    def test_no_material_at_all_uses_fallback(self) -> None:
        self.assertEqual(_build_query({"interests": []}, None), "생활 지원 복지 서비스")

    def test_query_never_carries_pii(self) -> None:
        # 검색 로그와 임베딩 provider로 PII가 나가면 안 된다.
        query = _build_query(
            {"interests": ["주거"]},
            "제 번호는 010-1234-5678이고 1990년 3월 15일생인데 월세 지원 있나요",
        )
        self.assertNotIn("010-1234-5678", query)
        self.assertNotIn("1990년 3월 15일", query)
        self.assertIn("월세", query)

    def test_long_question_is_truncated(self) -> None:
        query = _build_query({"interests": []}, "월세" * 300)
        self.assertLessEqual(len(query), 220)


class ConfigurableTopKTests(unittest.TestCase):
    class _Store:
        def __init__(self, results=(), exact_chunks=()) -> None:
            self.top_k = None
            self.search_filter = None
            self.results = results
            self.exact_chunks = tuple(exact_chunks)
            self.exact_calls: list[tuple[SourceType, dict]] = []

        def search(self, source_type, query, *, query_id, top_k, search_filter):
            self.top_k = top_k
            self.search_filter = search_filter
            return self.results

        def get_chunks_by_metadata(self, source_type, *, metadata_equals, **kwargs):
            self.exact_calls.append((source_type, dict(metadata_equals)))
            return tuple(
                chunk
                for chunk in self.exact_chunks
                if chunk.source_type is source_type
                and all(
                    chunk.metadata.get(key) == value
                    for key, value in metadata_equals.items()
                )
            )

    def _state(self, top_k=7):
        return {
            "query_id": "q-top-k",
            "as_of": date(2026, 1, 1),
            "slots": {"region_names": []},
            "policy_top_k": top_k,
        }

    def test_uses_top_k_from_graph_state(self) -> None:
        store = self._Store(
            tuple(self._candidate(f"service-{rank}", rank) for rank in range(1, 11))
        )
        result = search_policies(self._state(7), store)
        self.assertEqual(store.top_k, SEMANTIC_CANDIDATE_LIMIT)
        self.assertEqual(len(result["subsidy_chunks"]), 7)

    def test_explicit_node_argument_overrides_state(self) -> None:
        store = self._Store(
            tuple(self._candidate(f"service-{rank}", rank) for rank in range(1, 11))
        )
        result = search_policies(self._state(7), store, top_k=3)
        self.assertEqual(store.top_k, SEMANTIC_CANDIDATE_LIMIT)
        self.assertEqual(len(result["subsidy_chunks"]), 3)

    def test_rejects_out_of_range_top_k(self) -> None:
        for value in (0, 21, True, "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                search_policies(self._state(value), self._Store())

    def test_self_international_age_is_connected_to_search_filter(self) -> None:
        store = self._Store()
        state = self._state()
        state["slots"].update(
            {
                "birth_date": "1960-12-31",
                "age": 65,
                "age_year_based": 66,
                "age_subject": "self",
            }
        )

        search_policies(state, store)

        self.assertEqual(store.search_filter.age, 65)
        self.assertTrue(store.search_filter.allow_missing_age)

    def test_non_self_age_is_not_used_as_search_filter(self) -> None:
        store = self._Store()
        state = self._state()
        state["slots"].update(
            {
                "birth_date": "2020-01-01",
                "age": 6,
                "age_year_based": 6,
                "age_subject": "child",
            }
        )

        search_policies(state, store)

        self.assertIsNone(store.search_filter.age)

    @staticmethod
    def _candidate(
        source_id: str, rank: int, *, chunk_suffix: str = ""
    ) -> RetrievedChunk:
        text = f"정책 {source_id}{chunk_suffix}"
        chunk = Chunk(
            schema_version=SCHEMA_VERSION,
            chunk_id=f"chunk-{source_id}{chunk_suffix}",
            doc_id=f"subsidy:{source_id}:v1",
            source_type=SourceType.SUBSIDY,
            text=text,
            heading_path=("지원대상",),
            ordinal=0,
            citation_locator="지원대상",
            content_hash=compute_content_hash(text),
            metadata={"source_id": source_id},
        )
        return RetrievedChunk(
            query_id="q-top-k",
            chunk=chunk,
            rank=rank,
            score=rank / 10,
            score_type="cosine_distance",
            retriever_version="test:fixture",
            index_name="subsidy",
        )

    @staticmethod
    def _legal_basis_chunk(
        source_id: str, *, ordinal: int, chunk_part: int, chunk_id: str
    ) -> Chunk:
        text = f"정책 {source_id}\n근거법령\n\n테스트법(제{chunk_part + 1}조)"
        return Chunk(
            schema_version=SCHEMA_VERSION,
            chunk_id=chunk_id,
            doc_id=f"subsidy:{source_id}:v1",
            source_type=SourceType.SUBSIDY,
            text=text,
            heading_path=("근거법령",),
            ordinal=ordinal,
            citation_locator="근거법령",
            content_hash=compute_content_hash(text),
            metadata={
                "source_id": source_id,
                "section_type": "legal_basis",
                "chunk_part": chunk_part,
                "chunk_part_count": 2,
            },
        )

    @staticmethod
    def _conditions(*active: str) -> dict[str, str | None]:
        codes = (
            "JA0101",
            "JA0102",
            "JA0201",
            "JA0202",
            "JA0203",
            "JA0204",
            "JA0205",
            "JA0326",
            "JA0327",
            "JA0328",
            "JA0313",
            "JA0314",
            "JA0315",
            "JA0316",
            "JA0317",
            "JA0318",
            "JA0319",
            "JA0320",
            "JA0322",
            "JA1101",
            "JA1102",
            "JA1103",
        )
        return {code: "Y" if code in active else None for code in codes}

    def test_sidecar_postfilter_backfills_and_reranks_semantic_candidates(self) -> None:
        candidates = tuple(
            self._candidate(source_id, rank)
            for rank, source_id in enumerate(
                ("male-only", "unknown-service", "female-1", "female-2"),
                start=1,
            )
        )
        store = self._Store(candidates)
        state = self._state(top_k=3)
        state["slots"]["gender"] = "female"
        sidecar = {
            "male-only": self._conditions("JA0101"),
            "female-1": self._conditions("JA0102"),
            "female-2": self._conditions("JA0102"),
        }

        result = search_policies(state, store, support_conditions=sidecar)

        kept = result["subsidy_chunks"]
        self.assertEqual(
            [item.chunk.metadata["source_id"] for item in kept],
            ["unknown-service", "female-1", "female-2"],
        )
        self.assertEqual([item.rank for item in kept], [1, 2, 3])
        self.assertEqual(
            store.exact_calls,
            [
                (
                    SourceType.SUBSIDY,
                    {"source_id": source_id, "section_type": "legal_basis"},
                )
                for source_id in ("unknown-service", "female-1", "female-2")
            ],
        )

    def test_selected_sources_load_sorted_deduplicated_legal_basis_parts(self) -> None:
        candidates = (
            self._candidate("service-a", 7, chunk_suffix="-one"),
            self._candidate("service-a", 8, chunk_suffix="-two"),
            self._candidate("service-b", 9),
        )
        a_part_1 = self._legal_basis_chunk(
            "service-a", ordinal=2, chunk_part=1, chunk_id="basis-a-1"
        )
        a_part_0 = self._legal_basis_chunk(
            "service-a", ordinal=2, chunk_part=0, chunk_id="basis-a-0"
        )
        b_part = self._legal_basis_chunk(
            "service-b", ordinal=1, chunk_part=0, chunk_id="basis-b-0"
        )
        store = self._Store(
            candidates,
            exact_chunks=(a_part_1, a_part_0, a_part_0, b_part),
        )

        result = search_policies(self._state(3), store)

        self.assertEqual(
            result["subsidy_chunks"],
            [
                replace(candidates[0], rank=1),
                replace(candidates[1], rank=2),
                replace(candidates[2], rank=3),
            ],
        )
        self.assertEqual([item.rank for item in candidates], [7, 8, 9])
        self.assertEqual(
            [chunk.chunk_id for chunk in result["subsidy_legal_basis_chunks"]],
            ["basis-a-0", "basis-a-1", "basis-b-0"],
        )
        self.assertEqual(
            store.exact_calls,
            [
                (
                    SourceType.SUBSIDY,
                    {"source_id": "service-a", "section_type": "legal_basis"},
                ),
                (
                    SourceType.SUBSIDY,
                    {"source_id": "service-b", "section_type": "legal_basis"},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
