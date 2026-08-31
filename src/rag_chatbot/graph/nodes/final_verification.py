"""N14 최종 Claim-Citation 검증 노드.

N13(generate_answer)이 만든 state["draft_answer"] / state["citations"]가
실제 검증된 근거로만 구성됐는지 마지막으로 확인하고, 사용자에게 내보낼
최종 답변(state["final_answer"])과 상태(state["answer_status"])를 정한다.
Issue #25(graph builder 조립)에서 추가했다.

- state["citations"]는 이미 N13이 claim_plan의 evidence_chunk_ids로만
  조립했지만, 이 노드는 각 chunk_id가 실제로 state["subsidy_chunks"] /
  state["law_chunks"]에 존재하는지 다시 한 번 확인한다 - 두 노드 사이에서
  값이 조용히 섞이거나 잘못 전달되는 걸 막는 마지막 방어선이다. 검증에
  실패한 인용은 조용히 버린다(모델이 지어냈다고 가정하지, 사용자에게
  노출하지 않는다).
- assembled_result의 각 정책 중 하나라도 "정보 부족"(status_note)으로
  표시돼 있으면 완료로 단정하지 않고 부분 응답(partial)으로 낮춘다.
- 검증된 근거가 하나도 없으면(citations가 전부 걸러졌거나 애초에 없음)
  draft_answer를 그대로 노출하지 않고 확인 불가(abstained)로 처리한다 -
  N7의 fail 경로(E14)와 같은 이유로, 근거 없는 문장을 사용자에게 보여주지
  않는다.
"""

from __future__ import annotations

from ..state import AnswerStatus, CitationEntry, GraphState

_ABSTAIN_MESSAGE = (
    "죄송합니다. 확인된 근거가 부족해 답변을 제공할 수 없습니다. "
    "관련 기관의 공식 안내를 확인해 주세요."
)


def _known_chunk_ids(state: GraphState) -> set[str]:
    ids: set[str] = set()
    for retrieved in state.get("subsidy_chunks", []) or []:
        ids.add(retrieved.chunk.chunk_id)
    for retrieved in state.get("law_chunks", []) or []:
        ids.add(retrieved.chunk.chunk_id)
    return ids


def verify_final_answer(state: GraphState) -> dict:
    """draft_answer/citations를 검증해 final_answer/answer_status를 채워
    반환한다 (partial state update)."""

    known_ids = _known_chunk_ids(state)
    raw_citations: list[CitationEntry] = state.get("citations", []) or []
    verified_citations = [c for c in raw_citations if c.get("chunk_id") in known_ids]
    dropped = len(raw_citations) - len(verified_citations)

    assembled = state.get("assembled_result") or {}
    policies = assembled.get("policies", {})
    has_incomplete_policy = any(entry.get("status_note") for entry in policies.values())

    node_trace = list(state.get("node_trace", []))
    node_trace.append("N14")

    if not policies or not verified_citations:
        status: AnswerStatus = "abstained"
        return {
            "final_answer": _ABSTAIN_MESSAGE,
            "final_citations": [],
            "answer_status": status,
            "node_trace": node_trace,
        }

    status = "partial" if (dropped > 0 or has_incomplete_policy) else "complete"
    return {
        "final_answer": state.get("draft_answer", ""),
        "final_citations": verified_citations,
        "answer_status": status,
        "node_trace": node_trace,
    }


def route_final_verification(state: GraphState) -> str:
    """N14 판정 이후 종착 라우팅.

    다이어그램 기준 E23(통과)은 complete/partial을 모두 포함한다 - "완료 /
    부분 응답"이 같은 종착지이기 때문이다. abstained만 E22(근거 부족)로
    별도 종착지("확인 불가 / 부분 응답")로 보낸다.
    """
    routes = {
        "complete": "terminal_success",
        "partial": "terminal_success",
        "abstained": "terminal_insufficient",
    }
    try:
        return routes[state["answer_status"]]
    except (KeyError, TypeError):
        raise ValueError("missing or unknown answer_status") from None


__all__ = ["verify_final_answer", "route_final_verification"]
