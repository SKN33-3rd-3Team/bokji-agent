from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, RetrievedChunk, SourceType

from rag_chatbot.graph.nodes.claim_plan import plan_claims

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


class FakeClaimExtractor:
    """실제 LLM 대신, 결정론적으로 3종 claim(자격/금액/중복수급)을 만든다.

    진짜 LLM 품질을 검증하는 게 아니라, plan_claims()의 배선(청크 ->
    ClaimDraft 변환, ID 유일성, 원자적 분해)이 맞는지만 증명하는 용도.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def extract(self, *, policy_id: str, text: str) -> list[dict]:
        self.calls.append((policy_id, text))
        return [
            {"claim_type": "eligibility", "reasons": ["fake: 자격 조건 언급"]},
            {"claim_type": "amount", "reasons": ["fake: 지원금액 언급"]},
            {
                "claim_type": "duplicate",
                "law_check_required": True,
                "reasons": ["fake: 중복수급 언급"],
            },
        ]


class PlanClaimsNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        document = load_subsidy_document()
        chunks = chunk_document(document)
        self.subsidy_chunks = [
            as_retrieved(chunk, rank=i + 1) for i, chunk in enumerate(chunks)
        ]
        self.extractor = FakeClaimExtractor()

    def test_plan_claims_decomposes_each_chunk_into_three_claim_types(self) -> None:
        state = {"subsidy_chunks": self.subsidy_chunks}

        update = plan_claims(state, self.extractor)

        self.assertIn("claim_plan", update)
        claim_plan = update["claim_plan"]
        self.assertEqual(len(claim_plan), len(self.subsidy_chunks) * 3)
        claim_types = {c["claim_type"] for c in claim_plan}
        self.assertEqual(claim_types, {"eligibility", "amount", "duplicate"})

    def test_claim_ids_are_unique(self) -> None:
        state = {"subsidy_chunks": self.subsidy_chunks}

        update = plan_claims(state, self.extractor)

        claim_ids = [c["claim_id"] for c in update["claim_plan"]]
        self.assertEqual(len(claim_ids), len(set(claim_ids)))

    def test_doc_check_required_defaults_conservatively_to_true(self) -> None:
        state = {"subsidy_chunks": self.subsidy_chunks}

        update = plan_claims(state, self.extractor)

        self.assertTrue(all(c["doc_check_required"] for c in update["claim_plan"]))

    def test_extractor_is_called_once_per_chunk(self) -> None:
        state = {"subsidy_chunks": self.subsidy_chunks}

        plan_claims(state, self.extractor)

        self.assertEqual(len(self.extractor.calls), len(self.subsidy_chunks))

    def test_no_subsidy_chunks_yields_empty_claim_plan(self) -> None:
        update = plan_claims({"subsidy_chunks": []}, self.extractor)

        self.assertEqual(update["claim_plan"], [])


if __name__ == "__main__":
    unittest.main()
