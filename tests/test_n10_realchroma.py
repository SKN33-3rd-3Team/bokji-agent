"""N10 노드를 실제 ChromaVectorStore로 검증 (FakeStore 대신)."""

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

from src.rag_chatbot.graph.nodes.benefit_calculator import calculate_benefit_amount

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


def _amount_claim(policy_id: str, status: EvidenceStatus) -> dict:
    return {
        "claim_id": f"{policy_id}-amount",
        "policy_id": policy_id,
        "claim_type": "amount",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": ["chunk-1"],
        "status": status,
        "reasons": ["근거 문장"],
    }


def _verdict(policy_id: str, verdict: str) -> dict:
    return {"policy_id": policy_id, "verdict": verdict, "reasons": []}


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class CalculateBenefitAmountRealChromaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.persist_directory = Path(self.temporary_directory.name) / "index"
        self.config = VectorStoreConfig(
            persist_directory=self.persist_directory,
            collection_prefix="test_n10",
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

    def _sync_with_amount(self, amount) -> None:
        chunks = tuple(
            replace(chunk, metadata={**chunk.metadata, "amount": amount})
            for chunk in self.subsidy_chunks
        )
        self.store.sync_snapshot(SourceType.SUBSIDY, chunks, snapshot_id="snap-001")

    def test_real_search_uses_structured_amount_field(self) -> None:
        self._sync_with_amount(300000)
        state = {
            "query_id": "q1",
            "eligibility_verdicts": [_verdict(self.policy_id, "충족")],
            "claim_plan": [_amount_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, self.store)

        entry = result["benefit_amounts"][0]
        self.assertEqual(entry["policy_id"], self.policy_id)
        self.assertEqual(entry["amount"], 300000.0)
        self.assertTrue(entry["rule_chunk_id"])

    def test_real_search_without_amount_metadata_yields_none_not_guessed(self) -> None:
        self.store.sync_snapshot(
            SourceType.SUBSIDY, self.subsidy_chunks, snapshot_id="snap-001"
        )
        state = {
            "query_id": "q2",
            "eligibility_verdicts": [_verdict(self.policy_id, "충족")],
            "claim_plan": [_amount_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, self.store)

        entry = result["benefit_amounts"][0]
        self.assertIsNone(entry["amount"])
        self.assertIn("구조화된 금액 필드가 없어", entry["calculation_note"])

    def test_real_search_against_never_synced_source_type_yields_none(self) -> None:
        state = {
            "query_id": "q3",
            "eligibility_verdicts": [_verdict("policy-never-synced", "충족")],
            "claim_plan": [_amount_claim("policy-never-synced", EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, self.store)

        entry = result["benefit_amounts"][0]
        self.assertIsNone(entry["amount"])


if __name__ == "__main__":
    unittest.main()
