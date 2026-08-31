"""N12 결과 조립 노드.

xlsx 설계표 기준: N12는 "Node (결정론)"이다 - N9/N10/N11과 달리 vectorDB를
검색하지 않는다. 입력이 이미 검증·재확인을 마친 eligibility_verdicts,
benefit_amounts, duplicate_verdicts뿐이라 새로 근거를 찾을 이유가 없고,
여기서 다시 검색하면 오히려 앞 단계에서 확정한 판정을 임의로 뒤집을 위험이
생긴다 (설계 결정: 검색은 N9/N10/N11까지만).

이전 노드들의 출력을 정책별 구조로 모아 N13(답변 생성)에 넘길
assembled_result를 만든다.

- 정책 간 금액 총합 등 근거 없는 합산을 만들지 않는다 - 정책별로 분리된 구조를
  유지한다.
- eligibility_verdicts, benefit_amounts, duplicate_verdicts 중 하나라도 없는
  정책은 방어적으로 필터링하지 않고 "정보 부족"으로 표시한다 (누락을 숨기지
  않는다 - compliance 원칙).
- 어떤 노드를 거쳐왔는지 state["node_trace"]에 "N12"를 추가해 기록한다.
"""

from __future__ import annotations

from ..state import GraphState


def assemble_result(state: GraphState) -> dict:
    """state["eligibility_verdicts"], state["benefit_amounts"],
    state["duplicate_verdicts"]를 정책 단위로 묶어 state["assembled_result"],
    state["node_trace"]를 채워 반환한다 (partial state update).
    """
    eligibility_by_policy = {
        verdict["policy_id"]: verdict
        for verdict in state.get("eligibility_verdicts", [])
    }
    amount_by_policy = {
        entry["policy_id"]: entry for entry in state.get("benefit_amounts", [])
    }
    duplicate_by_policy = {
        entry["policy_id"]: entry for entry in state.get("duplicate_verdicts", [])
    }

    policies: dict[str, dict] = {}
    for policy_id, eligibility in eligibility_by_policy.items():
        entry: dict = {"eligibility": eligibility}

        if eligibility.get("verdict") == "충족":
            amount = amount_by_policy.get(policy_id)
            if amount is None:
                entry["benefit_amount"] = None
                entry["status_note"] = "정보 부족: 지원금 계산 결과 없음"
            else:
                entry["benefit_amount"] = amount

        duplicate = duplicate_by_policy.get(policy_id)
        if duplicate is None:
            entry["duplicate"] = None
            entry.setdefault("status_note", "정보 부족: 중복수급 판정 결과 없음")
        else:
            entry["duplicate"] = duplicate

        policies[policy_id] = entry

    assembled_result = {"policies": policies}

    node_trace = list(state.get("node_trace", []))
    node_trace.append("N12")

    return {"assembled_result": assembled_result, "node_trace": node_trace}
