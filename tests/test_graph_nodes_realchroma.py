"""N9 노드를 실제 ChromaVectorStore로 검증 (FakeStore 대신)."""

from __future__ import annotations

from dataclasses import replace
import gc
import json
from pathlib import Path
import tempfile
import unittest

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, EvidenceStatus, SourceType
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from src.rag_chatbot.graph.nodes import determine_eligibility

try:
    import chromadb as _chromadb  # noqa: F401
except Exception:
    CHROMA_AVAILABLE = False
else:
    CHROMA_AVAILABLE = True

FIXTURES = Path(__file__).parent / "fixtures"


def _load_subsidy_document() -> Document:
    line = (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines()[0]
    return Document.from_dict(json.loads(line))


def _claim(policy_id: str, status: EvidenceStatus, reasons=("근거 문장",)) -> dict:
    return {
        "claim_id": f"{policy_id}-eligibility",
        "policy_id": policy_id,
        "claim_type": "eligibility",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": ["chunk-1"],
        "status": status,
        "reasons": list(reasons),
    }


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class DetermineEligibilityRealChromaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.persist_directory = Path(self.temporary_directory.name) / "index"
        self.config = VectorStoreConfig(
            persist_directory=self.persist_directory,
            collection_prefix="test_n9",
            batch_size=2,
        )
        self.store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)

        subsidy_document = _load_subsidy_document()
        self.policy_id = subsidy_document.doc_id
        self.subsidy_chunks = chunk_document(subsidy_document)

    def tearDown(self) -> None:
        del self.store
        gc.collect()
        self.temporary_directory.cleanup()

    def _sync_with_age_condition(self, age_start=None, age_end=None) -> None:
        chunks = tuple(
            replace(
                chunk,
                metadata={**chunk.metadata, "age_start": age_start, "age_end": age_end},
            )
            for chunk in self.subsidy_chunks
        )
        self.store.sync_snapshot(SourceType.SUBSIDY, chunks, snapshot_id="snap-001")

    def test_real_search_confirms_충족_when_age_condition_met(self) -> None:
        self._sync_with_age_condition(age_start=65, age_end=None)
        state = {
            "query_id": "q1",
            "slots": {"age": 70},
            "claim_plan": [_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = determine_eligibility(state, self.store)

        self.assertEqual(
            result,
            {
                "eligibility_verdicts": [
                    {"policy_id": self.policy_id, "verdict": "충족", "reasons": ["근거 문장"]}
                ]
            },
        )

    def test_real_search_confirms_미충족_when_age_condition_violated(self) -> None:
        self._sync_with_age_condition(age_start=65, age_end=None)
        state = {
            "query_id": "q2",
            "slots": {"age": 40},
            "claim_plan": [_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = determine_eligibility(state, self.store)

        verdict = result["eligibility_verdicts"][0]
        self.assertEqual(verdict["verdict"], "미충족")
        self.assertIn("연령 조건 미충족", verdict["reasons"][0])

    def test_real_search_against_never_synced_source_type_yields_미확인(self) -> None:
        # SUBSIDY 컬렉션 자체를 한 번도 동기화하지 않은 상태 - ChromaVectorStore.search()가
        # CollectionNotFoundError를 던지는 실제 동작을, 노드가 미확인으로 흡수하는지 확인.
        state = {
            "query_id": "q3",
            "slots": {"age": 70},
            "claim_plan": [_claim("policy-never-synced", EvidenceStatus.SUPPORTED)],
        }

        result = determine_eligibility(state, self.store)

        verdict = result["eligibility_verdicts"][0]
        self.assertEqual(verdict["verdict"], "미확인")

    def test_real_search_finds_nothing_for_unknown_policy_after_sync_yields_미확인(self) -> None:
        self._sync_with_age_condition(age_start=65, age_end=None)
        state = {
            "query_id": "q4",
            "slots": {"age": 70},
            "claim_plan": [_claim("policy-not-in-index", EvidenceStatus.SUPPORTED)],
        }

        result = determine_eligibility(state, self.store)

        verdict = result["eligibility_verdicts"][0]
        self.assertEqual(verdict["verdict"], "미확인")
        self.assertIn("재검색", verdict["reasons"][0])


if __name__ == "__main__":
    unittest.main()
