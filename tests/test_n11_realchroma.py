"""N11 노드를 실제 ChromaVectorStore로 검증 (FakeStore 대신)."""

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

from src.rag_chatbot.graph.nodes.duplicate_benefit import check_duplicate_benefit

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


def _dup_claim(policy_id: str, status: EvidenceStatus) -> dict:
    return {
        "claim_id": f"{policy_id}-duplicate",
        "policy_id": policy_id,
        "claim_type": "duplicate",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": ["chunk-1"],
        "status": status,
        "reasons": ["근거 문장"],
    }


def _verdict(policy_id: str, verdict: str) -> dict:
    return {"policy_id": policy_id, "verdict": verdict, "reasons": []}


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class CheckDuplicateBenefitRealChromaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.persist_directory = Path(self.temporary_directory.name) / "index"
        self.config = VectorStoreConfig(
            persist_directory=self.persist_directory,
            collection_prefix="test_n11",
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

    def _sync_with_exclusion(self, mutually_exclusive_with) -> None:
        chunks = tuple(
            replace(
                chunk,
                metadata={
                    **chunk.metadata,
                    "mutually_exclusive_with": mutually_exclusive_with,
                },
            )
            for chunk in self.subsidy_chunks
        )
        self.store.sync_snapshot(SourceType.SUBSIDY, chunks, snapshot_id="snap-001")

    def test_real_search_confirms_불가_when_conflict_metadata_matches_eligible_policy(self) -> None:
        self._sync_with_exclusion(["other-policy"])
        state = {
            "query_id": "q1",
            "eligibility_verdicts": [
                _verdict(self.policy_id, "충족"),
                _verdict("other-policy", "충족"),
            ],
            "claim_plan": [_dup_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = check_duplicate_benefit(state, self.store)

        verdict = result["duplicate_verdicts"][0]
        self.assertEqual(verdict["status"], "불가")
        self.assertEqual(verdict["conflicts_with"], ["other-policy"])

    def test_real_search_no_exclusion_metadata_defaults_to_미확인(self) -> None:
        self.store.sync_snapshot(
            SourceType.SUBSIDY, self.subsidy_chunks, snapshot_id="snap-001"
        )
        state = {
            "query_id": "q2",
            "eligibility_verdicts": [_verdict(self.policy_id, "충족")],
            "claim_plan": [_dup_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = check_duplicate_benefit(state, self.store)

        verdict = result["duplicate_verdicts"][0]
        self.assertEqual(verdict["status"], "미확인")

    def test_real_search_against_never_synced_source_type_yields_미확인(self) -> None:
        state = {
            "query_id": "q3",
            "eligibility_verdicts": [_verdict("policy-never-synced", "충족")],
            "claim_plan": [_dup_claim("policy-never-synced", EvidenceStatus.SUPPORTED)],
        }

        result = check_duplicate_benefit(state, self.store)

        verdict = result["duplicate_verdicts"][0]
        self.assertEqual(verdict["status"], "미확인")


if __name__ == "__main__":
    unittest.main()
