from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest
from urllib.parse import quote

from rag_design.chunking import chunk_document
from rag_design.citation import build_citation, sanitize_public_url
from rag_design.contracts import (
    AbstentionReason,
    AnswerResult,
    Chunk,
    ClaimCheck,
    Citation,
    Document,
    EvidenceCheckResult,
    EvidenceStatus,
    RegionScope,
    RetrievedChunk,
    compute_document_id,
    render_legal_metadata_summary,
    validate_region_metadata,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load_documents() -> list[Document]:
    return [
        Document.from_dict(json.loads(line))
        for line in (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ContractAndCitationTests(unittest.TestCase):
    def setUp(self) -> None:
        documents = load_documents()
        self.subsidy = documents[0]
        self.law, self.admrul, self.ordin = documents[1:]
        self.legal_documents = (self.law, self.admrul, self.ordin)

    def test_document_round_trip(self) -> None:
        for document in (self.subsidy, *self.legal_documents):
            with self.subTest(doc_id=document.doc_id):
                self.assertEqual(Document.from_dict(document.to_dict()), document)

    def test_legal_document_ids_include_subtype_and_source_sequence(self) -> None:
        for document in self.legal_documents:
            with self.subTest(law_type=document.metadata["law_type"]):
                self.assertNotEqual(
                    document.source_id, document.metadata["source_sequence"]
                )
                self.assertEqual(
                    compute_document_id(
                        source_type=document.source_type,
                        source_id=document.source_id,
                        source_updated_at=document.source_updated_at,
                        effective_from=document.effective_from,
                        content_hash=document.content_hash,
                        law_type=document.metadata["law_type"],
                        source_sequence=document.metadata["source_sequence"],
                    ),
                    document.doc_id,
                )

    def test_legal_document_ids_require_canonical_effective_date(self) -> None:
        arguments = {
            "source_type": self.law.source_type,
            "source_id": self.law.source_id,
            "source_updated_at": self.law.source_updated_at,
            "content_hash": self.law.content_hash,
            "law_type": self.law.metadata["law_type"],
            "source_sequence": self.law.metadata["source_sequence"],
        }
        for effective_from in (None, "20251001", "2025-W40-3"):
            with self.subTest(effective_from=effective_from):
                with self.assertRaises(ValueError):
                    compute_document_id(
                        **arguments,
                        effective_from=effective_from,
                    )

    def test_legal_metadata_summary_is_an_exact_canonical_projection(self) -> None:
        for document in self.legal_documents:
            with self.subTest(law_type=document.metadata["law_type"]):
                self.assertEqual(
                    render_legal_metadata_summary(document.metadata),
                    document.content,
                )

    def test_chunk_and_retrieved_chunk_round_trip(self) -> None:
        chunk = chunk_document(self.subsidy)[0]
        self.assertEqual(Chunk.from_dict(chunk.to_dict()), chunk)
        retrieved = RetrievedChunk(
            query_id="q1",
            chunk=chunk,
            rank=1,
            score=0.8,
            score_type="cosine_similarity",
            retriever_version="dense-v1",
            index_name="subsidy",
        )
        self.assertEqual(RetrievedChunk.from_dict(retrieved.to_dict()), retrieved)

    def test_evidence_check_result_round_trip_and_evidence_link(self) -> None:
        result = EvidenceCheckResult(
            query_id="q1",
            status=EvidenceStatus.SUPPORTED,
            claim_checks=(
                ClaimCheck(
                    claim_id="claim-1",
                    status=EvidenceStatus.SUPPORTED,
                    evidence_chunk_ids=("law-chunk-1",),
                    reasons=("공개 법령 목록 메타데이터에서 법령명을 확인함",),
                ),
            ),
            evidence_chunk_ids=("law-chunk-1",),
            checker_version="rule-v1",
        )
        self.assertEqual(EvidenceCheckResult.from_dict(result.to_dict()), result)
        with self.assertRaisesRegex(ValueError, "declared evidence"):
            replace(result, evidence_chunk_ids=("other",))

    def test_supported_evidence_check_requires_supported_claims_and_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one claim"):
            EvidenceCheckResult(
                query_id="q1",
                status=EvidenceStatus.SUPPORTED,
                claim_checks=(),
                evidence_chunk_ids=(),
                checker_version="rule-v1",
            )

    def test_conflict_claim_requires_evidence_and_overall_conflict(self) -> None:
        with self.assertRaisesRegex(ValueError, "require evidence"):
            ClaimCheck(
                claim_id="claim-1",
                status=EvidenceStatus.CONFLICT,
                evidence_chunk_ids=(),
                reasons=("출처가 충돌함",),
            )
        conflict = ClaimCheck(
            claim_id="claim-1",
            status=EvidenceStatus.CONFLICT,
            evidence_chunk_ids=("law-chunk-1",),
            reasons=("두 법령 버전이 충돌함",),
        )
        supported = ClaimCheck(
            claim_id="claim-2",
            status=EvidenceStatus.SUPPORTED,
            evidence_chunk_ids=("law-chunk-2",),
            reasons=("법령 목록 메타데이터에서 공포일을 확인함",),
        )
        with self.assertRaisesRegex(ValueError, "overall conflict"):
            EvidenceCheckResult(
                query_id="q1",
                status=EvidenceStatus.PARTIAL,
                claim_checks=(supported, conflict),
                evidence_chunk_ids=("law-chunk-1", "law-chunk-2"),
                checker_version="rule-v1",
            )
        unsupported = ClaimCheck(
            claim_id="claim-1",
            status=EvidenceStatus.UNSUPPORTED,
            evidence_chunk_ids=(),
            reasons=("근거에서 확인되지 않음",),
        )
        with self.assertRaisesRegex(ValueError, "only supported claims"):
            EvidenceCheckResult(
                query_id="q1",
                status=EvidenceStatus.SUPPORTED,
                claim_checks=(unsupported,),
                evidence_chunk_ids=("law-chunk-1",),
                checker_version="rule-v1",
            )

    def test_blank_required_string_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "title"):
            replace(self.subsidy, title="  ")

    def test_region_metadata_scope_and_names_are_coupled(self) -> None:
        valid_cases = (
            (RegionScope.NATIONAL.value, ["전국"]),
            (RegionScope.REGIONAL.value, ["서울특별시"]),
            (
                RegionScope.REGIONAL.value,
                ["경기도", "경기도 수원시 장안구"],
            ),
            (
                RegionScope.REGIONAL.value,
                ["전남광주통합특별시", "전남광주통합특별시 여수시"],
            ),
            (RegionScope.UNKNOWN.value, []),
        )
        for region_scope, region_names in valid_cases:
            with self.subTest(region_scope=region_scope, region_names=region_names):
                validate_region_metadata(region_scope, region_names)

        invalid_cases = (
            (RegionScope.NATIONAL.value, []),
            (RegionScope.NATIONAL.value, ["서울특별시"]),
            (RegionScope.REGIONAL.value, []),
            (RegionScope.REGIONAL.value, ["전국"]),
            (RegionScope.REGIONAL.value, ["서울특별시 강남구"]),
            (RegionScope.REGIONAL.value, ["광주광역시"]),
            (RegionScope.REGIONAL.value, ["전라남도"]),
            (
                RegionScope.REGIONAL.value,
                ["서울특별시", "서울특별시", "서울특별시 강남구"],
            ),
            (RegionScope.UNKNOWN.value, ["전국"]),
            ("other", []),
            (RegionScope.UNKNOWN.value, ()),
        )
        for region_scope, region_names in invalid_cases:
            with self.subTest(region_scope=region_scope, region_names=region_names):
                with self.assertRaises(ValueError):
                    validate_region_metadata(region_scope, region_names)

    def test_effective_interval_must_be_forward(self) -> None:
        with self.assertRaisesRegex(ValueError, "effective_to"):
            replace(
                self.law,
                effective_from="2026-01-02",
                effective_to="2026-01-01",
            )

    def test_legal_citations_use_direct_public_subtype_urls(self) -> None:
        expected_urls = {
            "law": (
                "https://www.law.go.kr/LSW/lsInfoP.do?"
                "lsiSeq=276653&efYd=20251001"
            ),
            "admrul": (
                "https://www.law.go.kr/LSW/admRulInfoP.do?"
                "admRulSeq=2100000269822"
            ),
            "ordin": (
                "https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=2124625"
            ),
        }
        for document in self.legal_documents:
            with self.subTest(law_type=document.metadata["law_type"]):
                chunk = chunk_document(document)[0]
                citation = build_citation(chunk, document)
                self.assertEqual(
                    citation.source_url,
                    expected_urls[document.metadata["law_type"]],
                )
                self.assertEqual(citation.locator, "기본정보")
                self.assertNotIn("OC=", citation.source_url.upper())

    def test_legal_citation_rejects_article_locator(self) -> None:
        chunk = chunk_document(self.law)[0]
        article_chunk = replace(
            chunk,
            heading_path=("제1조(목적)",),
            citation_locator="제1조(목적)",
        )

        with self.assertRaises(ValueError):
            build_citation(article_chunk, self.law)

    def test_service_key_case_and_encoding_variants_are_removed(self) -> None:
        variants = ("SeRvIcEkEy", "service%4Bey", "service%254Bey")
        for name in variants:
            with self.subTest(name=name):
                url = f"https://www.data.go.kr/data/15113968/openapi.do?{name}=secret&page=1"
                self.assertEqual(
                    sanitize_public_url(url),
                    "https://www.data.go.kr/data/15113968/openapi.do",
                )

    def test_openlaw_oc_key_is_removed(self) -> None:
        url = "https://open.law.go.kr/LSO/openApi/guideResult.do?OC=secret&target=law"
        self.assertEqual(
            sanitize_public_url(url),
            "https://open.law.go.kr/LSO/openApi/guideResult.do",
        )

    def test_legal_document_rejects_authenticated_source_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "sanitized HTTPS official URL"):
            replace(
                self.law,
                source_url=(
                    f"{self.law.source_url}&OC=DO_NOT_STORE_THIS_VALUE"
                ),
            )

    def test_actual_secret_is_rejected_even_when_encoded_under_unknown_name(self) -> None:
        secret = "REAL-secret/123"
        url = f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/x?ref={quote(secret)}"
        with self.assertRaisesRegex(ValueError, "configured secret"):
            sanitize_public_url(url, secret_values=(secret,))

    def test_deeply_encoded_secret_is_rejected(self) -> None:
        secret = "REAL-secret/123"
        encoded = secret
        for _ in range(10):
            encoded = quote(encoded)
        url = f"https://www.law.go.kr/lsInfoP.do?lsiSeq={encoded}"
        with self.assertRaisesRegex(ValueError, "configured secret"):
            sanitize_public_url(url, secret_values=(secret,))

    def test_untrusted_citation_domain_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "official domain"):
            Citation("c1", "문서", "위치", "https://example.com/source")

    def test_answer_requires_citation_to_reference_evidence(self) -> None:
        citation = build_citation(chunk_document(self.subsidy)[0], self.subsidy)
        with self.assertRaisesRegex(ValueError, "evidence chunk"):
            AnswerResult(
                query_id="q1",
                answer="답변",
                abstained=False,
                abstention_reason=None,
                citations=(citation,),
                evidence_chunk_ids=("other",),
                latency_ms=1,
                index_versions=("subsidy-v1",),
                pipeline_version="p1",
            )

    def test_answer_round_trip(self) -> None:
        chunk = chunk_document(self.subsidy)[0]
        citation = build_citation(chunk, self.subsidy)
        result = AnswerResult(
            query_id="q1",
            answer="근거가 있는 테스트 답변입니다.",
            abstained=False,
            abstention_reason=None,
            citations=(citation,),
            evidence_chunk_ids=(chunk.chunk_id,),
            latency_ms=10,
            index_versions=("subsidy-v1",),
            pipeline_version="p1",
        )
        self.assertEqual(AnswerResult.from_dict(result.to_dict()), result)

    def test_answer_rejects_string_boolean(self) -> None:
        chunk = chunk_document(self.subsidy)[0]
        citation = build_citation(chunk, self.subsidy)
        value = AnswerResult(
            query_id="q1",
            answer="근거가 있는 테스트 답변입니다.",
            abstained=False,
            abstention_reason=None,
            citations=(citation,),
            evidence_chunk_ids=(chunk.chunk_id,),
            latency_ms=10,
            index_versions=("subsidy-v1",),
            pipeline_version="p1",
        ).to_dict()
        value["abstained"] = "false"
        with self.assertRaisesRegex(ValueError, "JSON boolean"):
            AnswerResult.from_dict(value)

    def test_error_is_not_counted_as_abstention_contract(self) -> None:
        result = AnswerResult(
            query_id="q1",
            answer="일시적인 오류입니다.",
            abstained=False,
            abstention_reason=None,
            citations=(),
            evidence_chunk_ids=(),
            latency_ms=1,
            index_versions=(),
            pipeline_version="p1",
            error_code="RETRIEVER_UNAVAILABLE",
        )
        self.assertFalse(result.abstained)
        with self.assertRaisesRegex(ValueError, "distinct"):
            replace(
                result,
                abstained=True,
                abstention_reason=AbstentionReason.NO_EVIDENCE,
            )


if __name__ == "__main__":
    unittest.main()
