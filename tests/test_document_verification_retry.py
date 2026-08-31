from __future__ import annotations

import gc
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, EvidenceStatus, SourceType
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from rag_chatbot.graph.nodes.document_verification import verify_official_documents

try:
    import chromadb as _chromadb  # noqa: F401
except Exception:
    CHROMA_AVAILABLE = False
else:
    CHROMA_AVAILABLE = True

FIXTURES = Path(__file__).parent / "fixtures"


def load_subsidy_document() -> Document:
    for line in (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        document = Document.from_dict(json.loads(line))
        if document.source_type is SourceType.SUBSIDY:
            return document
    raise AssertionError("fixture must contain a subsidy document")


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class RetryWidenSearchTests(unittest.TestCase):
    """N7 리뷰 피드백 #5: 재시도 시 top-K 범위 밖까지 넓혀서 재검색하는지 검증.

    실제 Chroma DB에 정책 청크 3개(지원대상/지원내용/신청방법)를 다 색인해두고,
    N4가 "1개만 찾아온 척"(narrow_chunks) State를 구성한다. 그 근거는
    N4가 못 찾은 다른 청크에만 있는 상황을 만들어서, 재시도 없이는
    unsupported, 재시도로는 supported가 되는지 확인한다.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        config = VectorStoreConfig(
            persist_directory=Path(self.temporary_directory.name) / "index",
            collection_prefix="test_n6_retry",
        )
        self.store = ChromaVectorStore(HashEmbeddingProvider(64), config)

        document = load_subsidy_document()
        self.all_chunks = list(chunk_document(document))
        self.assertEqual(len(self.all_chunks), 3, "픽스처가 3개 청크여야 테스트 성립")
        self.store.sync_snapshot(
            SourceType.SUBSIDY, self.all_chunks, snapshot_id="subsidy-001"
        )

        # "신청 방법" 청크(3번째)에만 있는 문장을 근거로 쓴다.
        self.application_method_chunk = next(
            c for c in self.all_chunks if "신청" in c.text and "청" in c.heading_path[0]
        )
        self.reason_only_in_third_chunk = "읍면동 주민센터 또는 온라인에서 신청합니다."
        self.assertIn(self.reason_only_in_third_chunk, self.application_method_chunk.text)

        self.policy_id = self.all_chunks[0].metadata["source_id"]

        # N4가 "지원 대상" 청크 1개만 찾아온 척 (narrow top-K 시뮬레이션).
        narrow_chunk = self.all_chunks[0]
        self.assertNotIn(self.reason_only_in_third_chunk, narrow_chunk.text)
        self.narrow_subsidy_chunks = [
            self._as_retrieved(narrow_chunk, rank=1),
        ]

    def _as_retrieved(self, chunk, *, rank: int):
        from rag_design.contracts import RetrievedChunk

        return RetrievedChunk(
            query_id="q-1",
            chunk=chunk,
            rank=rank,
            score=1.0 / rank,
            score_type="cosine_distance",
            retriever_version="test-v1",
            index_name="subsidy",
        )

    def tearDown(self) -> None:
        del self.store
        gc.collect()
        self.temporary_directory.cleanup()

    def _claim(self, **overrides) -> dict:
        base = {
            "claim_id": "c1",
            "policy_id": self.policy_id,
            "claim_type": "application_method",
            "doc_check_required": True,
            "law_check_required": False,
            "evidence_chunk_ids": [],
            "status": "pending",
            "reasons": [self.reason_only_in_third_chunk],
            "doc_retry_count": 0,
        }
        base.update(overrides)
        return base

    def test_first_attempt_without_retry_is_unsupported(self) -> None:
        """N4가 narrow하게 가져온 범위 안에는 근거가 없으니 미확인이어야 함."""

        state = {
            "query_id": "q-1",
            "claim_plan": [self._claim(doc_retry_count=0)],
            "subsidy_chunks": self.narrow_subsidy_chunks,
        }

        update = verify_official_documents(state, store=self.store)

        self.assertEqual(update["claim_plan"][0]["status"], EvidenceStatus.UNSUPPORTED.value)

    def test_retry_without_store_stays_unsupported_no_crash(self) -> None:
        """store 없이 재시도해도 에러 없이, 그냥 기존 범위 내에서만 검증돼야 함."""

        state = {
            "query_id": "q-1",
            "claim_plan": [self._claim(doc_retry_count=1)],
            "subsidy_chunks": self.narrow_subsidy_chunks,
        }

        update = verify_official_documents(state)  # store 생략

        self.assertEqual(update["claim_plan"][0]["status"], EvidenceStatus.UNSUPPORTED.value)

    def test_retry_with_store_widens_search_and_finds_evidence(self) -> None:
        """재시도 + store 제공 시, top-K 밖 청크까지 찾아서 근거를 확인해야 함."""

        state = {
            "query_id": "q-1",
            "claim_plan": [self._claim(doc_retry_count=1)],
            "subsidy_chunks": self.narrow_subsidy_chunks,
        }

        update = verify_official_documents(state, store=self.store, widen_top_k=10)

        result = update["claim_plan"][0]
        self.assertEqual(result["status"], EvidenceStatus.SUPPORTED.value)
        self.assertIn(
            self.application_method_chunk.chunk_id, result["evidence_chunk_ids"]
        )

    def test_non_retry_claim_does_not_trigger_widen_search_even_with_store(self) -> None:
        """doc_retry_count=0이면 store가 있어도 재검색 안 하고 narrow 범위만 봐야 함."""

        state = {
            "query_id": "q-1",
            "claim_plan": [self._claim(doc_retry_count=0)],
            "subsidy_chunks": self.narrow_subsidy_chunks,
        }

        update = verify_official_documents(state, store=self.store)

        self.assertEqual(update["claim_plan"][0]["status"], EvidenceStatus.UNSUPPORTED.value)


if __name__ == "__main__":
    unittest.main()
