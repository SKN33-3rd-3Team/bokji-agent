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

from src.rag_chatbot.graph.nodes.duplicate_benefit import (
    _find_restriction_clauses,
    _surrounding_sentence,
    check_duplicate_benefit,
)

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
        self.policy_id = subsidy_document.source_id
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


class _FakeChunk:
    def __init__(self, text: str):
        self.text = text
        self.metadata: dict = {}


class _FakeRetrieved:
    def __init__(self, text: str):
        self.chunk = _FakeChunk(text)


class RestrictionClauseTests(unittest.TestCase):
    """원문에서 중복수급 제한 조항을 찾는 경로 (2026-08-31 추가).

    metadata에 mutually_exclusive_with 필드가 없다는 이유로 예전에는 무조건
    "판정 불가"만 돌려줬다. 그런데 원천 데이터를 세어보니 정부24 문서
    10,963건 중 191건(1.7%)에 중복 제한 조항이 **실재한다** - 있는 근거를
    안 쓰고 있었다.
    """

    def test_finds_a_real_restriction_clause(self) -> None:
        # 실제 원천 데이터(유아학비 지원)에 있는 문장.
        text = (
            "유아학비 (누리과정) 지원\n지역: 전국\n지원대상\n\n"
            "○ 만 3~5세 유아 - 유치원 이용시간에 아이돌봄서비스 등과 중복지원 불가"
        )
        clauses = _find_restriction_clauses([_FakeRetrieved(text)])
        self.assertEqual(len(clauses), 1)
        self.assertIn("중복지원 불가", clauses[0])
        self.assertIn("아이돌봄서비스", clauses[0])  # 어떤 제도와 겹치는지도 살린다

    def test_quotes_the_original_sentence_verbatim(self) -> None:
        # 인용문이 원문에 실제로 있어야 N6/N14의 근거 검증과 어긋나지 않는다.
        text = "지원내용\n\n○ 중복수혜불가 조건이 적용됩니다"
        clause = _find_restriction_clauses([_FakeRetrieved(text)])[0]
        self.assertIn(clause.replace("...", "")[:10], text)

    def test_ignores_text_without_any_restriction(self) -> None:
        text = "지원대상\n\n○ 만 65세 이상 어르신 누구나 신청 가능합니다"
        self.assertEqual(_find_restriction_clauses([_FakeRetrieved(text)]), [])

    def test_does_not_treat_plain_mention_of_중복_as_a_restriction(self) -> None:
        # "중복"이라는 단어만으로 제한이라고 단정하면 오탐이 난다.
        text = "지원내용\n\n○ 다른 사업과 중복 지원 가능합니다"
        self.assertEqual(_find_restriction_clauses([_FakeRetrieved(text)]), [])

    def test_caps_the_number_of_quoted_clauses(self) -> None:
        text = "\n".join(f"○ 항목{i} 중복지원 불가" for i in range(10))
        self.assertLessEqual(len(_find_restriction_clauses([_FakeRetrieved(text)])), 3)

    def test_long_clause_is_truncated(self) -> None:
        text = "○ " + "가" * 400 + " 중복지원 불가"
        clause = _find_restriction_clauses([_FakeRetrieved(text)])[0]
        self.assertLessEqual(len(clause), 210)
