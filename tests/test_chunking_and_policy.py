from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
import unittest

from rag_design.chunking import CHUNKING_VERSION, ChunkingConfig, chunk_document
from rag_design.contracts import Section, SourceType, compute_content_hash
from rag_design.index_policy import (
    MetadataFilter,
    QueryScope,
    chunk_matches_filter,
    route_indexes,
    validate_cross_index_merge,
)
from rag_design.policy import EvidenceState, decide_abstention
from rag_design.validation import validate_chunk_batch

from tests.test_contracts_and_citations import load_documents


class ChunkingAndPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.subsidy, self.law = load_documents()

    def test_subsidy_sections_remain_separate(self) -> None:
        chunks = chunk_document(self.subsidy)
        self.assertEqual(len(chunks), 3)
        self.assertEqual([chunk.citation_locator for chunk in chunks], ["지원 대상", "지원 내용", "신청 방법"])
        self.assertIn("지역: 전국", chunks[0].text)
        self.assertEqual(chunks[0].metadata["region_scope"], "national")
        self.assertEqual(chunks[0].metadata["region_names"], ["전국"])
        self.assertTrue(
            chunks[0].metadata["chunking_version"].startswith(
                f"{CHUNKING_VERSION}:"
            )
        )

    def test_chunk_ids_are_deterministic_and_unique(self) -> None:
        first = chunk_document(self.subsidy)
        second = chunk_document(self.subsidy)
        self.assertEqual([item.chunk_id for item in first], [item.chunk_id for item in second])
        self.assertEqual(len({item.chunk_id for item in first}), len(first))
        self.assertEqual(
            first[0].chunk_id,
            "subsidy:000000465790:2026-01-29:chunk:95647f8d27b2122c9bc0",
        )
        self.assertEqual(
            first[0].metadata["chunking_version"],
            "structure-v2:max_chars=800:overlap_chars=100",
        )

    def test_duplicate_structure_path_fails_instead_of_reusing_chunk_id(self) -> None:
        duplicate = replace(
            self.subsidy,
            sections=(self.subsidy.sections[0], self.subsidy.sections[0]),
        )
        with self.assertRaisesRegex(ValueError, "duplicate chunk_id"):
            chunk_document(duplicate)

    def test_external_duplicate_chunk_id_is_rejected(self) -> None:
        chunk = chunk_document(self.subsidy)[0]
        duplicate = replace(chunk, ordinal=chunk.ordinal + 1)
        issues = validate_chunk_batch((chunk, duplicate), (self.subsidy,))
        self.assertIn("duplicate_chunk_id", {issue.code for issue in issues})

    def test_chunk_id_and_version_change_with_chunking_config(self) -> None:
        first = chunk_document(
            self.subsidy, ChunkingConfig(max_chars=800, overlap_chars=100)
        )[0]
        second = chunk_document(
            self.subsidy, ChunkingConfig(max_chars=900, overlap_chars=100)
        )[0]
        self.assertNotEqual(first.chunk_id, second.chunk_id)
        self.assertNotEqual(
            first.metadata["chunking_version"], second.metadata["chunking_version"]
        )

    def test_chunk_batch_requires_parent_metadata_and_reproducible_text(self) -> None:
        chunk = chunk_document(self.law)[0]
        missing_metadata = replace(chunk, metadata={})
        missing_issues = validate_chunk_batch((missing_metadata,), (self.law,))
        self.assertIn(
            "missing_chunk_metadata", {issue.code for issue in missing_issues}
        )

        changed_text = "부모 법령 조문과 무관한 색인 본문입니다."
        manipulated = replace(
            chunk,
            text=changed_text,
            content_hash=compute_content_hash(changed_text),
        )
        manipulated_issues = validate_chunk_batch((manipulated,), (self.law,))
        self.assertIn(
            "chunk_batch_not_reproducible",
            {issue.code for issue in manipulated_issues},
        )

    def test_law_locator_retains_branch_article_paragraph_item_and_subitem(self) -> None:
        section = Section(
            heading_path=("제10조의2(가지번호)", "제2항", "제3호", "나목"),
            content="구조 보존 검사용 공개 테스트 문장입니다.",
            metadata={"section_type": "subitem"},
        )
        document = replace(self.law, sections=(section,))
        chunk = chunk_document(document)[0]
        self.assertEqual(
            chunk.citation_locator,
            "제10조의2(가지번호) > 제2항 > 제3호 > 나목",
        )
        self.assertIn(chunk.citation_locator, chunk.text)

    def test_long_section_splits_with_maximum_length(self) -> None:
        section = Section(
            heading_path=("지원 내용",),
            content=("복지 지원 내용을 설명합니다. " * 80).strip(),
            metadata={"section_type": "support_details"},
        )
        document = replace(self.subsidy, sections=(section,))
        chunks = chunk_document(document, ChunkingConfig(max_chars=200, overlap_chars=20))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 200 for chunk in chunks))

    def test_effective_interval_is_half_open(self) -> None:
        chunk = chunk_document(self.law)[0]
        chunk = replace(
            chunk,
            metadata={**chunk.metadata, "effective_from": "2025-10-01", "effective_to": "2026-01-01"},
        )
        self.assertTrue(
            chunk_matches_filter(
                chunk, MetadataFilter(SourceType.LAW, date(2025, 10, 1))
            )
        )
        self.assertFalse(
            chunk_matches_filter(
                chunk, MetadataFilter(SourceType.LAW, date(2026, 1, 1))
            )
        )

    def test_law_filter_fails_closed_without_effective_date(self) -> None:
        chunk = chunk_document(self.law)[0]
        undated = replace(
            chunk,
            metadata={**chunk.metadata, "effective_from": None},
        )
        self.assertFalse(
            chunk_matches_filter(
                undated, MetadataFilter(SourceType.LAW, date(2025, 10, 1))
            )
        )

    def test_national_subsidy_matches_a_regional_filter(self) -> None:
        chunk = chunk_document(self.subsidy)[0]
        self.assertTrue(
            chunk_matches_filter(
                chunk,
                MetadataFilter(
                    SourceType.SUBSIDY,
                    date(2026, 8, 26),
                    region_names=("서울특별시",),
                ),
            )
        )

    def test_missing_region_metadata_does_not_match_a_regional_filter(self) -> None:
        chunk = chunk_document(self.subsidy)[0]
        chunk = replace(
            chunk,
            metadata={
                key: value for key, value in chunk.metadata.items() if key != "region_names"
            },
        )
        self.assertFalse(
            chunk_matches_filter(
                chunk,
                MetadataFilter(
                    SourceType.SUBSIDY,
                    date(2026, 8, 26),
                    region_names=("서울특별시",),
                ),
            )
        )

    def test_regional_subsidy_uses_exact_hierarchical_name_intersection(self) -> None:
        regional_document = replace(
            self.subsidy,
            metadata={
                **self.subsidy.metadata,
                "region_scope": "regional",
                "region_names": ["서울특별시", "서울특별시 강남구"],
            },
        )
        chunk = chunk_document(regional_document)[0]
        for region_name in ("서울특별시", "서울특별시 강남구"):
            with self.subTest(region_name=region_name):
                self.assertTrue(
                    chunk_matches_filter(
                        chunk,
                        MetadataFilter(
                            SourceType.SUBSIDY,
                            date(2026, 8, 26),
                            region_names=(region_name,),
                        ),
                    )
                )
        self.assertFalse(
            chunk_matches_filter(
                chunk,
                MetadataFilter(
                    SourceType.SUBSIDY,
                    date(2026, 8, 26),
                    region_names=("부산광역시",),
                ),
            )
        )
        self.assertIn("지역: 서울특별시, 서울특별시 강남구", chunk.text)

    def test_unknown_region_is_unfiltered_only(self) -> None:
        unknown_document = replace(
            self.subsidy,
            metadata={
                **self.subsidy.metadata,
                "region_scope": "unknown",
                "region_names": [],
            },
        )
        chunk = chunk_document(unknown_document)[0]
        self.assertTrue(
            chunk_matches_filter(
                chunk,
                MetadataFilter(SourceType.SUBSIDY, date(2026, 8, 26)),
            )
        )
        self.assertFalse(
            chunk_matches_filter(
                chunk,
                MetadataFilter(
                    SourceType.SUBSIDY,
                    date(2026, 8, 26),
                    region_names=("서울특별시",),
                ),
            )
        )
        self.assertIn("지역: 미확정", chunk.text)

    def test_region_filter_requires_canonical_names_and_subsidy_source(self) -> None:
        policy = MetadataFilter(
            SourceType.SUBSIDY,
            date(2026, 8, 26),
            region_names=("서울특별시", "서울특별시 강남구"),
        )
        self.assertEqual(
            policy.to_portable_dict()["region_names_any"],
            ["서울특별시", "서울특별시 강남구"],
        )
        for invalid_name in (
            "ALL",
            "1100000000",
            "중구",
            "강원도",
            "광주광역시",
            "전라남도",
        ):
            with self.subTest(invalid_name=invalid_name):
                with self.assertRaisesRegex(ValueError, "region names"):
                    MetadataFilter(
                        SourceType.SUBSIDY,
                        date(2026, 8, 26),
                        region_names=(invalid_name,),
                    )
        with self.assertRaisesRegex(ValueError, "only to subsidy"):
            MetadataFilter(
                SourceType.LAW,
                date(2026, 8, 26),
                region_names=("서울특별시",),
            )

    def test_cross_index_raw_scores_are_forbidden(self) -> None:
        self.assertEqual(route_indexes(QueryScope.BOTH), ("subsidy", "law"))
        with self.assertRaisesRegex(ValueError, "not comparable"):
            validate_cross_index_merge("raw_score")

    def test_abstains_on_no_evidence_conflict_and_stale(self) -> None:
        cases = (
            (EvidenceState(()), "no_evidence"),
            (
                EvidenceState(("c1",), conflict_detected=True),
                "conflict",
            ),
            (
                EvidenceState(
                    ("c1",), freshness_required=True, freshness_verified=False
                ),
                "stale",
            ),
        )
        for state, expected in cases:
            with self.subTest(expected=expected):
                decision = decide_abstention(state)
                self.assertTrue(decision.abstain)
                self.assertEqual(decision.reason.value, expected)

    def test_score_is_not_an_abstention_input(self) -> None:
        decision = decide_abstention(
            EvidenceState(
                ("c1",),
                required_aspects=frozenset({"대상", "신청"}),
                supported_aspects=frozenset({"대상"}),
            )
        )
        self.assertEqual(decision.reason.value, "no_evidence")
        self.assertEqual(decision.missing_aspects, ("신청",))


if __name__ == "__main__":
    unittest.main()
