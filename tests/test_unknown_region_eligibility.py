"""UNKNOWN region is a candidate, never an assertion of regional eligibility."""
import pytest

from rag_design.contracts import EvidenceStatus
from src.rag_chatbot.graph.nodes import determine_eligibility, generate_answer, verify_final_answer
from src.rag_chatbot.graph.nodes.result_assembly import assemble_result
from src.rag_chatbot.llm import FakeLLMClient, FailingLLMClient
from src.rag_chatbot.service import _build_policy_view, _rank_policies
from tests.test_graph_nodes import FakeStore, _claim, _retrieved_chunk
from tests.test_service import FakeDetailStore

NOTICE = "지역 조건 추가 확인 필요"


def state_and_store(age=70, status=EvidenceStatus.SUPPORTED, scope="unknown"):
    chunk = _retrieved_chunk("policy-a", {"region_scope": scope,
        "region_names": [] if scope == "unknown" else ["전국"] if scope == "national" else ["서울특별시"],
        "age_start": 65, "source_url": "https://www.gov.kr/portal/service/serviceInfo/policy-a"})
    claim = _claim("policy-a", status, reasons=("기존 근거 상태",))
    claim["evidence_chunk_ids"] = [chunk.chunk.chunk_id]
    return {"query_id": "q1", "slots": {"age": age, "region_names": []},
            "claim_plan": [claim], "subsidy_chunks": [chunk]}, FakeStore({"policy-a": [chunk]})


@pytest.mark.parametrize("age,expected", [(70, "미확인"), (18, "미충족")])
def test_region_only_is_added_without_erasing_age_or_violation(age, expected):
    state, store = state_and_store(age)
    verdict = determine_eligibility(state, store)["eligibility_verdicts"][0]
    assert verdict["verdict"] == expected
    assert verdict["checked"] == ["연령"]
    assert "지역" in verdict["unchecked"] and "소득 수준" in verdict["unchecked"]
    assert NOTICE in verdict["reasons"]
    assert any(("연령 조건 미충족" if age == 18 else "기존 근거 상태") in r for r in verdict["reasons"])
    assert state["subsidy_chunks"][0].chunk.metadata["region_names"] == []


@pytest.mark.parametrize("scope", ["national", "regional"])
def test_known_policy_region_is_not_unknown_because_user_address_is_unknown(scope):
    state, store = state_and_store(scope=scope)
    verdict = determine_eligibility(state, store)["eligibility_verdicts"][0]
    assert verdict["verdict"] == "충족"
    assert "지역" not in verdict["unchecked"]
    assert NOTICE not in verdict["reasons"]


@pytest.mark.parametrize("status", [EvidenceStatus.UNSUPPORTED, EvidenceStatus.PARTIAL,
                                   EvidenceStatus.CONFLICT, EvidenceStatus.NOT_APPLICABLE])
def test_existing_uncertainty_and_empty_evidence_are_not_upgraded(status):
    state, store = state_and_store(status=status)
    verdict = determine_eligibility(state, store)["eligibility_verdicts"][0]
    assert verdict["verdict"] == "미확인" and not verdict.get("checked")
    assert verdict["unchecked"] == ["지역"]
    assert len(verdict["reasons"]) == 2 and NOTICE in verdict["reasons"]
    assert not store.calls


def test_recheck_metadata_and_missing_recheck_both_preserve_region_notice():
    state, store = state_and_store()
    state["subsidy_chunks"] = []
    assert "지역" in determine_eligibility(state, store)["eligibility_verdicts"][0]["unchecked"]
    state, _ = state_and_store()
    verdict = determine_eligibility(state, FakeStore({}))["eligibility_verdicts"][0]
    assert verdict["verdict"] == "미확인"
    assert "재검색에서 해당 정책 근거를 다시 찾지 못함" in verdict["reasons"]
    assert NOTICE in verdict["reasons"]


def test_other_policy_unknown_does_not_contaminate_known_policy():
    state, store = state_and_store(scope="national")
    state["subsidy_full_chunks"] = [_retrieved_chunk("other", {"region_scope": "unknown", "region_names": []})]
    assert determine_eligibility(state, store)["eligibility_verdicts"][0]["verdict"] == "충족"


def test_unknown_region_negative_reason_cannot_be_rewritten_as_eligible():
    state, store = state_and_store(age=18)
    client = FakeLLMClient(response="모든 자격 조건 충족")
    verdict = determine_eligibility(state, store, llm_client=client)["eligibility_verdicts"][0]
    assert verdict["verdict"] == "미충족" and not client.calls
    assert "연령 조건 미충족" in verdict["reasons"][0]
    assert NOTICE in verdict["reasons"]


@pytest.mark.parametrize("age,expected", [(70, "미확인"), (18, "미충족")])
@pytest.mark.parametrize("mode", ["none", "rewriter", "failing"])
def test_n9_to_final_and_service_keeps_notice_and_never_overall_eligible(age, expected, mode):
    state, store = state_and_store(age)
    state.update(determine_eligibility(state, store))
    state.update(assemble_result(state, store))
    client = None if mode == "none" else FakeLLMClient(response="모든 자격 조건 충족") if mode == "rewriter" else FailingLLMClient()
    state.update(generate_answer(state, llm_client=client))
    result = verify_final_answer(state)
    assert NOTICE in result["final_answer"]
    assert "모든 자격 조건 충족" not in result["final_answer"]
    assert "연령" in result["final_answer"] and "소득 수준" in result["final_answer"]
    assert result["answer_status"] == "partial"
    entry = state["assembled_result"]["policies"]["policy-a"]
    view = _build_policy_view("policy-a", entry, store=FakeDetailStore({}), query_id="q1", rank=1, is_top=True)
    assert view["eligibility_status"] == expected
    assert view["verification_checked"] == ["연령"]
    assert "지역" in view["verification_unchecked"] and NOTICE in view["eligibility_reasons"]
    assert _rank_policies({"policy-a": entry, "known": {"eligibility": {"verdict": "충족"}}})[0][0] == "known"
    if mode == "rewriter":
        assert not client.calls


def test_no_citations_retains_abstention_and_region_notice():
    state, store = state_and_store(status=EvidenceStatus.UNSUPPORTED)
    state.update(determine_eligibility(state, store))
    state.update(assemble_result(state, store))
    state["draft_answer"] = "모든 조건 충족"
    state["citations"] = []
    final = verify_final_answer(state)
    assert final["answer_status"] == "abstained" and not final["final_citations"]
    assert NOTICE in final["final_answer"] and "모든 조건 충족" not in final["final_answer"]


@pytest.mark.parametrize("age,expected", [(70, "미확인"), (18, "미충족")])
def test_mixed_policies_keep_region_notice_scoped_through_final(age, expected):
    state, _ = state_and_store(age)
    unknown = state["subsidy_chunks"][0]
    known = _retrieved_chunk("policy-known", {
        "region_scope": "national", "region_names": ["전국"], "age_start": 0,
        "source_url": "https://www.gov.kr/portal/service/serviceInfo/policy-known",
    })
    known_claim = _claim("policy-known", EvidenceStatus.SUPPORTED)
    known_claim["evidence_chunk_ids"] = [known.chunk.chunk_id]
    state["claim_plan"].append(known_claim)
    state["subsidy_chunks"].append(known)
    state["subsidy_full_chunks"] = [unknown, known]
    store = FakeStore({"policy-a": [unknown], "policy-known": [known]})

    state.update(determine_eligibility(state, store))
    verdicts = {v["policy_id"]: v for v in state["eligibility_verdicts"]}
    assert verdicts["policy-a"]["verdict"] == expected
    assert verdicts["policy-a"]["checked"] == ["연령"]
    assert verdicts["policy-a"]["unchecked"].count("지역") == 1
    assert verdicts["policy-known"]["verdict"] == "충족"
    assert "지역" not in verdicts["policy-known"]["unchecked"]
    assert NOTICE not in verdicts["policy-known"]["reasons"]

    state.update(assemble_result(state, store))
    client = FakeLLMClient(response="모든 자격 조건 충족")
    state.update(generate_answer(state, llm_client=client))
    final = verify_final_answer(state)
    assert not client.calls
    assert final["answer_status"] == "partial"
    assert f"policy-a: {NOTICE}" in final["final_answer"]
    assert f"policy-known: {NOTICE}" not in final["final_answer"]
    assert "모든 자격 조건 충족" not in final["final_answer"]
    assert final["final_citations"]
    assert unknown.chunk.metadata["region_scope"] == "unknown"
    assert unknown.chunk.metadata["region_names"] == []
