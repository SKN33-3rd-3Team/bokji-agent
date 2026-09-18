"""Automatic mode uses real graph nodes; only provider/store I/O is substituted."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from unittest.mock import Mock

import pytest

from rag_design.contracts import compute_content_hash
from src.rag_chatbot import service
from src.rag_chatbot.graph.builder import build_graph, run_graph, resume_graph
from src.rag_chatbot.graph.nodes.policy_search import search_policies
from src.rag_chatbot.graph.nodes.result_assembly import assemble_result
from src.rag_chatbot.llm import FailingLLMClient
from src.rag_chatbot.llm.client import GraphProviderError
from tests.test_search_evidence_regressions import OfflineStore, retrieved, subsidy_chunks


def policy_store(*specs):
    chunks = []
    for spec in specs:
        pid = spec["id"]
        for chunk in subsidy_chunks():
            section = chunk.metadata["section_type"]
            text = spec.get("text", "지원금은 기관에서 정합니다.") if section == "support_details" else "국민에게 복지 서비스를 제공합니다."
            chunks.append(replace(chunk, doc_id=f"subsidy:{pid}:2026-01-29", chunk_id=f"{pid}-{chunk.ordinal}",
                text=text, content_hash=compute_content_hash(text), metadata={
                    **chunk.metadata, "source_id": pid, "title": pid,
                    "region_scope": spec.get("scope", "national"),
                    "region_names": spec.get("regions", ["전국"]),
                    "age_start": spec.get("age_start"), "age_end": spec.get("age_end"),
                    "amount": spec.get("amount"),
                }))
    class Store(OfflineStore):
        def search(self, *args, query_id, **kwargs):
            return [replace(item, query_id=query_id) for item in super().search(*args, **kwargs)]
    return Store([retrieved(c) for c in chunks], chunks)


def test_actual_graph_filters_region_keeps_unknown_money_and_exits_auto(monkeypatch):
    store = policy_store({"id": "local", "scope": "regional", "regions": ["서울특별시"]},
                         {"id": "unknown", "scope": "unknown", "regions": []},
                         {"id": "national"})
    graph = build_graph(store)
    result = run_graph(graph, user_input="", session_id="auto", automatic_recommendation=True,
                       as_of=date(2026, 9, 18))
    assert "__interrupt__" not in result and result["answer_status"] == "partial", (result.get("abstention_decision"), result.get("claim_plan"))
    assert set(result["assembled_result"]["policies"]) == {"national"}
    assert result["slots"].get("employment_status") is None
    response = service._to_chat_response(result, session_id="auto", store=store)
    card, = response["policies"]
    assert card["eligibility_status"] == "충족" and card["verification_unchecked"]
    assert card["amount_label"] is None and card["amount_is_maximum"] is None
    assert "지원금:" not in response["final_answer"]
    assert "local" not in response["output_text"] and "unknown" not in response["output_markdown"]
    assert response["final_citations"] and all(c["policy_id"] == "national" for c in response["final_citations"])
    next_turn = resume_graph(graph, session_id="auto", user_input="주거 지원")
    assert next_turn["automatic_recommendation"] is False
    assert next_turn["initial_user_input"] == "주거 지원"
    assert next_turn["__interrupt__"] and "employment_status" in next_turn["missing_slots"]


def test_nationwide_is_checked_before_candidate_limit_and_supplied_region_unchanged():
    store = policy_store({"id": "bad-scope", "scope": "unknown", "regions": []},
                         {"id": "bad-target", "scope": "national", "regions": []},
                         {"id": "local", "scope": "regional", "regions": ["서울특별시"]},
                         {"id": "national"})
    # I/O fake deliberately ignores metadata filtering: N4 still verifies the target.
    store.search = Mock(return_value=store.candidates)
    state = {"automatic_recommendation": True, "query_id": "auto", "as_of": date(2026, 9, 18), "slots": {}}
    found = search_policies(state, store, top_k=1)["subsidy_chunks"]
    assert [c.chunk.metadata["source_id"] for c in found] == ["national"]
    assert store.search.call_args.kwargs["search_filter"].metadata_equals == {"region_scope": "national"}
    state["slots"] = {"region_scope": "regional", "region_names": ["서울특별시"]}
    search_policies(state, store)
    assert store.search.call_args.kwargs["search_filter"].metadata_equals == {}
    assert store.search.call_args.kwargs["search_filter"].region_names == ("서울특별시",)


@pytest.mark.parametrize("abstained", [False, True])
def test_global_and_individual_filter_outputs_citations_conflicts_and_general_unchanged(abstained):
    def entry(verdict):
        return {"eligibility": {"verdict": verdict, "checked": ["연령"], "unchecked": ["소득 수준"]},
                "benefit_amount": {"amount": 0},
                "duplicate": {"status": "조건부", "conflicts_with": ["keep", "unknown", "unmet"]}}
    result = {"automatic_recommendation": True, "answer_status": "abstained" if abstained else "partial",
              "assembled_result": {"policies": {"keep": entry("충족"), "unknown": entry("미확인"), "unmet": entry("미충족")}},
              "final_answer": "old unknown unmet answer", "final_citations": [
                  {"policy_id": p, "chunk_id": p} for p in ("keep", "unknown", "unmet")]}
    before = deepcopy(result)
    store = Mock(search=Mock(return_value=[]))
    response = service._to_chat_response(result, session_id="auto", store=store)
    expected = [] if abstained else ["keep"]
    assert [p["policy_id"] for p in response["policies"]] == expected
    assert response["output_json"]["policies"] == response["policies"]
    assert response["output_json"]["final_answer"] == response["final_answer"]
    assert response["output_json"]["evidence_count"] == len(expected)
    assert [c["policy_id"] for c in response["final_citations"]] == expected
    assert response["output_json"]["summary"]["checked"] == len(expected)
    assert "unknown" not in response["final_answer"] and "unmet" not in response["output_text"]
    if not abstained:
        card, = response["policies"]
        assert card["rank"] == 1 and card["badge"] == "우선 검토"
        assert [c["policy_id"] for c in card["duplicate_conflicts"]] == ["keep"]
        assert card["amount"] == 0 and "0원" in response["final_answer"]
    assert result == before  # raw evidence/state preserved
    result["automatic_recommendation"] = False
    assert len(service._to_chat_response(result, session_id="normal", store=store)["policies"]) == 3


@pytest.mark.parametrize("benefit,label", [
    ({"amount": 0}, "0원"),
    ({"amount": 100, "period": "month", "per_unit": "person", "total_amount": None}, "1인당 월 100원"),
    ({"amount_min": 100, "amount_max": 200, "total_amount_min": 1200, "total_amount_max": 2400}, "100원~200원 (총 1,200원~2,400원)"),
    ({"total_amount": 500}, "총 500원"),
    ({"total_amount_min": 100, "total_amount_max": 200}, "총 100원~200원"),
    ({"amount": None, "period": "month", "is_maximum": True}, None),
])
def test_known_zero_ranges_unit_total_and_missing_money(benefit, label):
    result = {"automatic_recommendation": True, "answer_status": "partial", "assembled_result": {"policies": {
        "p": {"eligibility": {"verdict": "충족"}, "benefit_amount": benefit}}}}
    response = service._to_chat_response(result, session_id="auto", store=Mock(search=Mock(return_value=[])))
    card, = response["policies"]
    assert card["amount_label"] == label
    if label is None:
        assert all(card[field] is None for field in ("amount", "amount_label", "amount_period", "amount_is_maximum",
                   "amount_per_unit", "amount_total", "amount_min", "amount_max", "total_amount_min", "total_amount_max"))
        assert " | 충족 |  | " in response["output_markdown"]
    else:
        assert label in response["final_answer"]


def test_actual_provider_failure_does_not_become_zero_or_rule_fallback():
    store = policy_store({"id": "a"}, {"id": "b"})  # exercises N5 prefetch too
    graph = build_graph(store, llm_client=FailingLLMClient())
    with pytest.raises(GraphProviderError):
        run_graph(graph, user_input="", session_id="auto", automatic_recommendation=True)
    # The same cached graph still permits the established ordinary fallback policy.
    result = run_graph(graph, user_input="", session_id="normal", slots={
        "region_scope": "national", "region_names": ["전국"], "birth_date": "2000-01-01",
        "gender": "female", "income_bracket": "under_30", "employment_status": "not_working",
        "disability_status": "not_registered"})
    assert "__interrupt__" not in result and result["answer_status"] in ("complete", "partial"), (result.get("missing_slots"), result.get("abstention_decision"))


def test_real_calculator_missing_children_never_interrupts_auto():
    class Provider:
        def complete(self, prompt, **kwargs):
            if "[정책 원문]" in prompt:
                text = prompt.split("[정책 원문]\n", 1)[1].split("\n\n", 1)[0]
                return json.dumps({"claims": [{"claim_type": kind, "reasons": [text]}
                                              for kind in ("eligibility", "amount", "duplicate")]})
            return json.dumps({"variable": "children_count", "tiers": [
                {"label": "첫째", "match_min": 1, "match_max": 1, "amount": 100000},
                {"label": "둘째", "match_min": 2, "match_max": 2, "amount": 200000},
            ]})
    store = policy_store({"id": "children", "text": "자녀 수에 따라 첫째 10만원, 둘째 20만원을 지원합니다."})
    graph = build_graph(store, llm_client=Provider())
    result = run_graph(graph, user_input="", session_id="auto-calc", automatic_recommendation=True)
    assert result["calc_missing_slots"] == ["children_count"]
    assert "__interrupt__" not in result and not result["slots"].get("children_count")
    response = service._to_chat_response(result, session_id="auto-calc", store=store)
    card, = response["policies"]
    assert card["amount"] is None and card["amount_label"] is None
    assert "지원금:" not in response["final_answer"]


def test_auto_duplicate_refs_keep_source_clause_without_recommending_removed_card():
    state = {"automatic_recommendation": True,
             "eligibility_verdicts": [{"policy_id": "keep", "verdict": "충족"},
                                      {"policy_id": "removed", "verdict": "미확인"}],
             "benefit_amounts": [{"policy_id": "keep", "amount": 0}],
             "duplicate_verdicts": [{"policy_id": "keep", "status": "조건부",
                  "conflicts_with": ["removed"], "condition_note": "함께 추천되었습니다.",
                  "restriction_clauses": ["다른 정책 수급자는 중복 지급하지 않습니다."]}]}
    policies = assemble_result(state, Mock())["assembled_result"]["policies"]
    assert set(policies) == {"keep"}
    duplicate = policies["keep"]["duplicate"]
    assert duplicate["status"] == "조건부" and duplicate["conflicts_with"] == []
    assert duplicate["restriction_clauses"] == state["duplicate_verdicts"][0]["restriction_clauses"]
    assert "함께 추천" not in duplicate["condition_note"]
    assert duplicate["restriction_clauses"][0] in duplicate["condition_note"]
