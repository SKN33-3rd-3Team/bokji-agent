"""Full/retried subsidy evidence must survive the metadata-only N8 branch."""
from dataclasses import replace

import pytest

from rag_design.chunking import chunk_document
from rag_design.contracts import SourceType
from rag_chatbot.graph.nodes.evidence_gate import evaluate_evidence
from rag_chatbot.graph.nodes.targeted_law_search import search_targeted_laws
from rag_chatbot.graph.nodes.duplicate_benefit import check_duplicate_benefit
from tests.test_contracts_and_citations import load_documents
from tests.test_duplicate_benefit_clauses import _chunk as duplicate_clause_fixture
from tests.test_search_evidence_regressions import (
    AS_OF, REASON, OfflineStore, retrieved, subsidy_chunks, verify_official_documents,
)


@pytest.mark.parametrize("mode", ["full_only", "retry_only"])
def test_n6_n7_n8_n7_preserves_subsidy_evidence(mode, record_property):
    chunks = subsidy_chunks()
    matching = next(c for c in chunks if REASON in c.text)
    representative = next(c for c in chunks if REASON not in c.text)
    law_document = next(d for d in load_documents() if d.source_type is SourceType.LAW)
    law_chunk = chunk_document(law_document)[0]
    assert law_chunk.metadata["content_level"] == "metadata_only"
    law = replace(retrieved(matching), chunk=law_chunk, index_name="law")
    source_pair = {"law_type": law_chunk.metadata["law_type"],
                   "source_id": law_chunk.metadata["source_id"]}
    claim = {"claim_id": "law-branch", "policy_id": matching.metadata["source_id"],
             "claim_type": "eligibility", "doc_check_required": True,
             "law_check_required": True, "required_aspects": ["legal_metadata"],
             "required_law_sources": [source_pair], "status": "pending",
             "reasons": [REASON], "evidence_chunk_ids": [],
             "doc_retry_count": int(mode == "retry_only")}
    state = {"query_id": "frozen", "as_of": AS_OF, "safety_blocked": False,
             "claim_plan": [claim], "law_chunks": [],
             "subsidy_chunks": [retrieved(representative)]}
    if mode == "full_only":
        state["subsidy_full_chunks"] = [retrieved(c) for c in chunks]
    state.update(verify_official_documents(state, store=OfflineStore([retrieved(matching)])))
    assert state["claim_plan"][0]["status"] == "supported"
    assert state["claim_plan"][0]["evidence_chunk_ids"] == [matching.chunk_id]
    state.update(evaluate_evidence(state))
    record_property("first_gate", state["evidence_gate_verdict"])
    assert state["evidence_gate_verdict"] == "insufficient_law"
    calls = []

    def search(source, query, *, query_id, search_filter):
        assert source is SourceType.LAW
        assert query_id == "frozen" and query.strip()
        assert search_filter.metadata_equals == source_pair
        assert search_filter.as_of == AS_OF
        calls.append(source_pair)
        return (law,)

    try:
        state.update(search_targeted_laws(state, search=search))
    finally:
        record_property("law_search_calls", len(calls))
    assert calls == [source_pair]
    assert set(state["claim_plan"][0]["evidence_chunk_ids"]) == {
        matching.chunk_id, law_chunk.chunk_id}
    gate = evaluate_evidence(state)
    assert gate["evidence_gate_verdict"] == "pass"
    assert gate["abstention_decision"].abstain is False


@pytest.mark.parametrize("retry", [True, False], ids=["partial_retry_pool", "complete_pool_control"])
def test_n6_retry_does_not_suppress_n11_restriction_search(retry, record_property):
    chunks = subsidy_chunks()
    general = next(c for c in chunks if REASON in c.text)
    representative = next(c for c in chunks if REASON not in c.text)
    policy_id = general.metadata["source_id"]
    # Reuse the existing conditional-clause fixture/oracle for BOTH branches.
    # See test_other_clause_without_a_named_counterpart_is_conditional.
    restriction_text = "타 기관 사업과 중복 지원 불가"
    restriction = duplicate_clause_fixture(policy_id, "청년 월세 지원", "support_target",
                                           "※ " + restriction_text).chunk
    queries = []

    class Store:
        def search(self, source, query, *, query_id, top_k, search_filter):
            assert source is SourceType.SUBSIDY
            assert search_filter.metadata_equals == {"source_id": policy_id}
            queries.append(query)
            if query == policy_id:
                assert top_k == 20
                return (retrieved(general),)
            assert query == policy_id + " 중복수급 병급 제한"
            assert top_k == 8
            return (retrieved(restriction),)

    # Retry belongs to an eligibility claim, not the duplicate claim.
    state = {"query_id": "frozen", "subsidy_chunks": [retrieved(representative)],
             "eligibility_verdicts": [{"policy_id": policy_id, "verdict": "충족"}],
             "claim_plan": [
                 {"claim_id": "general", "policy_id": policy_id, "claim_type": "eligibility",
                  "status": "supported", "doc_check_required": True, "doc_retry_count": int(retry),
                  "reasons": [REASON]},
                 {"claim_id": "duplicate", "policy_id": policy_id, "claim_type": "duplicate",
                  "status": "supported", "doc_check_required": False, "doc_retry_count": 0,
                  "reasons": []}]}
    if not retry:
        state["subsidy_full_chunks"] = [retrieved(general), retrieved(restriction)]
    store = Store()
    state.update(verify_official_documents(state, store=store))
    assert state["claim_plan"][0]["status"] == "supported"
    result = check_duplicate_benefit(state, store)["duplicate_verdicts"][0]
    observed = {"status": result["status"], "restriction_clauses": result["restriction_clauses"],
                "queries": queries}
    record_property("n11_observed", repr(observed))
    assert observed == {"status": "조건부", "restriction_clauses": [restriction_text],
                        "queries": [policy_id, policy_id + " 중복수급 병급 제한"] if retry else []}
