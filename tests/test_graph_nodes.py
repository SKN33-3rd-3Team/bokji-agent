"""src/rag_chatbot/graph/nodes 노드 단위 테스트 (N12부터 시작 - 브랜치별 분리)."""

from __future__ import annotations

from src.rag_chatbot.graph.nodes import assemble_result


def _eligibility(policy_id: str, verdict: str) -> dict:
    return {"policy_id": policy_id, "verdict": verdict, "reasons": ["근거"]}


def _amount(policy_id: str, amount: float) -> dict:
    return {
        "policy_id": policy_id,
        "amount": amount,
        "rule_chunk_id": f"{policy_id}-chunk",
        "calculation_note": "테스트용",
    }


def _duplicate(policy_id: str, status: str) -> dict:
    return {
        "policy_id": policy_id,
        "status": status,
        "conflicts_with": [],
        "condition_note": "테스트용",
    }


def test_complete_policy_is_assembled_with_all_parts():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [_amount("policy-a", 300000)],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["eligibility"]["verdict"] == "충족"
    assert entry["benefit_amount"]["amount"] == 300000
    assert entry["duplicate"]["status"] == "미확인"
    assert "status_note" not in entry
    assert result["node_trace"] == ["N12"]


def test_충족_without_amount_result_marks_정보_부족_not_dropped():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["benefit_amount"] is None
    assert "정보 부족" in entry["status_note"]


def test_미충족_policy_has_no_benefit_amount_key():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "미충족")],
        "benefit_amounts": [],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인")],
    }

    result = assemble_result(state)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert "benefit_amount" not in entry  # 미충족 정책엔 금액 계산 자체를 안 함


def test_missing_duplicate_verdict_marks_정보_부족():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족")],
        "benefit_amounts": [_amount("policy-a", 100000)],
        "duplicate_verdicts": [],
    }

    result = assemble_result(state)

    entry = result["assembled_result"]["policies"]["policy-a"]
    assert entry["duplicate"] is None
    assert "정보 부족" in entry["status_note"]


def test_multiple_policies_amounts_are_never_summed():
    state = {
        "eligibility_verdicts": [_eligibility("policy-a", "충족"), _eligibility("policy-b", "충족")],
        "benefit_amounts": [_amount("policy-a", 100000), _amount("policy-b", 200000)],
        "duplicate_verdicts": [_duplicate("policy-a", "미확인"), _duplicate("policy-b", "미확인")],
    }

    result = assemble_result(state)

    policies = result["assembled_result"]["policies"]
    assert policies["policy-a"]["benefit_amount"]["amount"] == 100000
    assert policies["policy-b"]["benefit_amount"]["amount"] == 200000
    assert "total" not in result["assembled_result"]  # 합산 필드 자체가 없어야 함


def test_node_trace_appends_to_existing_trace():
    state = {
        "eligibility_verdicts": [],
        "benefit_amounts": [],
        "duplicate_verdicts": [],
        "node_trace": ["N1", "N4", "N9"],
    }

    result = assemble_result(state)

    assert result["node_trace"] == ["N1", "N4", "N9", "N12"]
