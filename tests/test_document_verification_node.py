from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, EvidenceStatus, RetrievedChunk, SourceType

from rag_chatbot.graph.nodes.document_verification import verify_official_documents

FIXTURES = Path(__file__).parent / "fixtures"


def load_subsidy_document() -> Document:
    for line in (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        document = Document.from_dict(json.loads(line))
        if document.source_type is SourceType.SUBSIDY:
            return document
    raise AssertionError("fixture must contain a subsidy document")


def as_retrieved(chunk, *, rank: int) -> RetrievedChunk:
    return RetrievedChunk(
        query_id="q-1",
        chunk=chunk,
        rank=rank,
        score=1.0 / rank,
        score_type="cosine_distance",
        retriever_version="test-v1",
        index_name="subsidy",
    )


class VerifyOfficialDocumentsNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        document = load_subsidy_document()
        chunks = chunk_document(document)
        self.subsidy_chunks = [
            as_retrieved(chunk, rank=i + 1) for i, chunk in enumerate(chunks)
        ]
        self.policy_id = self.subsidy_chunks[0].chunk.doc_id
        # 실제 원문에 있는 문장을 그대로 가져와서 "원문 그대로 발췌" 전제를 재현
        self.verbatim_reason = "국공립 및 사립유치원에 다니는 3~5세 유아입니다."
        self.assertIn(
            self.verbatim_reason,
            "\n".join(c.chunk.text for c in self.subsidy_chunks),
            "테스트 전제 확인: 이 문장이 실제 픽스처 원문에 있어야 함",
        )

    def _claim(self, **overrides) -> dict:
        base = {
            "claim_id": "c1",
            "policy_id": self.policy_id,
            "claim_type": "eligibility",
            "doc_check_required": True,
            "law_check_required": False,
            "evidence_chunk_ids": [],
            "status": "pending",
            "reasons": [self.verbatim_reason],
        }
        base.update(overrides)
        return base

    def test_verbatim_reason_found_in_source_is_supported(self) -> None:
        state = {"claim_plan": [self._claim()], "subsidy_chunks": self.subsidy_chunks}

        update = verify_official_documents(state)

        result = update["claim_plan"][0]
        self.assertEqual(result["status"], EvidenceStatus.SUPPORTED.value)
        self.assertTrue(result["evidence_chunk_ids"])

    def test_reason_not_in_source_is_unsupported(self) -> None:
        claim = self._claim(reasons=["원문에 절대 없을 만한 문장입니다"])
        state = {"claim_plan": [claim], "subsidy_chunks": self.subsidy_chunks}

        update = verify_official_documents(state)

        self.assertEqual(update["claim_plan"][0]["status"], EvidenceStatus.UNSUPPORTED.value)
        self.assertEqual(update["claim_plan"][0]["evidence_chunk_ids"], [])

    def test_partially_matching_reasons_is_partial(self) -> None:
        claim = self._claim(
            reasons=[self.verbatim_reason, "이건 원문에 없는 문장입니다"]
        )
        state = {"claim_plan": [claim], "subsidy_chunks": self.subsidy_chunks}

        update = verify_official_documents(state)

        self.assertEqual(update["claim_plan"][0]["status"], EvidenceStatus.PARTIAL.value)

    def test_doc_check_required_false_passes_through_unchanged(self) -> None:
        claim = self._claim(doc_check_required=False, reasons=["아무 문장"])
        state = {"claim_plan": [claim], "subsidy_chunks": self.subsidy_chunks}

        update = verify_official_documents(state)

        self.assertEqual(update["claim_plan"][0], claim)

    def test_unknown_policy_id_is_unsupported(self) -> None:
        claim = self._claim(policy_id="존재하지-않는-정책")
        state = {"claim_plan": [claim], "subsidy_chunks": self.subsidy_chunks}

        update = verify_official_documents(state)

        self.assertEqual(update["claim_plan"][0]["status"], EvidenceStatus.UNSUPPORTED.value)

    def test_empty_claim_plan_yields_empty_result(self) -> None:
        update = verify_official_documents(
            {"claim_plan": [], "subsidy_chunks": self.subsidy_chunks}
        )

        self.assertEqual(update["claim_plan"], [])


if __name__ == "__main__":
    unittest.main()
