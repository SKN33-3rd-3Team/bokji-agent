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

출력 정형화(구조화 출력) - experiments/model_evaluation/rag_eval.ipynb 실험
결과 반영:
    LLM에게 "자유롭게 다듬어라"고만 시키면 두 가지 문제가 실측으로
    확인됐다 - (1) 정책마다 형식이 들쭉날쭉해서 화면에 안정적으로 못
    올린다, (2) 더 위험하게는 원문 숫자를 자릿수째 부풀려 쓰는(예:
    280,000원 -> 2,800,000원) 환각이 나왔는데 LLM 자신에게 다시 검증을
    시켜도 못 잡아낸 사례가 있었다.

    그래서 이 노드는 LLM에게 숫자·판정 같은 사실 자체를 다시 쓰게 하지
    않는다. LLM 역할은 정책별 안내 문장(summary) 하나를 자연스럽게
    다듬는 것으로 한정하고, 그 문장에 원문(각 정책의 템플릿 섹션)에 없는
    숫자가 하나라도 섞여 있으면 그 정책의 summary는 통째로 버리고 템플릿
    문장만 쓴다(_validate_structured_summaries). 판정/금액/중복수급 같은
    사실 라인은 이 검증과 무관하게 항상 템플릿(규칙 기반)에서만 나온다.

    구조화 출력은 이 프로젝트의 LLMClient.complete()가 response_format을
    지원하지 않아(프롬프트/서빙이 아직 미확정, llm/client.py 참고),
    response_format 강제가 아니라 "JSON으로만 답하라"는 프롬프트 지시 +
    loads_json_object()의 관대한 파싱(코드펜스·잡텍스트 허용)으로
    구현한다 - 이미 N5/N9가 쓰는 것과 같은 방식이다.
"""

from __future__ import annotations

import os
import re
from typing import Any

from rag_design.contracts import RetrievedChunk

from ...llm import LLMCallError, LLMClient, loads_json_object
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

_STRUCTURED_SYSTEM_PROMPT = (
    "너는 복지 정책 안내 문구를 다듬는 보조 도구다. 반드시 JSON 객체 하나로만 "
    "답한다 - 코드펜스나 그 외 설명 문장을 앞뒤에 붙이지 않는다. 주어진 정보에 "
    "있는 사실(제도명, 충족 여부, 금액, 중복수급 여부, 법령명)을 하나도 "
    "바꾸거나 추가하지 않는다. 특히 숫자는 절대 새로 계산하거나 자릿수를 "
    "바꾸지 말고, 필요하면 주어진 문장의 숫자를 그대로 옮겨쓴다. 정보에 없는 "
    "내용은 언급하지 않는다."
)

_NUMBER_PATTERN = re.compile(r"\d[\d,]*")


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


def _numbers_in(text: str) -> set[str]:
    """텍스트에 등장하는 숫자를 콤마 없이 정규화해서 집합으로 뽑는다.

    "2,800,000"과 "2800000"을 같은 숫자로 보기 위해 콤마를 지운다 - 자릿수
    자체가 바뀐 환각(예: 280,000 -> 2,800,000)은 이 정규화 후에도 문자열이
    달라지므로 여전히 잡힌다.
    """

    return {match.replace(",", "") for match in _NUMBER_PATTERN.findall(text)}


def _build_structured_prompt(sections_by_id: dict[str, str]) -> str:
    policy_blocks = "\n\n".join(
        f"policy_id: {policy_id}\n{text}" for policy_id, text in sections_by_id.items()
    )
    return (
        "다음은 정책별로 규칙 기반 검증을 이미 마친 정보다. 각 정책마다 이 "
        "정보만 바탕으로 자연스러운 한국어 안내 문장을 한두 개(summary) "
        "만들어라. 숫자나 사실을 새로 만들거나 바꾸지 말고, 주어진 정보에 "
        "있는 표현만 옮겨써라.\n\n"
        f"{policy_blocks}\n\n"
        "아래 JSON 스키마로만 답하라(다른 텍스트 금지):\n"
        '{"policies": [{"policy_id": "<위 policy_id 그대로>", '
        '"summary": "<안내 문장>"}]}'
    )


def _validate_structured_summaries(
    parsed: dict, sections_by_id: dict[str, str]
) -> dict[str, str]:
    """LLM이 만든 정책별 summary 중 신뢰할 수 있는 것만 걸러서 돌려준다.

    두 가지를 검증한다:
    1. policy_id가 실제 후보(sections_by_id)에 있는가 - 없으면 지어낸
       정책이므로 버린다.
    2. summary에 등장하는 숫자가 그 정책의 원문(템플릿 섹션)에 있는
       숫자로만 이루어져 있는가 - 원문에 없는 숫자가 하나라도 있으면
       (rag_eval.ipynb에서 실측된 자릿수 부풀림 환각 패턴) 그 정책의
       summary는 통째로 버린다. 숫자가 아닌 서술(과장 표현 등)까지는
       걸러내지 못하지만, 가장 위험한 실패(금액 오기재)는 기계적으로
       차단한다.
    """

    raw_policies = parsed.get("policies")
    if not isinstance(raw_policies, list):
        return {}

    validated: dict[str, str] = {}
    for item in raw_policies:
        if not isinstance(item, dict):
            continue
        policy_id = item.get("policy_id")
        summary = item.get("summary")
        if not isinstance(policy_id, str) or not isinstance(summary, str):
            continue
        summary = summary.strip()
        if not summary:
            continue
        section_text = sections_by_id.get(policy_id)
        if section_text is None:
            continue
        extra_numbers = _numbers_in(summary) - _numbers_in(section_text)
        if extra_numbers:
            continue
        validated[policy_id] = summary
    return validated


def _compose_structured_answer(
    sections_by_id: dict[str, str], validated_summaries: dict[str, str]
) -> str:
    parts: list[str] = []
    for policy_id, section_text in sections_by_id.items():
        summary = validated_summaries.get(policy_id)
        parts.append(f"{summary}\n\n{section_text}" if summary else section_text)
    return "\n\n".join(parts)


def generate_answer(state: GraphState, llm_client: LLMClient | None = None) -> dict:
    """state["assembled_result"]를 바탕으로 draft_answer/citations를 채워
    반환한다 (partial state update).

    llm_client: 정책별 안내 문장을 자연스럽게 다듬을 때만 쓰는 선택적 LLM
    클라이언트(``src.rag_chatbot.llm.LLMClient``). 구조화 출력(JSON)을
    요청하고 ``_validate_structured_summaries``로 검증한 뒤, 검증을 통과한
    정책에만 다듬어진 문장을 붙인다. None이거나 호출/파싱/검증이
    실패하면(``LLMCallError``, JSON 파싱 실패, 전부 검증 탈락) 규칙 기반
    템플릿 문장을 그대로 쓴다 - RunPod 엔드포인트가 아직 없어도(2026-08-31
    기준) 이 노드는 끝까지 동작한다.
    """
    assembled = state.get("assembled_result") or {}
    policies = assembled.get("policies", {})
    chunks_by_id = _chunks_by_id(state)

    sections_by_id: dict[str, str] = {}
    citations: list[CitationEntry] = []
    for policy_id, entry in policies.items():
        sections_by_id[policy_id] = _template_section(policy_id, entry)
        citations.extend(_collect_citations(policy_id, state, chunks_by_id))

    template_answer = (
        "\n\n".join(sections_by_id.values())
        if sections_by_id
        else "확인된 복지 제도 정보가 없습니다."
    )

    draft_answer = template_answer
    if llm_client is not None and sections_by_id:
        try:
            response = llm_client.complete(
                _build_structured_prompt(sections_by_id),
                system=_STRUCTURED_SYSTEM_PROMPT,
            )
            parsed = loads_json_object(response)
            validated_summaries = _validate_structured_summaries(parsed, sections_by_id)
        except (LLMCallError, ValueError, TypeError) as exc:
            if os.environ.get("BOKJI_TRACE") == "1":
                print(f"     [N13 DEBUG] {type(exc).__name__}: {exc}", flush=True)
            validated_summaries = {}

        if validated_summaries:
            draft_answer = _compose_structured_answer(sections_by_id, validated_summaries)

    node_trace = list(state.get("node_trace", []))
    node_trace.append("N13")

    return {"draft_answer": draft_answer, "citations": citations, "node_trace": node_trace}


__all__ = ["generate_answer"]
