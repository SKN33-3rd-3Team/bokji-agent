from __future__ import annotations

from datetime import date
import gc
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, SourceType
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from rag_chatbot.graph.nodes.policy_search import _build_query, search_policies

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
        def __init__(self) -> None:
            self.top_k = None
            self.search_filter = None

        def search(self, source_type, query, *, query_id, top_k, search_filter):
            self.top_k = top_k
            self.search_filter = search_filter
            return ()

    def _state(self, top_k=7):
        return {
            "query_id": "q-top-k",
            "as_of": date(2026, 1, 1),
            "slots": {"region_names": []},
            "policy_top_k": top_k,
        }

    def test_uses_top_k_from_graph_state(self) -> None:
        store = self._Store()
        search_policies(self._state(7), store)
        self.assertEqual(store.top_k, 7)

    def test_explicit_node_argument_overrides_state(self) -> None:
        store = self._Store()
        search_policies(self._state(7), store, top_k=3)
        self.assertEqual(store.top_k, 3)

    def test_rejects_out_of_range_top_k(self) -> None:
        for value in (0, 21, True, "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                search_policies(self._state(value), self._Store())

    def test_self_age_is_connected_to_search_filter(self) -> None:
        store = self._Store()
        state = self._state()
        state["slots"].update(
            {
                "birth_date": "1960-01-01",
                "age": 66,
                "age_year_based": 66,
                "age_subject": "self",
            }
        )
        search_policies(state, store)
        self.assertEqual(store.search_filter.age, 66)
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


if __name__ == "__main__":
    unittest.main()
