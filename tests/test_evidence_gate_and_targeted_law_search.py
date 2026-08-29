from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from rag_chatbot.graph.nodes import evaluate_evidence, search_targeted_laws
from rag_design.chunking import (
    ChunkingConfig,
    chunk_document,
    compute_chunk_id,
    compute_chunk_id_from_document_id,
)
from rag_design.citation import legal_citation_url
from rag_design.contracts import (
    AbstentionReason,
    EvidenceStatus,
    RetrievedChunk,
    Section,
    SourceType,
    compute_content_hash,
    compute_document_id,
    render_legal_metadata_summary,
)
from rag_design.policy import (
    AbstentionDecision,
    LEGAL_ARTICLE_BODY_ASPECT,
    LEGAL_METADATA_ASPECT,
)
from rag_design.vector_store import VectorSearchFilter

from tests.test_contracts_and_citations import load_documents


class EvidenceGateAndTargetedLawSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        (
            self.subsidy_document,
            self.law_document,
            self.admrul_document,
            *_,
        ) = load_documents()
        self.subsidy_chunks = chunk_document(self.subsidy_document)
        self.law_chunk = chunk_document(self.law_document)[0]
        self.admrul_chunk = chunk_document(self.admrul_document)[0]
        self.query_id = "query-1"
        self.as_of = "2026-08-26"
        self.subsidy = self._retrieved(self.subsidy_chunks[0])
        self.law = self._retrieved(self.law_chunk)
        self.admrul = self._retrieved(self.admrul_chunk, rank=2)

    def _retrieved(
        self,
        chunk,
        *,
        query_id: str | None = None,
        rank: int = 1,
    ) -> RetrievedChunk:
        return RetrievedChunk(
            query_id=query_id or self.query_id,
            chunk=chunk,
            rank=rank,
            score=0.1,
            score_type="cosine_distance",
            retriever_version="test-v1",
            index_name=chunk.source_type.value,
        )

    def _claim(
        self,
        *,
        claim_id: str = "claim-1",
        law_required: bool = False,
        evidence_ids: list[str] | None = None,
        required_sources: list[dict[str, str]] | None = None,
    ) -> dict:
        result = {
            "claim_id": claim_id,
            "policy_id": self.subsidy_document.source_id,
            "claim_type": "eligibility",
            "doc_check_required": False,
            "law_check_required": law_required,
            "evidence_chunk_ids": evidence_ids
            if evidence_ids is not None
            else [self.subsidy.chunk.chunk_id],
            "status": EvidenceStatus.SUPPORTED.value,
            "reasons": ["실제 보조금 원문으로 확인됨"],
        }
        if law_required:
            result["required_aspects"] = [LEGAL_METADATA_ASPECT]
            result["required_law_sources"] = (
                required_sources
                if required_sources is not None
                else [self._source_ref(self.law)]
            )
        return result

    @staticmethod
    def _source_ref(item: RetrievedChunk) -> dict[str, str]:
        return {
            "law_type": item.chunk.metadata["law_type"],
            "source_id": item.chunk.metadata["source_id"],
        }

    def _state(self, claim: dict | None = None) -> dict:
        return {
            "query_id": self.query_id,
            "as_of": self.as_of,
            "safety_blocked": False,
            "claim_plan": [claim or self._claim()],
            "subsidy_chunks": [self.subsidy],
            "law_chunks": [],
        }

    def _n8_state(self, claim: dict | None = None) -> dict:
        state = self._state(claim or self._claim(law_required=True))
        state.update(evaluate_evidence(state))
        self.assertEqual(state["evidence_gate_verdict"], "insufficient_law")
        return state

    def _law_with_sequence(
        self, source_sequence: str, *, rank: int = 2
    ) -> RetrievedChunk:
        metadata = {
            **self.law.chunk.metadata,
            "source_sequence": source_sequence,
            "source_url": legal_citation_url(
                law_type=self.law.chunk.metadata["law_type"],
                source_sequence=source_sequence,
                effective_from=self.law.chunk.metadata["effective_from"],
            ),
        }
        doc_id = compute_document_id(
            source_type=SourceType.LAW,
            source_id=metadata["source_id"],
            source_updated_at=metadata["source_updated_at"],
            effective_from=metadata["effective_from"],
            content_hash=self.law.chunk.content_hash,
            law_type=metadata["law_type"],
            source_sequence=source_sequence,
        )
        chunk_id = compute_chunk_id_from_document_id(
            doc_id,
            self.law.chunk.heading_path,
            metadata["chunk_part"],
            metadata["chunking_version"],
        )
        return self._retrieved(
            replace(
                self.law.chunk,
                chunk_id=chunk_id,
                doc_id=doc_id,
                metadata=metadata,
            ),
            rank=rank,
        )

    def _multipart_law_document(self):
        metadata = {
            **self.law_document.metadata,
            "revision_type": "일부개정" * 100,
        }
        content = render_legal_metadata_summary(metadata)
        return replace(
            self.law_document,
            content=content,
            sections=(
                Section(
                    heading_path=self.law_document.sections[0].heading_path,
                    content=content,
                    metadata=self.law_document.sections[0].metadata,
                ),
            ),
            content_hash=compute_content_hash(content),
            metadata=metadata,
        )

    def _multipart_law(self) -> tuple[RetrievedChunk, ...]:
        document = self._multipart_law_document()
        chunks = chunk_document(
            document, ChunkingConfig(max_chars=120, overlap_chars=10)
        )
        self.assertGreater(len(chunks), 2)
        return tuple(
            self._retrieved(chunk, rank=index + 1)
            for index, chunk in enumerate(chunks)
        )

    def assertFailReason(self, result: dict, reason: AbstentionReason) -> None:
        self.assertEqual(result["evidence_gate_verdict"], "fail")
        self.assertTrue(result["abstention_decision"].abstain)
        self.assertIs(result["abstention_decision"].reason, reason)

    def test_t0_claim_plan_must_be_a_nonempty_list(self) -> None:
        for value in (None, {}, []):
            with self.subTest(value=value):
                state = self._state()
                if value is None:
                    del state["claim_plan"]
                else:
                    state["claim_plan"] = value
                with self.assertRaises(ValueError):
                    evaluate_evidence(state)

        for claim_type in (None, "other", 1):
            with self.subTest(claim_type=claim_type):
                claim = self._claim()
                if claim_type is None:
                    del claim["claim_type"]
                else:
                    claim["claim_type"] = claim_type
                with self.assertRaises(ValueError):
                    evaluate_evidence(self._state(claim))

        for claim_type in ("eligibility", "amount", "duplicate"):
            with self.subTest(allowed_claim_type=claim_type):
                claim = self._claim()
                claim["claim_type"] = claim_type
                self.assertEqual(
                    evaluate_evidence(self._state(claim))["evidence_gate_verdict"],
                    "pass",
                )

    def test_t1_e9_supported_subsidy_evidence_passes_without_dates(self) -> None:
        state = self._state()
        before = deepcopy(state)

        result = evaluate_evidence(state)

        self.assertEqual(result["evidence_gate_verdict"], "pass")
        self.assertFalse(result["abstention_decision"].abstain)
        self.assertEqual(result["missing_document_claim_ids"], [])
        self.assertEqual(result["missing_law_claim_ids"], [])
        self.assertEqual(result["doc_retry_count"], 0)
        self.assertEqual(result["law_retry_count"], 0)
        self.assertEqual(state, before)

    def test_t2_missing_n5_evidence_fields_retry_once_then_fail(self) -> None:
        variants = ("status", "reasons", "evidence_chunk_ids")
        for field_name in variants:
            with self.subTest(field_name=field_name):
                claim = self._claim()
                claim.pop(field_name)
                state = self._state(claim)

                first = evaluate_evidence(state)
                self.assertEqual(
                    first["evidence_gate_verdict"], "insufficient_document"
                )
                self.assertEqual(first["missing_document_claim_ids"], ["claim-1"])
                self.assertEqual(first["doc_retry_count"], 1)

                state.update(first)
                second = evaluate_evidence(state)
                self.assertFailReason(second, AbstentionReason.NO_EVIDENCE)
                self.assertEqual(second["doc_retry_count"], 1)

    def test_t3_missing_legal_metadata_targets_only_law_claim(self) -> None:
        law_claim = self._claim(law_required=True)
        ordinary_claim = self._claim(claim_id="claim-2")
        state = self._state()
        state["claim_plan"] = [ordinary_claim, law_claim]

        result = evaluate_evidence(state)

        self.assertEqual(result["evidence_gate_verdict"], "insufficient_law")
        self.assertEqual(result["missing_document_claim_ids"], [])
        self.assertEqual(result["missing_law_claim_ids"], ["claim-1"])
        self.assertEqual(result["law_retry_count"], 1)

    def test_t4_n8_metadata_supplement_then_n7_passes(self) -> None:
        state = self._state(self._claim(law_required=True))
        first = evaluate_evidence(state)
        state.update(first)
        calls: list[tuple] = []

        def search(*args, **kwargs):
            calls.append((args, kwargs))
            return (self.law,)

        update = search_targeted_laws(state, search=search)
        state.update(update)
        final = evaluate_evidence(state)

        self.assertEqual(len(calls), 1)
        self.assertEqual(final["evidence_gate_verdict"], "pass")
        self.assertFalse(final["abstention_decision"].abstain)
        self.assertEqual(state["claim_plan"][0]["status"], "supported")
        self.assertIn(
            self.subsidy.chunk.chunk_id,
            state["claim_plan"][0]["evidence_chunk_ids"],
        )
        self.assertIn(
            self.law.chunk.chunk_id,
            state["claim_plan"][0]["evidence_chunk_ids"],
        )

    def test_t4_law_only_evidence_cannot_pass_substantive_claim(self) -> None:
        claim = self._claim(
            law_required=True, evidence_ids=[self.law.chunk.chunk_id]
        )
        state = self._state(claim)
        state["law_chunks"] = [self.law]

        result = evaluate_evidence(state)

        self.assertEqual(
            result["evidence_gate_verdict"], "insufficient_document"
        )
        self.assertEqual(result["missing_document_claim_ids"], ["claim-1"])

    def test_t5_empty_law_search_exhausts_retry_without_loop(self) -> None:
        state = self._state(self._claim(law_required=True))
        state.update(evaluate_evidence(state))
        update = search_targeted_laws(state, search=lambda *args, **kwargs: ())
        state.update(update)

        result = evaluate_evidence(state)

        self.assertFailReason(result, AbstentionReason.NO_EVIDENCE)
        self.assertEqual(result["law_retry_count"], 1)
        self.assertEqual(result["missing_law_claim_ids"], ["claim-1"])

    def test_t5_later_empty_pair_discards_earlier_search_results(self) -> None:
        sources = [self._source_ref(self.law), self._source_ref(self.admrul)]
        state = self._n8_state(
            self._claim(law_required=True, required_sources=sources)
        )
        before = deepcopy(state)

        def search(source, query, **kwargs):
            pair = dict(kwargs["search_filter"].metadata_equals)
            return (self.law,) if pair == sources[0] else ()

        update = search_targeted_laws(state, search=search)

        self.assertEqual(update["claim_plan"], before["claim_plan"])
        self.assertEqual(update["law_chunks"], before["law_chunks"])
        self.assertEqual(state, before)

    def test_t5_later_empty_pair_does_not_hide_prior_combined_conflict(self) -> None:
        sources = [self._source_ref(self.law), self._source_ref(self.admrul)]
        state = self._n8_state(
            self._claim(law_required=True, required_sources=sources)
        )
        state["law_chunks"] = [self.law]
        conflicting = replace(
            self.law,
            chunk=replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "source_name": "서로 다른 법령 출처",
                },
            ),
        )
        before = deepcopy(state)

        def search(source, query, **kwargs):
            pair = dict(kwargs["search_filter"].metadata_equals)
            return (conflicting,) if pair == sources[0] else ()

        with self.assertRaisesRegex(ValueError, "different evidence payloads"):
            search_targeted_laws(state, search=search)

        self.assertEqual(state, before)

    def test_t6_safety_missing_nonboolean_and_true_fail_closed(self) -> None:
        for value in (None, 0, "false", True):
            with self.subTest(value=value):
                state = self._state()
                if value is None:
                    del state["safety_blocked"]
                else:
                    state["safety_blocked"] = value
                result = evaluate_evidence(state)
                self.assertFailReason(result, AbstentionReason.SAFETY)
                self.assertEqual(result["missing_document_claim_ids"], [])
                self.assertEqual(result["missing_law_claim_ids"], [])

    def test_t7_conflict_fails_without_retry(self) -> None:
        claim = self._claim()
        claim["status"] = EvidenceStatus.CONFLICT.value
        result = evaluate_evidence(self._state(claim))

        self.assertFailReason(result, AbstentionReason.CONFLICT)
        self.assertEqual(result["doc_retry_count"], 0)
        self.assertEqual(result["law_retry_count"], 0)

        second_sequence = self._law_with_sequence("276654")
        claim = self._claim(
            law_required=True,
            evidence_ids=[
                self.subsidy.chunk.chunk_id,
                self.law.chunk.chunk_id,
                second_sequence.chunk.chunk_id,
            ],
        )
        state = self._state(claim)
        state["law_chunks"] = [self.law, second_sequence]
        result = evaluate_evidence(state)
        self.assertFailReason(result, AbstentionReason.CONFLICT)
        self.assertEqual(result["missing_law_claim_ids"], [])

    def test_t8_as_of_and_half_open_intervals_fail_stale(self) -> None:
        for value in (None, "20260826", "2026-02-30"):
            with self.subTest(as_of=value):
                state = self._state()
                if value is None:
                    del state["as_of"]
                else:
                    state["as_of"] = value
                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.STALE
                )

        bounded = replace(
            self.subsidy.chunk,
            metadata={
                **self.subsidy.chunk.metadata,
                "effective_from": "2026-01-01",
                "effective_to": self.as_of,
            },
        )
        state = self._state()
        state["subsidy_chunks"] = [self._retrieved(bounded)]
        state["claim_plan"][0]["evidence_chunk_ids"] = [bounded.chunk_id]
        self.assertFailReason(evaluate_evidence(state), AbstentionReason.STALE)

        malformed = replace(
            self.subsidy.chunk,
            metadata={
                **self.subsidy.chunk.metadata,
                "effective_from": "not-a-date",
            },
        )
        state = self._state()
        state["subsidy_chunks"] = [self._retrieved(malformed)]
        state["claim_plan"][0]["evidence_chunk_ids"] = [malformed.chunk_id]
        self.assertFailReason(evaluate_evidence(state), AbstentionReason.STALE)

        undated_law = replace(
            self.law.chunk,
            metadata={**self.law.chunk.metadata, "effective_from": None},
        )
        claim = self._claim(
            law_required=True,
            evidence_ids=[self.subsidy.chunk.chunk_id, undated_law.chunk_id],
        )
        state = self._state(claim)
        state["law_chunks"] = [self._retrieved(undated_law)]
        self.assertFailReason(evaluate_evidence(state), AbstentionReason.STALE)

    def test_t9_retry_counter_contract(self) -> None:
        claim = self._claim(evidence_ids=[])
        state = self._state(claim)
        self.assertEqual(evaluate_evidence(state)["doc_retry_count"], 1)

        for value in (False, -1, 1.0, "1", 2):
            with self.subTest(value=value):
                state = self._state()
                state["doc_retry_count"] = value
                with self.assertRaises(ValueError):
                    evaluate_evidence(state)

    def test_t10_duplicate_claim_and_evidence_ids_are_rejected(self) -> None:
        duplicate_claims = self._state()
        duplicate_claims["claim_plan"] = [self._claim(), self._claim()]
        with self.assertRaises(ValueError):
            evaluate_evidence(duplicate_claims)

        claim = self._claim(
            evidence_ids=[
                self.subsidy.chunk.chunk_id,
                self.subsidy.chunk.chunk_id,
            ]
        )
        state = self._state(claim)
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            evaluate_evidence(state)
        self.assertEqual(state, before)

    def test_t10_law_source_refs_are_exact_ordered_ascii_pairs(self) -> None:
        valid_ref = self._source_ref(self.law)
        invalid_source_lists = (
            [valid_ref, dict(valid_ref)],
            [{"law_type": "wrong", "source_id": "006478"}],
            [{"law_type": "law", "source_id": ""}],
            [{"law_type": "law", "source_id": "12A"}],
            [{"law_type": "law", "source_id": "１２"}],
            [{"law_type": "law"}],
            [{"law_type": "law", "source_id": "006478", "extra": "x"}],
        )
        for sources in invalid_source_lists:
            with self.subTest(sources=sources):
                claim = self._claim(law_required=True, required_sources=sources)
                before = deepcopy(claim)
                with self.assertRaises(ValueError):
                    evaluate_evidence(self._state(claim))
                self.assertEqual(claim, before)

        state = self._n8_state()
        calls = []
        search_targeted_laws(
            state,
            search=lambda *args, **kwargs: calls.append(kwargs) or (),
        )
        self.assertEqual(
            calls[0]["search_filter"].metadata_equals["source_id"], "006478"
        )

    def test_t10a_missing_or_contradictory_law_sources_fail_closed(self) -> None:
        for sources in (None, []):
            with self.subTest(sources=sources):
                claim = self._claim(law_required=True)
                if sources is None:
                    del claim["required_law_sources"]
                else:
                    claim["required_law_sources"] = sources
                result = evaluate_evidence(self._state(claim))
                self.assertFailReason(result, AbstentionReason.NO_EVIDENCE)
                self.assertEqual(result["missing_law_claim_ids"], [])

        claim = self._claim()
        claim["required_law_sources"] = [self._source_ref(self.law)]
        with self.assertRaises(ValueError):
            evaluate_evidence(self._state(claim))

    def test_t11_untrusted_subsidy_references_do_not_satisfy_documents(self) -> None:
        fabricated = self._state(self._claim(evidence_ids=["made-up-chunk"]))
        mixed_fabricated = self._state(
            self._claim(
                evidence_ids=[self.subsidy.chunk.chunk_id, "made-up-chunk"]
            )
        )
        cross_query = self._state()
        cross_query["subsidy_chunks"] = [
            replace(self.subsidy, query_id="another-query")
        ]
        wrong_policy = self._state()
        wrong_policy["claim_plan"][0]["policy_id"] = "another-policy"

        duplicate_result = self._state()
        duplicate_result["subsidy_chunks"] = [self.subsidy, self.subsidy]

        dual_resolve = self._state()
        dual_resolve["law_chunks"] = [self.subsidy]

        wrong_pool = self._state()
        wrong_pool["subsidy_chunks"] = []
        wrong_pool["law_chunks"] = [self.subsidy]

        unrelated_law = self._state(
            self._claim(
                law_required=True,
                evidence_ids=[
                    self.subsidy.chunk.chunk_id,
                    self.admrul.chunk.chunk_id,
                ],
            )
        )
        unrelated_law["law_chunks"] = [self.admrul]

        for state in (
            fabricated,
            mixed_fabricated,
            cross_query,
            wrong_policy,
            duplicate_result,
            dual_resolve,
            wrong_pool,
            unrelated_law,
        ):
            with self.subTest(state=state):
                result = evaluate_evidence(state)
                self.assertFailReason(result, AbstentionReason.NO_EVIDENCE)
                self.assertEqual(result["missing_document_claim_ids"], [])
                self.assertEqual(result["missing_law_claim_ids"], [])

    def test_t11_malformed_subsidy_metadata_is_terminal(self) -> None:
        malformed_metadata = (
            None,
            [],
            {},
            {"source_id": 7},
            {"source_id": ""},
            {"source_id": "   "},
        )

        for metadata in malformed_metadata:
            with self.subTest(metadata=metadata):
                item = self._retrieved(
                    replace(self.subsidy.chunk, metadata=metadata)
                )
                state = self._state()
                state["subsidy_chunks"] = [item]

                result = evaluate_evidence(state)

                self.assertFailReason(result, AbstentionReason.NO_EVIDENCE)
                self.assertEqual(result["missing_document_claim_ids"], [])
                self.assertEqual(result["missing_law_claim_ids"], [])

    def test_t11_unexpected_law_pair_precedes_stale_regardless_of_order(self) -> None:
        stale_unexpected = self._retrieved(
            replace(
                self.admrul.chunk,
                metadata={
                    **self.admrul.chunk.metadata,
                    "effective_to": self.admrul.chunk.metadata["effective_from"],
                },
            ),
            rank=2,
        )

        for law_order in (
            (self.law, stale_unexpected),
            (stale_unexpected, self.law),
        ):
            with self.subTest(order=[item.chunk.chunk_id for item in law_order]):
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[
                        self.subsidy.chunk.chunk_id,
                        *(item.chunk.chunk_id for item in law_order),
                    ],
                )
                state = self._state(claim)
                state["law_chunks"] = list(law_order)

                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.NO_EVIDENCE
                )

    def test_t11_unexpected_law_pair_precedes_sequence_conflict_in_any_order(
        self,
    ) -> None:
        second_sequence = self._law_with_sequence("276654", rank=3)
        required_sources = [self._source_ref(self.admrul)]

        for law_order in (
            (self.admrul, self.law, second_sequence),
            (second_sequence, self.law, self.admrul),
        ):
            with self.subTest(order=[item.chunk.chunk_id for item in law_order]):
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[
                        self.subsidy.chunk.chunk_id,
                        *(item.chunk.chunk_id for item in law_order),
                    ],
                    required_sources=required_sources,
                )
                state = self._state(claim)
                state["law_chunks"] = list(law_order)

                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.NO_EVIDENCE
                )

    def test_t12_aspect_capability_boundaries(self) -> None:
        for aspects in (None, [], [LEGAL_ARTICLE_BODY_ASPECT]):
            with self.subTest(aspects=aspects):
                claim = self._claim(law_required=True)
                if aspects is None:
                    claim.pop("required_aspects")
                else:
                    claim["required_aspects"] = aspects
                self.assertFailReason(
                    evaluate_evidence(self._state(claim)),
                    AbstentionReason.NO_EVIDENCE,
                )

        for claim in (
            {**self._claim(), "required_aspects": [LEGAL_METADATA_ASPECT]},
            {
                **self._claim(law_required=True),
                "required_aspects": ["unknown_aspect"],
            },
        ):
            with self.subTest(claim=claim):
                with self.assertRaises(ValueError):
                    evaluate_evidence(self._state(claim))

        mixed = self._claim(
            law_required=True,
            evidence_ids=[self.subsidy.chunk.chunk_id, self.law.chunk.chunk_id],
        )
        mixed["required_aspects"] = [
            LEGAL_METADATA_ASPECT,
            LEGAL_ARTICLE_BODY_ASPECT,
        ]
        state = self._state(mixed)
        state["law_chunks"] = [self.law]
        result = evaluate_evidence(state)
        self.assertFailReason(result, AbstentionReason.NO_EVIDENCE)
        self.assertEqual(
            result["abstention_decision"].missing_aspects,
            (LEGAL_ARTICLE_BODY_ASPECT,),
        )

        missing_metadata = self._claim(
            claim_id="claim-2",
            law_required=True,
            required_sources=[self._source_ref(self.admrul)],
        )
        state = self._state()
        state["claim_plan"] = [mixed, missing_metadata]
        state["law_chunks"] = [self.law]
        result = evaluate_evidence(state)
        self.assertEqual(
            set(result["abstention_decision"].missing_aspects),
            {LEGAL_ARTICLE_BODY_ASPECT, LEGAL_METADATA_ASPECT},
        )

    def test_t13_explicit_and_fallback_queries(self) -> None:
        second = self._retrieved(self.subsidy_chunks[1], rank=2)
        claim = self._claim(
            law_required=True,
            evidence_ids=[second.chunk.chunk_id, self.subsidy.chunk.chunk_id],
        )
        state = self._state(claim)
        state["subsidy_chunks"] = [self.subsidy, second]
        state.update(evaluate_evidence(state))

        queries: list[str] = []

        def search(source, query, **kwargs):
            queries.append(query)
            return ()

        search_targeted_laws(state, search=search)
        self.assertEqual(
            queries,
            [f"{self.subsidy.chunk.text}\n{second.chunk.text}"],
        )

        state["claim_plan"][0]["search_query"] = "  explicit law query  "
        queries.clear()
        search_targeted_laws(state, search=search)
        self.assertEqual(queries, ["explicit law query"])

    def test_t14_query_id_is_required_by_both_nodes(self) -> None:
        for value in (None, 7, "   "):
            with self.subTest(value=value):
                state = self._n8_state()
                if value is None:
                    del state["query_id"]
                else:
                    state["query_id"] = value
                with self.assertRaises(ValueError):
                    evaluate_evidence(state)
                with self.assertRaises(ValueError):
                    search_targeted_laws(state, search=lambda *args, **kwargs: ())

    def test_t15_search_call_contract(self) -> None:
        state = self._state(self._claim(law_required=True))
        state.update(evaluate_evidence(state))
        calls: list[tuple] = []

        def search(*args, **kwargs):
            calls.append((args, kwargs))
            return ()

        search_targeted_laws(state, search=search)

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args, (SourceType.LAW, self.subsidy.chunk.text))
        self.assertEqual(kwargs["query_id"], self.query_id)
        self.assertIsInstance(kwargs["search_filter"], VectorSearchFilter)
        self.assertEqual(kwargs["search_filter"].as_of, date.fromisoformat(self.as_of))
        self.assertEqual(
            kwargs["search_filter"].metadata_equals,
            self._source_ref(self.law),
        )

    def test_t15_searches_each_missing_pair_in_declared_order(self) -> None:
        sources = [self._source_ref(self.law), self._source_ref(self.admrul)]
        state = self._n8_state(
            self._claim(law_required=True, required_sources=sources)
        )
        calls = []

        def search(source, query, **kwargs):
            pair = dict(kwargs["search_filter"].metadata_equals)
            calls.append(pair)
            return (self.law,) if pair == sources[0] else (self.admrul,)

        state.update(search_targeted_laws(state, search=search))

        self.assertEqual(calls, sources)
        self.assertEqual(evaluate_evidence(state)["evidence_gate_verdict"], "pass")

    def test_t15a_n8_rejects_invalid_targets_before_search(self) -> None:
        base = self._n8_state()
        invalid_states = []

        entry_changes = (
            ("safety_blocked", True),
            ("safety_blocked", 0),
            ("evidence_gate_verdict", "pass"),
            ("law_retry_count", 0),
            ("law_retry_count", True),
            ("abstention_decision", object()),
            ("abstention_decision", AbstentionDecision(False, None)),
            (
                "abstention_decision",
                AbstentionDecision(True, AbstentionReason.SAFETY),
            ),
            ("missing_document_claim_ids", ()),
            ("missing_document_claim_ids", ["claim-1"]),
        )
        for field_name, value in entry_changes:
            state = deepcopy(base)
            state[field_name] = value
            invalid_states.append(state)

        for targets in ([], ["unknown"], ["claim-1", "claim-1"]):
            state = deepcopy(base)
            state["missing_law_claim_ids"] = targets
            invalid_states.append(state)

        non_law = deepcopy(base)
        non_law["claim_plan"][0]["law_check_required"] = False
        non_law["claim_plan"][0]["required_aspects"] = []
        non_law["claim_plan"][0]["required_law_sources"] = []
        invalid_states.append(non_law)

        satisfied = deepcopy(base)
        satisfied["law_chunks"] = [self.law]
        satisfied["claim_plan"][0]["evidence_chunk_ids"].append(
            self.law.chunk.chunk_id
        )
        invalid_states.append(satisfied)

        invalid_as_of = deepcopy(base)
        invalid_as_of["as_of"] = "20260826"
        invalid_states.append(invalid_as_of)

        missing_safety = deepcopy(base)
        del missing_safety["safety_blocked"]
        invalid_states.append(missing_safety)

        ordered = self._state()
        ordered["claim_plan"] = [
            self._claim(claim_id="claim-1", law_required=True),
            self._claim(claim_id="claim-2", law_required=True),
        ]
        ordered.update(evaluate_evidence(ordered))
        ordered["missing_law_claim_ids"] = ["claim-2", "claim-1"]
        invalid_states.append(ordered)

        for state in invalid_states:
            calls = []
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    search_targeted_laws(
                        state, search=lambda *args, **kwargs: calls.append(args)
                    )
                self.assertEqual(calls, [])

    def test_t16_malformed_search_results_are_errors(self) -> None:
        state = self._n8_state()

        changed_text = "법령 원문으로 가장한 비정규 데이터"
        noncanonical_chunk = replace(
            self.law.chunk,
            text=changed_text,
            content_hash=compute_content_hash(changed_text),
        )
        stale_chunk = replace(
            self.law.chunk,
            metadata={
                **self.law.chunk.metadata,
                "effective_to": self.as_of,
            },
        )
        invalid_results = (
            [replace(self.law, query_id="another-query")],
            [self.subsidy],
            [self.admrul],
            [self._retrieved(noncanonical_chunk)],
            [self._retrieved(stale_chunk)],
            [self._retrieved(replace(self.law.chunk, content_hash="0" * 64))],
            [
                self._retrieved(
                    replace(self.law.chunk, doc_id="law:tampered-document-id")
                )
            ],
            [object()],
            object(),
        )
        for result in invalid_results:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    search_targeted_laws(
                        state, search=lambda *args, result=result, **kwargs: result
                    )

    def test_t16_invalid_law_identity_is_terminal(self) -> None:
        source_id_invalid = replace(
            self.law.chunk,
            metadata={**self.law.chunk.metadata, "source_id": "１２"},
        )
        sequence_invalid = replace(
            self.law.chunk,
            metadata={**self.law.chunk.metadata, "source_sequence": "２７６６５３"},
        )
        for chunk in (source_id_invalid, sequence_invalid):
            with self.subTest(metadata=chunk.metadata):
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[self.subsidy.chunk.chunk_id, chunk.chunk_id],
                )
                state = self._state(claim)
                state["law_chunks"] = [self._retrieved(chunk)]
                result = evaluate_evidence(state)
                self.assertFailReason(result, AbstentionReason.NO_EVIDENCE)
                self.assertEqual(result["missing_law_claim_ids"], [])

    def test_t16_mixed_valid_and_invalid_law_references_cannot_pass(self) -> None:
        invalid_chunk = replace(self.law.chunk, chunk_id="law:invalid-reference")
        invalid_law = self._retrieved(
            invalid_chunk, query_id="another-query", rank=2
        )
        claim = self._claim(
            law_required=True,
            evidence_ids=[
                self.subsidy.chunk.chunk_id,
                invalid_chunk.chunk_id,
                self.law.chunk.chunk_id,
            ],
        )
        state = self._state(claim)
        state["law_chunks"] = [invalid_law, self.law]

        result = evaluate_evidence(state)

        self.assertFailReason(result, AbstentionReason.NO_EVIDENCE)
        self.assertEqual(result["missing_law_claim_ids"], [])

    def test_t16_law_disabled_claim_still_rejects_polluted_law_id(self) -> None:
        invalid_law = replace(self.law, query_id="another-query")
        claim = self._claim(
            evidence_ids=[self.subsidy.chunk.chunk_id, self.law.chunk.chunk_id]
        )
        state = self._state(claim)
        state["law_chunks"] = [invalid_law]

        self.assertFailReason(evaluate_evidence(state), AbstentionReason.NO_EVIDENCE)

    def test_t16_conflicting_duplicate_chunk_is_not_hidden_by_dedupe(self) -> None:
        stale_chunk = replace(
            self.law.chunk,
            metadata={
                **self.law.chunk.metadata,
                "effective_to": self.as_of,
            },
        )
        claim = self._claim(
            law_required=True,
            evidence_ids=[self.subsidy.chunk.chunk_id, self.law.chunk.chunk_id],
        )
        state = self._state(claim)
        state["law_chunks"] = [self.law, self._retrieved(stale_chunk, rank=2)]

        self.assertFailReason(
            evaluate_evidence(state), AbstentionReason.NO_EVIDENCE
        )

    def test_t17_merges_and_deduplicates_in_first_seen_order(self) -> None:
        claim = self._claim(law_required=True)
        state = self._n8_state(claim)
        telemetry_duplicate = replace(self.law, rank=2, score=0.9)
        state["law_chunks"] = [self.law, telemetry_duplicate]

        update = search_targeted_laws(
            state, search=lambda *args, **kwargs: (telemetry_duplicate,)
        )

        self.assertEqual(update["law_chunks"], [self.law])
        self.assertEqual(
            update["claim_plan"][0]["evidence_chunk_ids"],
            [self.subsidy.chunk.chunk_id, self.law.chunk.chunk_id],
        )

    def test_t17_same_id_payload_conflict_is_rejected_before_merge(self) -> None:
        state = self._n8_state()
        state["law_chunks"] = [self.law]
        changed_payload = replace(
            self.law,
            chunk=replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "source_name": "다른 법령 출처",
                },
            ),
        )
        before = deepcopy(state)

        with self.assertRaisesRegex(ValueError, "different evidence payloads"):
            search_targeted_laws(
                state, search=lambda *args, **kwargs: (changed_payload,)
            )
        self.assertEqual(state, before)

    def test_t17_new_law_id_cannot_collide_with_subsidy_pool(self) -> None:
        state = self._n8_state()
        colliding_subsidy = self._retrieved(
            replace(
                self.subsidy.chunk,
                chunk_id=self.law.chunk.chunk_id,
            )
        )
        state["subsidy_chunks"] = [colliding_subsidy]
        state["claim_plan"][0]["evidence_chunk_ids"] = [
            colliding_subsidy.chunk.chunk_id
        ]
        before = deepcopy(state)

        with self.assertRaisesRegex(ValueError, "collides with the subsidy"):
            search_targeted_laws(
                state, search=lambda *args, **kwargs: (self.law,)
            )
        self.assertEqual(state, before)

    def test_t17_new_sequence_conflict_rejects_entire_update(self) -> None:
        state = self._n8_state()
        state["law_chunks"] = [self.law]
        second_sequence = self._law_with_sequence("276654")
        before = deepcopy(state)

        with self.assertRaisesRegex(ValueError, "conflicting source sequences"):
            search_targeted_laws(
                state, search=lambda *args, **kwargs: (second_sequence,)
            )
        self.assertEqual(state, before)

    def test_t20_all_declared_law_sources_are_required(self) -> None:
        sources = [self._source_ref(self.law), self._source_ref(self.admrul)]
        partial_claim = self._claim(
            law_required=True,
            evidence_ids=[self.subsidy.chunk.chunk_id, self.law.chunk.chunk_id],
            required_sources=sources,
        )
        partial = self._state(partial_claim)
        partial["law_chunks"] = [self.law]
        result = evaluate_evidence(partial)
        self.assertEqual(result["evidence_gate_verdict"], "insufficient_law")
        partial.update(result)

        calls = []

        def search(source, query, **kwargs):
            calls.append(dict(kwargs["search_filter"].metadata_equals))
            return (self.admrul,)

        partial.update(search_targeted_laws(partial, search=search))
        self.assertEqual(calls, [sources[1]])
        self.assertEqual(
            evaluate_evidence(partial)["evidence_gate_verdict"], "pass"
        )

        full_claim = self._claim(
            law_required=True,
            evidence_ids=[
                self.subsidy.chunk.chunk_id,
                self.law.chunk.chunk_id,
                self.admrul.chunk.chunk_id,
            ],
            required_sources=sources,
        )
        full = self._state(full_claim)
        full["law_chunks"] = [self.law, self.admrul]
        self.assertEqual(evaluate_evidence(full)["evidence_gate_verdict"], "pass")

    def test_t21_law_dates_hash_and_document_identity_are_strict(self) -> None:
        stale_chunks = (
            replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "effective_from": "2025-10-01T00:00:00",
                },
            ),
            replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "effective_date": "2025-10-02",
                },
            ),
            replace(
                self.law.chunk,
                metadata={**self.law.chunk.metadata, "issued_date": "20250318"},
            ),
            replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "source_updated_at": "20250318",
                },
            ),
            replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "effective_to": self.law.chunk.metadata["effective_from"],
                },
            ),
        )
        changed_text = "정규화된 법령 기본정보가 아닌 임의 텍스트"
        terminal_chunks = (
            replace(self.law.chunk, content_hash="0" * 64),
            replace(self.law.chunk, doc_id="law:tampered-document-id"),
            replace(self.law.chunk, chunk_id="law:arbitrary-chunk-id"),
            replace(self.law.chunk, ordinal=self.law.chunk.ordinal + 1),
            replace(
                self.law.chunk,
                text=changed_text,
                content_hash=compute_content_hash(changed_text),
            ),
            replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "source_url": (
                        "https://www.law.go.kr/LSW/lsInfoP.do?"
                        "lsiSeq=999&efYd=20251001"
                    ),
                },
            ),
            replace(
                self.law.chunk,
                metadata={**self.law.chunk.metadata, "chunk_part": True},
            ),
            replace(
                self.law.chunk,
                metadata={**self.law.chunk.metadata, "chunk_part_count": True},
            ),
            replace(
                self.law.chunk,
                metadata={**self.law.chunk.metadata, "chunk_part_count": 0},
            ),
            replace(
                self.law.chunk,
                metadata={**self.law.chunk.metadata, "chunk_part": 1},
            ),
            replace(
                self.law.chunk,
                metadata={**self.law.chunk.metadata, "chunk_part_count": 2},
            ),
        )
        for chunk in stale_chunks:
            with self.subTest(stale=chunk.metadata):
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[self.subsidy.chunk.chunk_id, chunk.chunk_id],
                )
                state = self._state(claim)
                state["law_chunks"] = [self._retrieved(chunk)]
                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.STALE
                )
        for chunk in terminal_chunks:
            with self.subTest(terminal=chunk):
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[self.subsidy.chunk.chunk_id, chunk.chunk_id],
                )
                state = self._state(claim)
                state["law_chunks"] = [self._retrieved(chunk)]
                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.NO_EVIDENCE
                )

                n8_state = self._n8_state()
                before = deepcopy(n8_state)
                with self.assertRaises(ValueError):
                    search_targeted_laws(
                        n8_state,
                        search=lambda *args, chunk=chunk, **kwargs: (
                            self._retrieved(chunk),
                        ),
                    )
                self.assertEqual(n8_state, before)

    def test_t21_law_metadata_structure_and_source_name_are_strict(self) -> None:
        base_metadata = dict(self.law.chunk.metadata)
        without_source_name = {
            key: value
            for key, value in base_metadata.items()
            if key != "source_name"
        }
        malformed_metadata = (
            None,
            [],
            without_source_name,
            {**base_metadata, "source_name": 7},
            {**base_metadata, "source_name": ""},
            {**base_metadata, "source_name": "   "},
        )

        for metadata in malformed_metadata:
            with self.subTest(metadata=metadata):
                chunk = replace(self.law.chunk, metadata=metadata)
                item = self._retrieved(chunk)
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[self.subsidy.chunk.chunk_id, chunk.chunk_id],
                )
                state = self._state(claim)
                state["law_chunks"] = [item]
                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.NO_EVIDENCE
                )

                n8_state = self._n8_state()
                before = deepcopy(n8_state)
                with self.assertRaises(ValueError):
                    search_targeted_laws(
                        n8_state,
                        search=lambda *args, item=item, **kwargs: (item,),
                    )
                self.assertEqual(n8_state, before)

    def test_t21_stale_precedes_invalid_sequence_regardless_of_order(self) -> None:
        invalid_sequence = self._retrieved(
            replace(
                self.law.chunk,
                metadata={
                    **self.law.chunk.metadata,
                    "source_sequence": "invalid",
                },
            )
        )
        stale = self._retrieved(
            replace(
                self.admrul.chunk,
                metadata={
                    **self.admrul.chunk.metadata,
                    "effective_to": self.admrul.chunk.metadata["effective_from"],
                },
            ),
            rank=2,
        )
        required_sources = [
            self._source_ref(self.law),
            self._source_ref(self.admrul),
        ]

        for law_order in ((invalid_sequence, stale), (stale, invalid_sequence)):
            with self.subTest(order=[item.chunk.chunk_id for item in law_order]):
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[
                        self.subsidy.chunk.chunk_id,
                        *(item.chunk.chunk_id for item in law_order),
                    ],
                    required_sources=required_sources,
                )
                state = self._state(claim)
                state["law_chunks"] = list(law_order)
                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.STALE
                )

    def test_t22_canonical_multipart_subset_passes_both_nodes(self) -> None:
        parts = self._multipart_law()
        subset = (parts[0], parts[-1])
        claim = self._claim(
            law_required=True,
            evidence_ids=[
                self.subsidy.chunk.chunk_id,
                *(item.chunk.chunk_id for item in subset),
            ],
        )
        state = self._state(claim)
        state["law_chunks"] = list(subset)

        self.assertEqual(evaluate_evidence(state)["evidence_gate_verdict"], "pass")

        n8_state = self._n8_state()
        update = search_targeted_laws(
            n8_state, search=lambda *args, **kwargs: subset
        )
        self.assertEqual(update["law_chunks"], list(subset))
        self.assertIs(update["law_chunks"][0], subset[0])
        n8_state.update(update)
        self.assertEqual(
            evaluate_evidence(n8_state)["evidence_gate_verdict"], "pass"
        )

    def test_t22_revision_parent_and_same_part_conflicts_fail_closed(self) -> None:
        first, *_, last = self._multipart_law()
        parent_conflicts = (
            replace(
                last,
                chunk=replace(last.chunk, doc_id="law:tampered-parent-document"),
            ),
            replace(
                last,
                chunk=replace(
                    last.chunk,
                    metadata={
                        **last.chunk.metadata,
                        "source_name": "서로 다른 법령 출처",
                    },
                ),
            ),
        )
        for conflicting in parent_conflicts:
            with self.subTest(conflicting=conflicting.chunk):
                claim = self._claim(
                    law_required=True,
                    evidence_ids=[
                        self.subsidy.chunk.chunk_id,
                        first.chunk.chunk_id,
                        conflicting.chunk.chunk_id,
                    ],
                )
                state = self._state(claim)
                state["law_chunks"] = [first, conflicting]
                self.assertFailReason(
                    evaluate_evidence(state), AbstentionReason.CONFLICT
                )

        same_part = self._retrieved(
            replace(first.chunk, chunk_id="law:different-same-part-payload"),
            rank=2,
        )
        claim = self._claim(
            law_required=True,
            evidence_ids=[
                self.subsidy.chunk.chunk_id,
                first.chunk.chunk_id,
                same_part.chunk.chunk_id,
            ],
        )
        state = self._state(claim)
        state["law_chunks"] = [first, same_part]
        self.assertFailReason(evaluate_evidence(state), AbstentionReason.CONFLICT)

        n8_state = self._n8_state()
        n8_state["law_chunks"] = [first]
        conflicting_search_result = parent_conflicts[1]
        before = deepcopy(n8_state)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            search_targeted_laws(
                n8_state,
                search=lambda *args, **kwargs: (conflicting_search_result,),
            )
        self.assertEqual(n8_state, before)

    def test_t23_document_and_document_id_chunk_helpers_are_equivalent(self) -> None:
        cases = (
            (self.law_document, ChunkingConfig()),
            (
                self._multipart_law_document(),
                ChunkingConfig(max_chars=120, overlap_chars=10),
            ),
        )
        for document, config in cases:
            for chunk in chunk_document(document, config):
                version = chunk.metadata["chunking_version"]
                part = chunk.metadata["chunk_part"]
                expected = compute_chunk_id(
                    document, chunk.heading_path, part, version
                )
                actual = compute_chunk_id_from_document_id(
                    document.doc_id, chunk.heading_path, part, version
                )
                self.assertEqual(actual, expected)
                self.assertEqual(actual, chunk.chunk_id)

    def test_t18_search_exceptions_propagate(self) -> None:
        state = self._n8_state()

        def broken_search(*args, **kwargs):
            raise RuntimeError("retriever unavailable")

        with self.assertRaisesRegex(RuntimeError, "retriever unavailable"):
            search_targeted_laws(state, search=broken_search)

    def test_t19_nodes_return_partial_updates_without_mutation(self) -> None:
        state = self._state(self._claim(law_required=True))
        state["slots"] = {"age": 4}
        before = deepcopy(state)

        gate_update = evaluate_evidence(state)
        self.assertEqual(state, before)
        self.assertEqual(
            set(gate_update),
            {
                "evidence_gate_verdict",
                "abstention_decision",
                "missing_document_claim_ids",
                "missing_law_claim_ids",
                "doc_retry_count",
                "law_retry_count",
            },
        )

        state.update(gate_update)
        before_search = deepcopy(state)
        search_update = search_targeted_laws(
            state, search=lambda *args, **kwargs: (self.law,)
        )
        self.assertEqual(state, before_search)
        self.assertEqual(set(search_update), {"claim_plan", "law_chunks"})
        self.assertEqual(state["slots"], {"age": 4})

    def test_t19_empty_and_success_updates_do_not_alias_nested_claim_data(self) -> None:
        def mutate_returned_claim(update: dict) -> None:
            claim = update["claim_plan"][0]
            claim["evidence_chunk_ids"].append("mutated-evidence")
            claim["reasons"].append("mutated-reason")
            claim["required_law_sources"][0]["source_id"] = "999"

        empty_state = self._n8_state()
        empty_state["law_chunks"] = [self.law]
        empty_before = deepcopy(empty_state)
        empty_update = search_targeted_laws(
            empty_state, search=lambda *args, **kwargs: ()
        )
        self.assertIs(empty_update["law_chunks"][0], self.law)
        mutate_returned_claim(empty_update)
        self.assertEqual(empty_state, empty_before)

        success_state = self._n8_state()
        success_before = deepcopy(success_state)
        success_update = search_targeted_laws(
            success_state, search=lambda *args, **kwargs: (self.law,)
        )
        self.assertIs(success_update["law_chunks"][0], self.law)
        mutate_returned_claim(success_update)
        self.assertEqual(success_state, success_before)


if __name__ == "__main__":
    unittest.main()
