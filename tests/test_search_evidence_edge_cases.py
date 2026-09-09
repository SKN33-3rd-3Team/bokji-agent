"""Independent supplemental contracts; never included in the frozen27 metric."""
from dataclasses import replace
import json

import pytest

from rag_design.vector_store import ChromaVectorStore, VectorSearchFilter
from rag_design.contracts import SourceType, compute_content_hash
from rag_chatbot.graph.nodes.evidence_gate import evaluate_evidence
from rag_chatbot.graph.nodes.answer_generation import generate_answer
from rag_chatbot.graph.nodes.final_verification import verify_final_answer
from tests.test_search_evidence_regressions import (
    AS_OF, REASON, OfflineStore, determine_eligibility, retrieved,
    search_policies, subsidy_chunks, verify_official_documents,
)


def claim(policy_id):
    return {"claim_id": "edge-" + policy_id, "policy_id": policy_id,
            "claim_type": "eligibility", "doc_check_required": True,
            "law_check_required": False, "status": "supported",
            "reasons": [REASON], "evidence_chunk_ids": []}


@pytest.mark.parametrize("mode", ["full_only", "retry_only", "shared_identical_id",
                                  "conflicting_same_id", "duplicate_in_original_pool"])
def test_evidence_chain_through_citations(mode, record_property):
    chunks = subsidy_chunks()
    matching = next(c for c in chunks if REASON in c.text)
    representative = next(c for c in chunks if REASON not in c.text) if mode in (
        "full_only", "retry_only") else matching
    state = {"query_id": "frozen", "as_of": AS_OF, "slots": {},
             "safety_blocked": False, "law_chunks": []}
    state.update(search_policies(state, OfflineStore([retrieved(representative)], chunks)))
    state["claim_plan"] = [claim(matching.metadata["source_id"])]
    retry_store = None
    if mode == "retry_only":
        state.pop("subsidy_full_chunks")
        state["claim_plan"][0]["doc_retry_count"] = 1
        retry_store = OfflineStore([retrieved(matching)])
    elif mode == "conflicting_same_id":
        text = matching.text + "\n충돌하는 별도 버전"
        conflicting = replace(matching, text=text, content_hash=compute_content_hash(text))
        state["subsidy_full_chunks"] = [retrieved(conflicting)]
    elif mode == "duplicate_in_original_pool":
        state["subsidy_chunks"].append(retrieved(matching))
    state.update(verify_official_documents(state, store=retry_store))
    assert state["claim_plan"][0]["status"] == "supported"
    assert state["claim_plan"][0]["evidence_chunk_ids"] == [matching.chunk_id]
    gate = evaluate_evidence(state)
    record_property("n6_evidence_ids", json.dumps([matching.chunk_id]))
    record_property("n7_decision", repr(gate["abstention_decision"]))
    if mode in ("conflicting_same_id", "duplicate_in_original_pool"):
        assert gate["evidence_gate_verdict"] == "fail", gate
        return
    state.update(gate)
    state["assembled_result"] = {"policies": {matching.metadata["source_id"]: {}}}
    # Direct downstream probes also run on a red gate to expose each consumer.
    # This does not simulate the real graph ignoring its gate failure.
    state.update(generate_answer(state, llm_client=None))
    final = verify_final_answer(state)
    expected_citation = {"policy_id": matching.metadata["source_id"],
        "chunk_id": matching.chunk_id, "source_url": matching.metadata["source_url"],
        "label": "근거 문서"}
    isolated_final = verify_final_answer({**state, "citations": [expected_citation]})
    observed = {"gate": gate["evidence_gate_verdict"], "citations": state["citations"],
                "final_citations": final["final_citations"],
                "final_with_known_citation": isolated_final["final_citations"]}
    record_property("chain_observed", repr(observed))
    assert observed == {"gate": "pass", "citations": [expected_citation],
                        "final_citations": [expected_citation],
                        "final_with_known_citation": [expected_citation]}


@pytest.mark.parametrize("year_age,expected", [(18, False), (19, True)])
def test_year_age_only_filter_enforces_year_minimum(year_age, expected):
    chunk = subsidy_chunks()[0]
    chunk = replace(chunk, metadata={**chunk.metadata, "region_scope": "national",
                    "region_names": ["전국"], "age_basis": "year",
                    "age_start": 19, "age_end": None})
    store = object.__new__(ChromaVectorStore)
    assert store._matches_filter(chunk, {}, SourceType.SUBSIDY,
                                 VectorSearchFilter(year_age=year_age)) is expected


@pytest.mark.parametrize("subject", ["child", "unknown", None])
def test_supported_claim_cannot_confirm_unresolved_subject_age(subject):
    # Requester satisfies this age bound: still cannot establish a child's age.
    chunk = subsidy_chunks()[0]
    chunk = replace(chunk, metadata={**chunk.metadata, "age_start": 65,
                    "age_end": None, "age_basis": "international_age"})
    state = {"query_id": "frozen", "as_of": AS_OF, "slots": {
        "birth_date": "1945-12-31", "age": 80, "age_year_based": 81,
        "age_subject": subject}, "claim_plan": [claim(chunk.metadata["source_id"])]}
    verdict = determine_eligibility(state, OfflineStore([retrieved(chunk)]))["eligibility_verdicts"][0]
    assert verdict["verdict"] == "미확인"
    assert "연령" not in verdict["checked"]
    assert not any("연령 조건 미충족" in reason for reason in verdict["reasons"])


def test_two_policies_use_their_own_full_or_representative_evidence():
    matching = next(c for c in subsidy_chunks() if REASON in c.text)
    foreign = replace(matching, chunk_id="other-evidence", doc_id="other-doc",
                      metadata={**matching.metadata, "source_id": "other-policy"})
    state = {"claim_plan": [claim(matching.metadata["source_id"]), claim("other-policy")],
             "subsidy_chunks": [retrieved(matching)],
             "subsidy_full_chunks": [retrieved(foreign)]}
    results = verify_official_documents(state)["claim_plan"]
    assert [(c["status"], c["evidence_chunk_ids"]) for c in results] == [
        ("supported", [matching.chunk_id]), ("supported", [foreign.chunk_id])]


@pytest.mark.parametrize("value", [False, 19.5, [], -1, 121])
def test_optional_year_age_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="year_age"):
        VectorSearchFilter(year_age=value)


def test_legacy_positional_filter_still_enforces_actual_age():
    chunk = subsidy_chunks()[0]
    chunk = replace(chunk, metadata={**chunk.metadata, "region_scope": "national",
                    "region_names": ["전국"], "age_start": 19, "age_end": None})
    legacy = VectorSearchFilter(None, ("서울특별시",), 18, False,
                               {"source_id": chunk.metadata["source_id"]}, "legacy-snapshot")
    store = object.__new__(ChromaVectorStore)
    for basis in (None, "unknown", "year"):
        candidate = replace(chunk, metadata={**chunk.metadata, "age_basis": basis})
        assert not store._matches_filter(candidate, {"snapshot_id": "legacy-snapshot"},
                                         SourceType.SUBSIDY, legacy)
    for value in (0, 120):
        assert VectorSearchFilter(year_age=value).year_age == value
