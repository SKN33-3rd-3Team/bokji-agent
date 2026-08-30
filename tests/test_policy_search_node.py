from __future__ import annotations

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

from rag_chatbot.graph.nodes.policy_search import search_policies

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
            "slots": {"interests": ["유아학비"], "region_names": []},
        }

        update = search_policies(state, self.store, top_k=3)

        self.assertIn("subsidy_chunks", update)
        chunks = update["subsidy_chunks"]
        self.assertGreater(len(chunks), 0)
        self.assertTrue(all(c.index_name == "subsidy" for c in chunks))
        self.assertTrue(all(c.query_id == "q-1" for c in chunks))

    def test_search_policies_falls_back_to_broad_query_when_no_interests(self) -> None:
        state = {"query_id": "q-2", "slots": {"region_names": []}}

        update = search_policies(state, self.store, top_k=3)

        self.assertIn("subsidy_chunks", update)
        self.assertGreater(len(update["subsidy_chunks"]), 0)

    def test_search_policies_requires_query_id(self) -> None:
        with self.assertRaises(ValueError):
            search_policies({"slots": {}}, self.store)


if __name__ == "__main__":
    unittest.main()
