from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from rag_design.chunking import chunk_document
from rag_design.citation import build_citation
from rag_design.contracts import (
    AnswerResult,
    ClaimCheck,
    EvidenceCheckResult,
    EvidenceStatus,
    RetrievedChunk,
    compute_content_hash,
    compute_document_id,
)
from rag_design.evaluation import (
    AbstentionCase,
    CitationCase,
    RetrievalCase,
    abstention_metrics,
    citation_metrics,
    operational_metrics,
    retrieval_metrics,
)
from rag_design.validation import (
    HandoffReport,
    ValidationIssue,
    validate_answer_evidence,
    validate_chunk_batch,
    validate_collection_handoff,
    validate_evidence_check_result,
)

from tests.test_contracts_and_citations import FIXTURES, load_documents


def load_handoff(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ValidationAndEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        documents = load_documents()
        self.subsidy = documents[0]
        self.law, self.admrul, self.ordin = documents[1:]
        self.legal_documents = (self.law, self.admrul, self.ordin)

    def _validate_single_legal(self, document):
        handoff = load_handoff("law_handoff.json")
        handoff["manifest"]["document_count"] = 1
        handoff["document_card"]["document_count"] = 1
        return validate_collection_handoff(
            [document], handoff["manifest"], handoff["document_card"]
        )

    def test_handoff_report_two_argument_construction_defaults_warnings(self) -> None:
        report = HandoffReport((), (self.subsidy.doc_id,))

        self.assertTrue(report.accepted)
        self.assertEqual(report.warnings, ())

    def test_warning_only_handoff_require_accepted_succeeds(self) -> None:
        warning = ValidationIssue("review", "documents[0]", "review candidate")
        report = HandoffReport((), (self.subsidy.doc_id,), (warning,))

        report.require_accepted()

    def test_public_fixtures_pass_handoff_validation(self) -> None:
        for documents, filename in (
            ((self.subsidy,), "subsidy_handoff.json"),
            (self.legal_documents, "law_handoff.json"),
        ):
            with self.subTest(filename=filename):
                handoff = load_handoff(filename)
                report = validate_collection_handoff(
                    documents, handoff["manifest"], handoff["document_card"]
                )
                self.assertTrue(report.accepted, report.issues)

    def test_legal_document_card_declares_metadata_only_support_boundary(self) -> None:
        card = load_handoff("law_handoff.json")["document_card"]

        unsupported = " ".join(card["unsupported_scope"])
        self.assertIn("조문 본문 검색·인용", unsupported)
        self.assertIn("법률 해석", unsupported)
        self.assertIn("국가법령정보센터 공식 상세 페이지", card["official_source_guidance"])
        self.assertEqual(
            card["version_identity"],
            "law_type + source_id + source_sequence + effective_from",
        )
        self.assertIn("source_sequence", card["update_policy"])

    def test_hash_mismatch_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        document = replace(self.subsidy, content_hash="0" * 64)
        report = validate_collection_handoff(
            [document], handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("hash_mismatch", {issue.code for issue in report.issues})

    def test_content_hash_normalizes_newlines_and_unicode(self) -> None:
        self.assertEqual(
            compute_content_hash("가\r\n나"),
            compute_content_hash("\u1100\u1161\n나"),
        )

    def test_section_content_must_be_traceable_to_document_body(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        altered_section = replace(
            self.subsidy.sections[0],
            content="문서 본문과 무관한 색인 내용입니다.",
        )
        document = replace(
            self.subsidy,
            sections=(altered_section, *self.subsidy.sections[1:]),
        )
        report = validate_collection_handoff(
            [document], handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("section_content_mismatch", {issue.code for issue in report.issues})

    def test_non_deterministic_document_id_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        document = replace(self.subsidy, doc_id="random-run-id")
        report = validate_collection_handoff(
            [document], handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("non_deterministic_doc_id", {issue.code for issue in report.issues})

    def test_duplicate_source_version_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        handoff["manifest"]["document_count"] = 2
        handoff["document_card"]["document_count"] = 2
        second_content = self.subsidy.content + "\n추가 공개 문장"
        duplicate_version = replace(
            self.subsidy,
            doc_id=self.subsidy.doc_id + ":copy",
            content=second_content,
            content_hash=compute_content_hash(second_content),
        )
        report = validate_collection_handoff(
            [self.subsidy, duplicate_version],
            handoff["manifest"],
            handoff["document_card"],
        )
        self.assertIn("duplicate_source_version", {issue.code for issue in report.issues})

    def test_duplicate_content_across_source_ids_is_reported_as_warning(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        handoff["manifest"]["document_count"] = 2
        handoff["document_card"]["document_count"] = 2
        source_id = "another-public-service"
        duplicate_content = replace(
            self.subsidy,
            doc_id=compute_document_id(
                source_type=self.subsidy.source_type,
                source_id=source_id,
                source_updated_at=self.subsidy.source_updated_at,
                effective_from=self.subsidy.effective_from,
                content_hash=self.subsidy.content_hash,
            ),
            source_id=source_id,
        )

        report = validate_collection_handoff(
            [self.subsidy, duplicate_content],
            handoff["manifest"],
            handoff["document_card"],
        )

        self.assertTrue(report.accepted, report.issues)
        self.assertEqual(
            {warning.code for warning in report.warnings},
            {"duplicate_content_candidate"},
        )
        self.assertEqual(len(report.accepted_document_ids), 2)

    def test_legal_duplicate_identity_is_scoped_by_subtype(self) -> None:
        handoff = load_handoff("law_handoff.json")
        handoff["manifest"]["document_count"] = 2
        handoff["document_card"]["document_count"] = 2
        source_sequence = "2100000999999"
        metadata = {
            **self.law.metadata,
            "law_type": "admrul",
            "source_sequence": source_sequence,
        }
        same_numeric_id_in_another_subtype = replace(
            self.law,
            doc_id=compute_document_id(
                source_type=self.law.source_type,
                source_id=self.law.source_id,
                source_updated_at=self.law.source_updated_at,
                effective_from=self.law.effective_from,
                content_hash=self.law.content_hash,
                law_type=metadata["law_type"],
                source_sequence=source_sequence,
            ),
            source_url=(
                "https://www.law.go.kr/LSW/admRulInfoP.do?"
                f"admRulSeq={source_sequence}"
            ),
            metadata=metadata,
        )

        report = validate_collection_handoff(
            [self.law, same_numeric_id_in_another_subtype],
            handoff["manifest"],
            handoff["document_card"],
        )

        self.assertTrue(report.accepted, report.issues)
        self.assertEqual(
            {warning.code for warning in report.warnings},
            {"duplicate_content_candidate"},
        )

    def test_duplicate_content_within_source_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        handoff["manifest"]["document_count"] = 2
        handoff["document_card"]["document_count"] = 2
        source_updated_at = "2026-02-01"
        duplicate_content = replace(
            self.subsidy,
            doc_id=compute_document_id(
                source_type=self.subsidy.source_type,
                source_id=self.subsidy.source_id,
                source_updated_at=source_updated_at,
                effective_from=self.subsidy.effective_from,
                content_hash=self.subsidy.content_hash,
            ),
            source_updated_at=source_updated_at,
        )

        report = validate_collection_handoff(
            [self.subsidy, duplicate_content],
            handoff["manifest"],
            handoff["document_card"],
        )

        self.assertFalse(report.accepted)
        self.assertIn("duplicate_content", {issue.code for issue in report.issues})
        self.assertEqual(report.accepted_document_ids, ())

    def test_missing_application_method_is_reported_as_warning(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        without_application_method = replace(
            self.subsidy,
            sections=tuple(
                section
                for section in self.subsidy.sections
                if section.metadata.get("section_type") != "application_method"
            ),
        )

        report = validate_collection_handoff(
            [without_application_method],
            handoff["manifest"],
            handoff["document_card"],
        )

        self.assertTrue(report.accepted, report.issues)
        self.assertEqual(
            {warning.code for warning in report.warnings},
            {"missing_recommended_subsidy_section"},
        )
        chunks = chunk_document(without_application_method)
        self.assertEqual(
            validate_chunk_batch(chunks, [without_application_method]),
            (),
        )

    def test_fatal_issue_and_warning_rejects_the_handoff(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        fatal_without_application_method = replace(
            self.subsidy,
            sections=tuple(
                section
                for section in self.subsidy.sections
                if section.metadata.get("section_type") != "application_method"
            ),
            parse_warnings=("fatal: required field could not be parsed",),
        )

        report = validate_collection_handoff(
            [fatal_without_application_method],
            handoff["manifest"],
            handoff["document_card"],
        )

        self.assertFalse(report.accepted)
        self.assertEqual(report.accepted_document_ids, ())
        self.assertIn("fatal_parse_warning", {issue.code for issue in report.issues})
        self.assertIn(
            "missing_recommended_subsidy_section",
            {warning.code for warning in report.warnings},
        )
        with self.assertRaisesRegex(ValueError, "rejected by blocking issues"):
            report.require_accepted()

    def test_missing_core_subsidy_section_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        without_support_target = replace(
            self.subsidy,
            sections=tuple(
                section
                for section in self.subsidy.sections
                if section.metadata.get("section_type") != "support_target"
            ),
        )

        report = validate_collection_handoff(
            [without_support_target],
            handoff["manifest"],
            handoff["document_card"],
        )

        self.assertFalse(report.accepted)
        self.assertIn("missing_subsidy_section", {issue.code for issue in report.issues})

    def test_blank_document_card_field_is_rejected(self) -> None:
        handoff = load_handoff("law_handoff.json")
        handoff["document_card"]["update_policy"] = " "
        report = validate_collection_handoff(
            self.legal_documents, handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("missing_card_field", {issue.code for issue in report.issues})

    def test_boolean_document_count_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        handoff["manifest"]["document_count"] = True
        handoff["document_card"]["document_count"] = True
        report = validate_collection_handoff(
            [self.subsidy], handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("invalid_count", {issue.code for issue in report.issues})

    def test_document_card_boolean_fields_reject_strings(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        handoff["document_card"]["rights_reviewed"] = "false"
        report = validate_collection_handoff(
            [self.subsidy], handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("invalid_boolean", {issue.code for issue in report.issues})

    def test_manifest_collected_at_requires_timezone(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        handoff["manifest"]["collected_at"] = "2026-08-26T17:00:00"
        report = validate_collection_handoff(
            [self.subsidy], handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("invalid_collected_at", {issue.code for issue in report.issues})

    def test_source_metadata_requires_expected_scalar_types(self) -> None:
        subsidy_handoff = load_handoff("subsidy_handoff.json")
        invalid_subsidy = replace(
            self.subsidy,
            metadata={**self.subsidy.metadata, "organization": 123},
        )
        subsidy_report = validate_collection_handoff(
            [invalid_subsidy],
            subsidy_handoff["manifest"],
            subsidy_handoff["document_card"],
        )
        self.assertIn(
            "invalid_source_metadata",
            {issue.code for issue in subsidy_report.issues},
        )

        law_handoff = load_handoff("law_handoff.json")
        invalid_law = replace(
            self.law,
            metadata={**self.law.metadata, "issued_date": 20250318},
        )
        law_handoff["manifest"]["document_count"] = 1
        law_handoff["document_card"]["document_count"] = 1
        law_report = validate_collection_handoff(
            [invalid_law], law_handoff["manifest"], law_handoff["document_card"]
        )
        self.assertIn(
            "invalid_source_metadata", {issue.code for issue in law_report.issues}
        )

    def test_unknown_region_metadata_is_valid_and_chunkable(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        document = replace(
            self.subsidy,
            metadata={
                **self.subsidy.metadata,
                "region_scope": "unknown",
                "region_names": [],
            },
        )
        report = validate_collection_handoff(
            [document], handoff["manifest"], handoff["document_card"]
        )
        self.assertTrue(report.accepted, report.issues)
        chunks = chunk_document(document)
        self.assertEqual(validate_chunk_batch(chunks, (document,)), ())

    def test_inconsistent_or_ambiguous_region_metadata_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        invalid_cases = (
            ("national", []),
            ("regional", ["전국"]),
            ("regional", ["중구"]),
            ("regional", ["서울특별시 강남구"]),
            ("unknown", ["전국"]),
        )
        for region_scope, region_names in invalid_cases:
            with self.subTest(region_scope=region_scope, region_names=region_names):
                document = replace(
                    self.subsidy,
                    metadata={
                        **self.subsidy.metadata,
                        "region_scope": region_scope,
                        "region_names": region_names,
                    },
                )
                report = validate_collection_handoff(
                    [document], handoff["manifest"], handoff["document_card"]
                )
                self.assertIn(
                    "invalid_region_metadata",
                    {issue.code for issue in report.issues},
                )

    def test_missing_region_names_is_rejected(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        metadata = dict(self.subsidy.metadata)
        del metadata["region_names"]
        report = validate_collection_handoff(
            [replace(self.subsidy, metadata=metadata)],
            handoff["manifest"],
            handoff["document_card"],
        )
        self.assertIn(
            "missing_source_metadata",
            {issue.code for issue in report.issues},
        )

    def test_fatal_parse_warning_text_is_not_copied_to_report(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        marker = "PRIVATE_MARKER"
        document = replace(
            self.subsidy,
            parse_warnings=(f"fatal: {marker}",),
        )
        report = validate_collection_handoff(
            [document], handoff["manifest"], handoff["document_card"]
        )
        self.assertIn("fatal_parse_warning", {issue.code for issue in report.issues})
        self.assertNotIn(marker, " ".join(issue.message for issue in report.issues))

    def test_handoff_rejects_actual_secret_in_url(self) -> None:
        handoff = load_handoff("law_handoff.json")
        secret = "REAL_OPENLAW_SECRET"
        handoff["manifest"]["source_dataset_url"] = (
            "https://open.law.go.kr/LSO/main.do?lsiSeq=REAL_OPENLAW_SECRET"
        )
        report = validate_collection_handoff(
            self.legal_documents,
            handoff["manifest"],
            handoff["document_card"],
            secret_values=(secret,),
        )
        self.assertIn("secret_exposure", {issue.code for issue in report.issues})

    def test_handoff_rejects_known_secret_anywhere_in_document_contract(self) -> None:
        handoff = load_handoff("subsidy_handoff.json")
        secret = "KNOWN_TEST_SECRET"
        content_with_secret = f"{self.subsidy.content}\n{secret}"
        section_with_secret = replace(
            self.subsidy.sections[0],
            content=f"{self.subsidy.sections[0].content} {secret}",
        )
        cases = (
            replace(
                self.subsidy,
                content=content_with_secret,
                content_hash=compute_content_hash(content_with_secret),
            ),
            replace(
                self.subsidy,
                content=content_with_secret,
                content_hash=compute_content_hash(content_with_secret),
                sections=(section_with_secret, *self.subsidy.sections[1:]),
            ),
            replace(
                self.subsidy,
                metadata={**self.subsidy.metadata, "debug_request": secret},
            ),
        )
        for document in cases:
            with self.subTest(document=document.doc_id):
                report = validate_collection_handoff(
                    [document],
                    handoff["manifest"],
                    handoff["document_card"],
                    secret_values=(secret,),
                )
                self.assertIn(
                    "secret_exposure", {issue.code for issue in report.issues}
                )
                self.assertNotIn(
                    secret, " ".join(issue.message for issue in report.issues)
                )

    def test_handoff_rejects_known_secret_in_manifest_and_document_card(self) -> None:
        secret = "KNOWN_HANDOFF_SECRET"
        cases = []
        manifest_handoff = load_handoff("subsidy_handoff.json")
        manifest_handoff["manifest"]["metadata"] = {"debug_request": secret}
        cases.append(manifest_handoff)
        card_handoff = load_handoff("subsidy_handoff.json")
        card_handoff["document_card"]["cleaning_method"] = secret
        cases.append(card_handoff)
        for handoff in cases:
            report = validate_collection_handoff(
                [self.subsidy],
                handoff["manifest"],
                handoff["document_card"],
                secret_values=(secret,),
            )
            self.assertIn("secret_exposure", {issue.code for issue in report.issues})
            self.assertNotIn(
                secret, " ".join(issue.message for issue in report.issues)
            )

    def test_handoff_rejects_nested_auth_query_without_known_secret_value(self) -> None:
        request_url = (
            "https://open.law.go.kr/DRF/lawService.do?"
            "OC=UNSUPPLIED_CREDENTIAL&target=law"
        )
        document = replace(
            self.law,
            metadata={
                **self.law.metadata,
                "collector_context": {"request_urls": [request_url]},
            },
        )
        report = self._validate_single_legal(document)

        self.assertIn("secret_exposure", {issue.code for issue in report.issues})

    def test_legal_handoff_requires_metadata_only_content_level(self) -> None:
        for record_name in ("manifest", "document_card"):
            for invalid_value in (None, "full_text"):
                with self.subTest(record=record_name, value=invalid_value):
                    handoff = load_handoff("law_handoff.json")
                    if invalid_value is None:
                        del handoff[record_name]["content_level"]
                    else:
                        handoff[record_name]["content_level"] = invalid_value
                    report = validate_collection_handoff(
                        self.legal_documents,
                        handoff["manifest"],
                        handoff["document_card"],
                    )
                    self.assertIn(
                        "invalid_legal_content_level",
                        {issue.code for issue in report.issues},
                    )

    def test_legal_documents_require_metadata_only_content_level(self) -> None:
        for invalid_value in (None, "full_text"):
            with self.subTest(value=invalid_value):
                metadata = dict(self.law.metadata)
                if invalid_value is None:
                    del metadata["content_level"]
                else:
                    metadata["content_level"] = invalid_value
                report = self._validate_single_legal(
                    replace(self.law, metadata=metadata)
                )
                self.assertIn(
                    "invalid_legal_content_level",
                    {issue.code for issue in report.issues},
                )

    def test_legal_documents_require_normalized_metadata_fields(self) -> None:
        required_fields = (
            "content_level",
            "law_type",
            "law_name",
            "source_sequence",
            "organization",
            "document_kind",
            "issued_date",
            "effective_date",
            "revision_type",
        )
        for field_name in required_fields:
            with self.subTest(field_name=field_name):
                metadata = dict(self.law.metadata)
                del metadata[field_name]
                report = self._validate_single_legal(
                    replace(self.law, metadata=metadata)
                )
                self.assertIn(
                    "missing_source_metadata",
                    {issue.code for issue in report.issues},
                )

    def test_legal_metadata_dates_require_canonical_yyyy_mm_dd(self) -> None:
        for field_name, invalid_value in (
            ("issued_date", "20250318"),
            ("effective_date", "20251001"),
        ):
            with self.subTest(field_name=field_name):
                report = self._validate_single_legal(
                    replace(
                        self.law,
                        metadata={
                            **self.law.metadata,
                            field_name: invalid_value,
                        },
                    )
                )
                self.assertIn(
                    "invalid_source_metadata",
                    {issue.code for issue in report.issues},
                )

    def test_full_text_article_structure_is_rejected(self) -> None:
        content = "제1조(목적)\n이 조문 본문은 정책상 색인 대상이 아닙니다."
        article_section = replace(
            self.law.sections[0],
            heading_path=("제1조(목적)",),
            content=content,
            metadata={"section_type": "article"},
        )
        document = replace(
            self.law,
            content=content,
            sections=(article_section,),
            content_hash=compute_content_hash(content),
        )
        report = self._validate_single_legal(document)
        self.assertIn(
            "invalid_legal_structure", {issue.code for issue in report.issues}
        )

    def test_full_text_disguised_as_basic_info_is_rejected(self) -> None:
        content = (
            "제1조(목적) 이 법은 국민의 최저생활을 보장한다.\n"
            "제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다."
        )
        document = replace(
            self.law,
            content=content,
            sections=(replace(self.law.sections[0], content=content),),
            content_hash=compute_content_hash(content),
        )

        report = self._validate_single_legal(document)

        self.assertIn("invalid_legal_content", {issue.code for issue in report.issues})

    def test_legal_structure_requires_one_exact_basic_info_section(self) -> None:
        cases = (
            replace(
                self.law,
                sections=(
                    replace(self.law.sections[0], heading_path=("상세정보",)),
                ),
            ),
            replace(
                self.law,
                sections=(
                    replace(
                        self.law.sections[0],
                        metadata={"section_type": "article"},
                    ),
                ),
            ),
            replace(
                self.law,
                sections=(self.law.sections[0], self.law.sections[0]),
            ),
            replace(
                self.law,
                sections=(
                    replace(self.law.sections[0], content="다른 기본정보"),
                ),
            ),
        )
        for document in cases:
            with self.subTest(sections=document.sections):
                report = self._validate_single_legal(document)
                self.assertIn(
                    "invalid_legal_structure",
                    {issue.code for issue in report.issues},
                )

    def test_legal_root_url_is_not_a_citation(self) -> None:
        report = self._validate_single_legal(
            replace(self.admrul, source_url="https://www.law.go.kr/")
        )
        self.assertIn(
            "legal_source_reference_mismatch",
            {issue.code for issue in report.issues},
        )

    def test_legal_identity_and_sequence_must_match_contract(self) -> None:
        sequence_metadata = {
            **self.law.metadata,
            "source_sequence": "999999",
        }
        cases = (
            replace(self.law, source_id="999999"),
            replace(self.law, metadata=sequence_metadata),
            replace(
                self.law,
                source_url=(
                    "https://www.law.go.kr/LSW/lsInfoP.do?"
                    "lsiSeq=999999&efYd=20251001"
                ),
            ),
        )
        expected_codes = (
            "non_deterministic_doc_id",
            "non_deterministic_doc_id",
            "legal_source_reference_mismatch",
        )
        for document, expected_code in zip(cases, expected_codes):
            with self.subTest(doc_id=document.doc_id, expected=expected_code):
                report = self._validate_single_legal(document)
                self.assertIn(
                    expected_code, {issue.code for issue in report.issues}
                )

    def test_legal_source_sequence_must_be_numeric(self) -> None:
        report = self._validate_single_legal(
            replace(
                self.law,
                metadata={**self.law.metadata, "source_sequence": "not-numeric"},
            )
        )
        self.assertIn(
            "invalid_source_metadata", {issue.code for issue in report.issues}
        )

    def test_legal_name_and_effective_date_match_parent_fields(self) -> None:
        cases = (
            (
                replace(
                    self.law,
                    metadata={**self.law.metadata, "law_name": "다른 법령명"},
                ),
                "legal_title_mismatch",
            ),
            (
                replace(
                    self.law,
                    metadata={
                        **self.law.metadata,
                        "effective_date": "2025-10-02",
                    },
                ),
                "legal_effective_date_mismatch",
            ),
        )
        for document, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                report = self._validate_single_legal(document)
                self.assertIn(
                    expected_code, {issue.code for issue in report.issues}
                )

    def test_legal_nested_api_url_cannot_expose_a_secret(self) -> None:
        secret = "KNOWN_LEGAL_SECRET"
        document = replace(
            self.law,
            metadata={
                **self.law.metadata,
                "raw_detail_url": (
                    "https://open.law.go.kr/DRF/lawService.do?"
                    f"OC={secret}&target=law"
                ),
            },
        )
        handoff = load_handoff("law_handoff.json")
        handoff["manifest"]["document_count"] = 1
        handoff["document_card"]["document_count"] = 1
        report = validate_collection_handoff(
            [document],
            handoff["manifest"],
            handoff["document_card"],
            secret_values=(secret,),
        )
        self.assertIn("secret_exposure", {issue.code for issue in report.issues})

    def test_intentional_body_omission_is_not_a_parse_warning(self) -> None:
        for document in self.legal_documents:
            with self.subTest(law_type=document.metadata["law_type"]):
                self.assertEqual(document.parse_warnings, ())
                self.assertEqual(document.metadata["content_level"], "metadata_only")

        policy_warning = (
            "본문(조문) 미포함 - 목록조회 API 정보만으로 구성됨 "
            "(본문 API 미사용 정책)"
        )
        report = self._validate_single_legal(
            replace(self.law, parse_warnings=(policy_warning,))
        )
        self.assertIn(
            "invalid_legal_parse_warning",
            {issue.code for issue in report.issues},
        )

        unrelated_warning = self._validate_single_legal(
            replace(
                self.law,
                parse_warnings=("기관명 공백 표기를 정규화함",),
            )
        )
        self.assertTrue(unrelated_warning.accepted, unrelated_warning.issues)

    def test_answer_and_evidence_check_must_use_actual_retrieved_chunks(self) -> None:
        chunk = chunk_document(self.subsidy)[0]
        retrieved = RetrievedChunk(
            query_id="q1",
            chunk=chunk,
            rank=1,
            score=0.8,
            score_type="cosine_similarity",
            retriever_version="dense-v1",
            index_name="subsidy",
        )
        fabricated_id = "fabricated-chunk"
        citation = replace(build_citation(chunk, self.subsidy), chunk_id=fabricated_id)
        answer = AnswerResult(
            query_id="q1",
            answer="자체 선언 근거를 사용한 답변",
            abstained=False,
            abstention_reason=None,
            citations=(citation,),
            evidence_chunk_ids=(fabricated_id,),
            latency_ms=1,
            index_versions=("subsidy-v1",),
            pipeline_version="p1",
        )
        self.assertIn(
            "unretrieved_evidence",
            {issue.code for issue in validate_answer_evidence(answer, (retrieved,))},
        )

        check = EvidenceCheckResult(
            query_id="q1",
            status=EvidenceStatus.SUPPORTED,
            claim_checks=(
                ClaimCheck(
                    claim_id="claim-1",
                    status=EvidenceStatus.SUPPORTED,
                    evidence_chunk_ids=(fabricated_id,),
                    reasons=("테스트 판정",),
                ),
            ),
            evidence_chunk_ids=(fabricated_id,),
            checker_version="rule-v1",
        )
        self.assertIn(
            "unretrieved_evidence",
            {
                issue.code
                for issue in validate_evidence_check_result(check, (retrieved,))
            },
        )

    def test_retrieval_metrics_and_zero_denominator(self) -> None:
        metrics = retrieval_metrics(
            [
                RetrievalCase(frozenset({"a", "b"}), ("x", "a", "b")),
                RetrievalCase(frozenset({"c"}), ("c", "x")),
            ],
            k=2,
        )
        self.assertAlmostEqual(metrics.recall_at_k, 0.75)
        self.assertAlmostEqual(metrics.mrr_at_k, 0.75)
        zero = retrieval_metrics([RetrievalCase(frozenset(), ())], k=5)
        self.assertEqual(
            (zero.recall_at_k, zero.mrr_at_k, zero.evaluated_queries),
            (0.0, 0.0, 0),
        )
        outside_k = retrieval_metrics(
            [RetrievalCase(frozenset({"gold"}), ("x", "gold"))], k=1
        )
        self.assertEqual(outside_k.mrr_at_k, 0.0)

    def test_citation_metrics_and_zero_denominator(self) -> None:
        metrics = citation_metrics(
            [
                CitationCase(
                    evidence_chunk_ids=frozenset({"a"}),
                    cited_chunk_ids=("a", "x"),
                    claim_citations={"claim": ("a",)},
                    required_claim_ids=frozenset({"claim", "missing"}),
                    claim_evidence_chunk_ids={
                        "claim": frozenset({"a"}),
                        "missing": frozenset({"missing-evidence"}),
                    },
                )
            ]
        )
        self.assertEqual((metrics.precision, metrics.coverage), (0.5, 0.5))
        zero = citation_metrics([])
        self.assertEqual((zero.precision, zero.coverage), (0.0, 0.0))
        unreported = citation_metrics(
            [
                CitationCase(
                    evidence_chunk_ids=frozenset({"a"}),
                    cited_chunk_ids=(),
                    claim_citations={"claim": ("a",)},
                    required_claim_ids=frozenset({"claim"}),
                    claim_evidence_chunk_ids={"claim": frozenset({"a"})},
                )
            ]
        )
        self.assertEqual(unreported.coverage, 0.0)

    def test_citation_coverage_uses_claim_specific_gold_evidence(self) -> None:
        metrics = citation_metrics(
            [
                CitationCase(
                    evidence_chunk_ids=frozenset({"claim-a", "claim-b"}),
                    cited_chunk_ids=("claim-b",),
                    claim_citations={"claim-a": ("claim-b",)},
                    required_claim_ids=frozenset({"claim-a"}),
                    claim_evidence_chunk_ids={
                        "claim-a": frozenset({"claim-a"})
                    },
                )
            ]
        )
        self.assertEqual((metrics.precision, metrics.coverage), (0.0, 0.0))

    def test_abstention_metrics_exclude_errors_and_define_zero_denominator(self) -> None:
        metrics = abstention_metrics(
            [
                AbstentionCase(True, True),
                AbstentionCase(False, True),
                AbstentionCase(True, False),
                AbstentionCase(True, False, error=True),
            ]
        )
        self.assertEqual((metrics.precision, metrics.recall), (0.5, 0.5))
        zero = abstention_metrics([AbstentionCase(False, False)])
        self.assertEqual((zero.precision, zero.recall), (0.0, 0.0))

    def test_operational_metrics_and_zero_denominator(self) -> None:
        metrics = operational_metrics([10, 20, 30, 40], [False, False, True, False])
        self.assertEqual(metrics.p50_latency_ms, 25.0)
        self.assertEqual(metrics.p95_latency_ms, 40.0)
        self.assertEqual(metrics.error_rate, 0.25)
        zero = operational_metrics([], [])
        self.assertEqual((zero.p50_latency_ms, zero.error_rate, zero.sample_count), (0.0, 0.0, 0))


if __name__ == "__main__":
    unittest.main()
