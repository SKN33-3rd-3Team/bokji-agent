"""N13 답변 생성 노드.

N12(assemble_result)가 만든 state["assembled_result"]와, 그 근거를 추적하기
위한 state["claim_plan"] / state["subsidy_chunks"] / state["law_chunks"]를
바탕으로 사용자에게 보여줄 답변 초안(state["draft_answer"])과 인용 목록
(state["citations"])을 만든다. Issue #25(graph builder 조립)에서 추가했다.

- 이 노드는 새로운 판정을 하지 않는다. N9~N12가 이미 검증한
  assembled_result 구조를 문장으로 옮길 뿐이다. LLM은 문장을 자연스럽게
  다듬는 데만 쓰고, 없거나 실패하면 규칙 기반 템플릿 문장을 그대로 쓴다
  (eligibility_verdict.py의 llm_client 패턴과 동일 - 판정/사실 자체는 LLM
  유무와 무관하게 동일해야 한다).
- 인용(citations)은 LLM 출력에서 뽑지 않는다. state["claim_plan"]의
  evidence_chunk_ids(N7이 이미 검증한 근거)와 state["subsidy_chunks"] /
  state["law_chunks"]의 chunk.metadata["source_url"]만으로 조립한다 - LLM이
  "이 출처를 봤다"고 말해도 그 자체를 근거로 인용을 만들지 않는다
  (document_verification_llm_judge.py의 "후보 밖 chunk_id는 지어낸 것으로
  보고 버린다" 원칙과 같은 이유). N14가 이 인용을 한 번 더 검증한다.
"""

from __future__ import annotations

from typing import Any

from rag_design.contracts import RetrievedChunk

from ...llm import LLMCallError, LLMClient
from ..state import CitationEntry, GraphState

# "지원 가능"은 과대 주장이었다. N9가 실제로 대조하는 조건은 문서 metadata에
# 있는 연령 기준뿐이고, 장애·성별·소득·취업은 비교조차 못 한다. 그래서 라벨을
# "확인된 범위"를 말하는 문구로 바꾸고, 무엇을 확인하지 못했는지는 아래
# _verification_line()이 한 줄로 덧붙인다
# (docs/PROJECT_COMPLIANCE.md - 확인하지 않은 것을 확인한 것처럼 말하지 않는다).
_STATUS_LABELS = {
    "충족": "확인한 조건에서는 결격 없음",
    "미충족": "지원 대상 아님",
    "미확인": "확인 필요",
}

_ANSWER_SYSTEM_PROMPT = (
    "너는 복지 정책 안내 답변을 다듬는 보조 도구다. 주어진 정보에 있는 사실"
    "(제도명, 충족 여부, 금액, 중복수급 여부, 법령명)을 하나도 바꾸거나 "
    "추가하지 않는다. 정보에 없는 내용은 언급하지 않는다."
)


def _resolve_source_url(
    chunk_id: str,
    chunks_by_id: dict[str, RetrievedChunk],
) -> str | None:
    retrieved = chunks_by_id.get(chunk_id)
    if retrieved is None:
        return None
    return retrieved.chunk.metadata.get("source_url")


def _chunks_by_id(state: GraphState) -> dict[str, RetrievedChunk]:
    chunks_by_id: dict[str, RetrievedChunk] = {}
    for retrieved in state.get("subsidy_chunks", []) or []:
        chunks_by_id[retrieved.chunk.chunk_id] = retrieved
    for retrieved in state.get("law_chunks", []) or []:
        chunks_by_id.setdefault(retrieved.chunk.chunk_id, retrieved)
    return chunks_by_id


def _collect_citations(
    policy_id: str, state: GraphState, chunks_by_id: dict[str, RetrievedChunk]
) -> list[CitationEntry]:
    citations: list[CitationEntry] = []
    seen: set[str] = set()
    for claim in state.get("claim_plan", []) or []:
        if claim.get("policy_id") != policy_id:
            continue
        for chunk_id in claim.get("evidence_chunk_ids", []) or []:
            if chunk_id in seen:
                continue
            source_url = _resolve_source_url(chunk_id, chunks_by_id)
            if source_url is None:
                continue
            seen.add(chunk_id)
            citations.append(
                {
                    "policy_id": policy_id,
                    "chunk_id": chunk_id,
                    "source_url": source_url,
                    "label": "근거 문서",
                }
            )
    return citations


def _verification_line(eligibility: dict[str, Any]) -> str | None:
    """이 판정이 무엇을 확인하고 무엇을 확인하지 못했는지 한 줄로 밝힌다.

    N9가 판정에 담아준 ``checked``/``unchecked``를 그대로 쓴다 - 여기서
    추측하지 않는다. 둘 다 비어 있으면(옛 형식의 판정 등) 아무것도 붙이지
    않는다.
    """

    checked = eligibility.get("checked") or []
    unchecked = eligibility.get("unchecked") or []
    if not checked and not unchecked:
        return None
    if checked:
        line = f"확인한 조건: {', '.join(checked)}"
    else:
        line = "확인한 조건: 없음(문서에 대조할 수 있는 구조화 기준이 없었음)"
    if unchecked:
        line += f" / 확인하지 못한 조건: {', '.join(unchecked)}"
    return line


def _template_section(policy_id: str, entry: dict[str, Any]) -> str:
    lines = [f"[{policy_id}]"]

    eligibility = entry.get("eligibility") or {}
    verdict = eligibility.get("verdict", "미확인")
    lines.append(f"- 지원자격: {_STATUS_LABELS.get(verdict, verdict)}")
    verification = _verification_line(eligibility)
    if verification:
        lines.append(f"  {verification}")
    if eligibility.get("reasons"):
        lines.append("  근거: " + " / ".join(eligibility["reasons"]))

    amount = entry.get("benefit_amount")
    if amount and amount.get("amount") is not None:
        lines.append(f"- 지원금액: {amount['amount']}")
    else:
        note = entry.get("status_note") or "지원금액 계산 불가"
        lines.append(f"- 지원금액: {note}")
        for law in entry.get("related_law") or []:
            if law.get("source_url"):
                lines.append(f"  관련 법령: {law.get('law_name')} ({law['source_url']})")

    duplicate = entry.get("duplicate")
    if duplicate:
        lines.append(f"- 중복수급: {duplicate.get('status', '미확인')}")
    else:
        lines.append("- 중복수급: 미확인 (판정 결과 없음)")

    return "\n".join(lines)


def generate_answer(state: GraphState, llm_client: LLMClient | None = None) -> dict:
    """state["assembled_result"]를 바탕으로 draft_answer/citations를 채워
    반환한다 (partial state update).

    llm_client: 템플릿 문장을 자연스럽게 다듬을 때만 쓰는 선택적 LLM
    클라이언트(``src.rag_chatbot.llm.LLMClient``). None이거나 호출이
    실패하면(``LLMCallError``) 규칙 기반 템플릿 문장을 그대로 쓴다 - RunPod
    엔드포인트가 아직 없어도(2026-08-31 기준) 이 노드는 끝까지 동작한다.
    """
    assembled = state.get("assembled_result") or {}
    policies = assembled.get("policies", {})
    chunks_by_id = _chunks_by_id(state)

    sections: list[str] = []
    citations: list[CitationEntry] = []
    for policy_id, entry in policies.items():
        sections.append(_template_section(policy_id, entry))
        citations.extend(_collect_citations(policy_id, state, chunks_by_id))

    template_answer = (
        "\n\n".join(sections) if sections else "확인된 복지 제도 정보가 없습니다."
    )

    draft_answer = template_answer
    if llm_client is not None and sections:
        prompt = (
            "다음은 규칙 기반으로 검증된 복지 제도 안내 정보다. 사실 관계를 "
            "하나도 바꾸거나 추가하지 말고, 사용자에게 보여줄 자연스러운 "
            "한국어 안내문으로 다듬어라.\n\n" + template_answer
        )
        try:
            response = llm_client.complete(prompt, system=_ANSWER_SYSTEM_PROMPT)
            if response.strip():
                draft_answer = response.strip()
        except LLMCallError:
            draft_answer = template_answer

    node_trace = list(state.get("node_trace", []))
    node_trace.append("N13")

    return {"draft_answer": draft_answer, "citations": citations, "node_trace": node_trace}


__all__ = ["generate_answer"]
