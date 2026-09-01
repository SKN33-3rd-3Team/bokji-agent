from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import (
    Document,
    RetrievedChunk,
    SourceType,
    compute_content_hash,
)

from rag_chatbot.graph.nodes.claim_plan import plan_claims
from rag_chatbot.graph.nodes.law_source_resolver import LawSourceResolver

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

    def test_required_law_sources_populated_when_law_check_required(self) -> None:
        """N7 리뷰 피드백 #3: law_check_required=True인 claim에
        required_aspects/required_law_sources가 채워지는지 - 근거법령 섹션이
        있는 문서로 직접 구성해서 끝까지 연동되는지 증명."""

        from datetime import datetime, timezone
        from rag_design.contracts import Document, Section, SourceType, compute_content_hash

        legal_basis_content = "유아교육법(제24조)||영유아보육법(제34조)"
        sections = (
            Section(
                heading_path=("지원 대상",),
                content="3~5세 유아입니다.",
                metadata={"section_type": "support_target"},
            ),
            Section(
                heading_path=("근거법령",),
                content=legal_basis_content,
                metadata={"section_type": "legal_basis"},
            ),
        )
        content = "\n".join(s.content for s in sections)
        document = Document(
            schema_version="1.0",
            doc_id="subsidy:test-policy:2026-01-01",
            source_type=SourceType.SUBSIDY,
            source_name="테스트",
            source_id="test-policy",
            source_url="https://www.gov.kr/test",
            title="테스트 정책",
            content=content,
            sections=sections,
            collected_at=datetime.now(timezone.utc).isoformat(),
            source_updated_at=None,
            effective_from="2026-01-01",
            effective_to=None,
            license="테스트",
            content_hash=compute_content_hash(content),
            metadata={"region_scope": "national", "region_names": ["전국"]},
            parse_warnings=(),
            sensitive_data_status="clear",
        )
        chunks = chunk_document(document)
        subsidy_chunks = [as_retrieved(c, rank=i + 1) for i, c in enumerate(chunks)]

        class LawRequiringExtractor:
            def extract(self, *, policy_id: str, text: str) -> list[dict]:
                if "유아" not in text:  # 근거법령 청크 자체에는 claim 안 만듦
                    return []
                return [
                    {
                        "claim_type": "eligibility",
                        "law_check_required": True,
                        "reasons": [text],
                        "required_aspects": ["나이 자격요건"],
                    }
                ]

        law_index = {
            "유아교육법": {"law_type": "law", "source_id": "L001"},
            "영유아보육법": {"law_type": "law", "source_id": "L002"},
        }

        class FakeResolver:
            def resolve(self, law_name: str):
                return law_index.get(law_name)

        state = {"subsidy_chunks": subsidy_chunks}
        update = plan_claims(state, LawRequiringExtractor(), FakeResolver())

        claim_plan = update["claim_plan"]
        self.assertEqual(len(claim_plan), 1)  # 근거법령 청크는 claim 안 만들어짐
        claim = claim_plan[0]
        self.assertEqual(claim["required_aspects"], ["나이 자격요건"])
        self.assertEqual(
            claim["required_law_sources"],
            [
                {"law_type": "law", "source_id": "L001"},
                {"law_type": "law", "source_id": "L002"},
            ],
        )

    def test_required_law_sources_empty_when_no_resolver_given(self) -> None:
        """resolver를 안 주면 에러 없이 그냥 빈 리스트로 나가야 함 (선택적 기능)."""

        state = {"subsidy_chunks": self.subsidy_chunks}
        law_extractor = FakeClaimExtractor()  # duplicate claim은 law_check_required=True

        update = plan_claims(state, law_extractor)  # law_resolver 생략

        law_claims = [c for c in update["claim_plan"] if c["claim_type"] == "duplicate"]
        self.assertTrue(law_claims)
        self.assertTrue(all(c["required_law_sources"] == [] for c in law_claims))

    def test_canonical_legal_basis_parts_are_ordered_deduped_and_preferred(self) -> None:
        source_id = self.subsidy_chunks[0].chunk.metadata["source_id"]
        base = self.subsidy_chunks[0].chunk

        def legal_basis_chunk(raw_content: str, *, part: int, chunk_id: str):
            text = f"테스트 정책\n근거법령\n\n{raw_content}"
            return replace(
                base,
                chunk_id=chunk_id,
                text=text,
                heading_path=("근거법령",),
                ordinal=10,
                citation_locator="근거법령",
                content_hash=compute_content_hash(text),
                metadata={
                    **base.metadata,
                    "section_type": "legal_basis",
                    "chunk_part": part,
                    "chunk_part_count": 2,
                },
            )

        canonical = [
            legal_basis_chunk("A법(제1조)||B법(제2조)", part=0, chunk_id="basis-0"),
            legal_basis_chunk("B법(제3조)||C법(제4조)", part=1, chunk_id="basis-1"),
        ]
        semantic_basis = as_retrieved(
            legal_basis_chunk("무시법(제1조)", part=0, chunk_id="semantic-basis"),
            rank=len(self.subsidy_chunks) + 1,
        )
        law_index = {
            "A법": {"law_type": "law", "source_id": "100"},
            "B법": {"law_type": "law", "source_id": "200"},
            "C법": {"law_type": "law", "source_id": "300"},
            "무시법": {"law_type": "law", "source_id": "999"},
        }

        class RecordingResolver:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def resolve(self, law_name: str):
                self.calls.append(law_name)
                return law_index.get(law_name)

        resolver = RecordingResolver()
        state = {
            "subsidy_chunks": [*self.subsidy_chunks, semantic_basis],
            "subsidy_legal_basis_chunks": canonical,
        }

        update = plan_claims(state, self.extractor, resolver)

        law_claims = [
            claim
            for claim in update["claim_plan"]
            if claim.get("law_check_required")
        ]
        self.assertTrue(law_claims)
        self.assertTrue(
            all(
                claim["required_law_sources"]
                == [
                    {"law_type": "law", "source_id": "100"},
                    {"law_type": "law", "source_id": "200"},
                    {"law_type": "law", "source_id": "300"},
                ]
                for claim in law_claims
            )
        )
        self.assertEqual(resolver.calls, ["A법", "B법", "B법", "C법"])
        self.assertNotIn("무시법", resolver.calls)
        self.assertTrue(
            all(policy_id == source_id for policy_id, _ in self.extractor.calls)
        )

    def test_empty_canonical_legal_basis_does_not_fall_back_to_semantic_chunk(
        self,
    ) -> None:
        base = self.subsidy_chunks[0].chunk
        text = "테스트 정책\n근거법령\n\n무시법(제1조)"
        semantic_basis = as_retrieved(
            replace(
                base,
                chunk_id="semantic-basis",
                text=text,
                heading_path=("근거법령",),
                ordinal=10,
                citation_locator="근거법령",
                content_hash=compute_content_hash(text),
                metadata={**base.metadata, "section_type": "legal_basis"},
            ),
            rank=len(self.subsidy_chunks) + 1,
        )

        class RecordingResolver:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def resolve(self, law_name: str):
                self.calls.append(law_name)
                return {"law_type": "law", "source_id": "999"}

        resolver = RecordingResolver()
        update = plan_claims(
            {
                "subsidy_chunks": [*self.subsidy_chunks, semantic_basis],
                "subsidy_legal_basis_chunks": [],
            },
            self.extractor,
            resolver,
        )

        law_claims = [
            claim
            for claim in update["claim_plan"]
            if claim.get("law_check_required")
        ]
        self.assertTrue(law_claims)
        self.assertTrue(
            all(claim["required_law_sources"] == [] for claim in law_claims)
        )
        self.assertEqual(resolver.calls, [])

    def test_policy_id_matches_chunk_source_id(self) -> None:
        """N7 리뷰 피드백: policy_id는 doc_id가 아니라 원본 source_id여야 함."""

        state = {"subsidy_chunks": self.subsidy_chunks}

        update = plan_claims(state, self.extractor)

        expected_source_ids = {
            chunk.chunk.metadata["source_id"] for chunk in self.subsidy_chunks
        }
        actual_policy_ids = {c["policy_id"] for c in update["claim_plan"]}
        self.assertEqual(actual_policy_ids, expected_source_ids)
        # doc_id(합성 해시값)가 아니라는 것도 명시적으로 확인
        doc_ids = {chunk.chunk.doc_id for chunk in self.subsidy_chunks}
        self.assertTrue(actual_policy_ids.isdisjoint(doc_ids - expected_source_ids))


if __name__ == "__main__":
    unittest.main()
