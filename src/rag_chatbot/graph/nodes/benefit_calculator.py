"""N10 지원금 계산 노드.

xlsx 설계표(노드_Agent/프롬프트 시트) 기준: N9가 "충족"으로 판정한 정책의
amount claim과 이전 노드가 전달한 claim_plan을 바탕으로 지원금을 계산한다.
LLM은 근거 chunk에서 "계산 규칙"만 추출하고, 실제 산술은 코드가 결정론적으로
수행한다 (LLM이 직접 금액을 계산하지 않는다 - 환각 방지).

N9와 동일하게, amount claim이 가리키는 정책 문서를 vectorDB에서 한 번 더
검색해 재확인한다 - claim_plan의 문자열 근거만 그대로 믿지 않고, 그 근거가
가리키는 chunk를 doc_id로 좁혀 다시 조회한다.

- 정책 간 금액을 임의로 합산하지 않는다. 정책별 금액은 항상 분리해서 유지한다.
- 계산에 사용한 근거 chunk id(rule_chunk_id)와 계산 방식 요약(calculation_note)을
  같이 남겨 N14 최종 검증에서 추적 가능하게 한다.
- 규칙이 모호하거나 조건부(예: 소득 구간별 차등)인 경우 amount=None으로 두고
  calculation_note에 사유를 남긴다. 임의로 대표값을 만들지 않는다.

- 재검색 시 doc_id뿐 아니라 section_type="support_details"(지원내용 섹션,
  스펙에서 말하는 SUBSIDY DETAIL에 해당)까지 좁혀서 검색한다. 이 정책 문서에
  지원내용 섹션 자체가 없으면(= "지원금 제도가 포함되지 않은 경우"에 가까움)
  검색 결과가 아예 없게 되어 자연스럽게 amount=None으로 떨어진다 - 별도의
  "이게 지원금 제도인지" 판단 로직을 추측으로 만들지 않고, 문서 구조 자체로
  게이팅한다.

LLM 규칙 추출 (2026-08-31 기준 DRAFT - 프롬프트/모델 미확정. 팀에서
skt/A.X-4.0-Light, Qwen/Qwen3.5-9B, Bllossom/llama-3.2-Korean-Bllossom-3B
세 모델 비교 중, RunPod Serverless로 서빙 예정): chunk metadata에 이미
구조화된 amount/benefit_amount 필드가 없으면, llm_client가 주어졌을 때만
그 chunk의 원문(지원내용 섹션 텍스트)을 LLM에 보내 "원문에 명시된 금액"만
JSON으로 추출하게 한다 - LLM은 절대 계산하거나 추측하지 않고, 원문에 없으면
null을 내도록 프롬프트에서 강제한다. llm_client가 없거나 호출/파싱이
실패하면 amount=None + 사유를 그대로 남긴다 (추측 금지 원칙 유지).

미해결 사항 (TODO, 팀 확인 필요):
- xlsx Metadata 시트의 calculation_rule 필드("신규 - LLM 추출 결과 캐싱 여부
  결정 필요")가 아직 chunk에 없다.
- "LLM은 규칙만 추출, 코드가 결정론적 산술 수행"이라는 원래 설계의 산술
  단계(예: "가구원수 x 단가"처럼 곱셈이 필요한 규칙)는 아직 안 만들었다 -
  지금은 원문에 이미 명시된 단일 금액만 그대로 쓰고, 계산식이 필요한
  규칙은 amount=None으로 남긴다. 실제 규칙 스키마가 정해지면 이 부분에
  산술 로직을 추가해야 한다.
- 정부24 원천 데이터 어디에도 실제 지원 금액 숫자 필드가 없는 것으로
  확인됨 - LLM 추출 대상이 될 원문은 지원내용 섹션의 자연어 문장뿐이라,
  금액이 아예 존재하지 않는 서비스형 정책과 진짜 계산 실패를 구분하는
  문제가 남아있음.
"""

from __future__ import annotations

import json
from collections import defaultdict

from rag_design.contracts import EvidenceStatus, SourceType
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
)

from ...llm import LLMCallError, LLMClient
from ..state import BenefitAmount, ClaimDraft, GraphState

_UNCERTAIN_STATUSES = {
    EvidenceStatus.UNSUPPORTED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.CONFLICT,
}

_RECHECK_TOP_K = 3
_AMOUNT_METADATA_KEYS = ("amount", "benefit_amount")


def calculate_benefit_amount(
    state: GraphState, store: ChromaVectorStore, llm_client: LLMClient | None = None
) -> dict:
    """state["eligibility_verdicts"](충족인 정책만)와 state["claim_plan"]
    (amount claim, 이전 노드가 전달)을 바탕으로 state["benefit_amounts"]를
    채워 반환한다 (partial state update).

    store: N9와 동일하게 재확인용 vectorDB 검색에 쓰는 ChromaVectorStore(또는
    동일한 ``search(...)`` 시그니처를 가진 객체). LangGraph 그래프 조립 시
    ``functools.partial(calculate_benefit_amount, store=store)``로 주입한다.

    llm_client: chunk에 구조화 금액 필드가 없을 때만 쓰는 선택적 LLM 클라이언트
    (``src.rag_chatbot.llm.LLMClient``). None이면(기본값) LLM을 호출하지 않고
    바로 amount=None으로 남긴다.
    """
    eligible_policy_ids = {
        verdict["policy_id"]
        for verdict in state.get("eligibility_verdicts", [])
        if verdict.get("verdict") == "충족"
    }

    claims_by_policy: dict[str, list[ClaimDraft]] = defaultdict(list)
    for claim in state.get("claim_plan", []):
        if claim.get("claim_type") != "amount":
            continue
        if claim["policy_id"] not in eligible_policy_ids:
            continue
        claims_by_policy[claim["policy_id"]].append(claim)

    amounts: list[BenefitAmount] = []
    for policy_id, claims in claims_by_policy.items():
        relevant = [
            claim
            for claim in claims
            if EvidenceStatus(claim["status"]) is not EvidenceStatus.NOT_APPLICABLE
        ]
        if not relevant or {EvidenceStatus(c["status"]) for c in relevant} & _UNCERTAIN_STATUSES:
            amounts.append(
                {
                    "policy_id": policy_id,
                    "amount": None,
                    "rule_chunk_id": "",
                    "calculation_note": "지원금 근거가 없거나 불확실함 (재검색 생략)",
                }
            )
            continue

        # vectorDB 재검색: claim_plan의 근거를 그대로 믿지 않고 같은 정책
        # 문서를 doc_id로 좁혀 다시 조회해 재확인한다.
        try:
            recheck_chunks = store.search(
                SourceType.SUBSIDY,
                f"{policy_id} 지원금액",
                query_id=f"{state.get('query_id', 'n10')}-{policy_id}-recheck",
                top_k=_RECHECK_TOP_K,
                search_filter=VectorSearchFilter(
                    metadata_equals={"doc_id": policy_id, "section_type": "support_details"}
                ),
            )
        except CollectionNotFoundError:
            # 아직 정책이 하나도 색인되지 않은 상태 - 근거를 못 찾은 것과 동일하게
            # 취급한다 (여기서 예외를 흘려보내면 그래프 전체가 죽는다).
            recheck_chunks = ()
        if not recheck_chunks:
            amounts.append(
                {
                    "policy_id": policy_id,
                    "amount": None,
                    "rule_chunk_id": "",
                    "calculation_note": "재검색에서 해당 정책 근거를 다시 찾지 못함",
                }
            )
            continue

        chunk = recheck_chunks[0].chunk
        structured_amount = next(
            (chunk.metadata[key] for key in _AMOUNT_METADATA_KEYS if key in chunk.metadata),
            None,
        )
        if structured_amount is None:
            llm_amount, note = _extract_amount_via_llm(chunk.text, llm_client)
            amounts.append(
                {
                    "policy_id": policy_id,
                    "amount": llm_amount,
                    "rule_chunk_id": chunk.chunk_id,
                    "calculation_note": note,
                }
            )
            continue

        amounts.append(
            {
                "policy_id": policy_id,
                "amount": float(structured_amount),
                "rule_chunk_id": chunk.chunk_id,
                "calculation_note": "재검색한 chunk metadata의 구조화 금액 필드를 그대로 사용",
            }
        )

    return {"benefit_amounts": amounts}


def _extract_amount_via_llm(
    chunk_text: str, llm_client: LLMClient | None
) -> tuple[float | None, str]:
    """chunk 원문에서 LLM으로 "이미 명시된 금액"만 뽑아낸다 (계산/추측 금지).

    DRAFT(팀 확인 필요, 확정 전): 프롬프트/출력 스키마가 아직 설계 중이라
    아래는 임시다. LLM이 계산식이 필요한 규칙(예: 소득 구간별 차등)까지
    만나면 amount를 null로 두도록 프롬프트에서 강제한다 - 산술 로직은 아직
    이 함수에 없다(위 모듈 docstring의 미해결 사항 참고).
    """
    if llm_client is None:
        return None, (
            "근거 chunk에 구조화된 금액 필드가 없어 계산 규칙 추출이 필요함 "
            "(LLM 미연결 - RUNPOD_* 환경변수 설정 또는 llm_client 인자 필요)"
        )

    prompt = (
        "다음은 복지 정책의 지원내용 원문이다. 사용자가 받을 수 있는 지원 "
        "금액을 구조화된 JSON으로만 추출하라. 절대 새로운 숫자를 계산하거나 "
        "추측하지 마라 - 원문에 명시된 확정 금액이 없으면(예: 소득 구간별로 "
        "달라지는 경우, 금액이 아예 언급되지 않는 경우) amount를 null로 둬라.\n\n"
        '출력 형식(다른 텍스트 없이 이 JSON 하나만): '
        '{"amount": <숫자 또는 null>, "reason": "<한 줄 설명>"}\n\n'
        f"원문:\n{chunk_text}"
    )
    try:
        response = llm_client.complete(
            prompt,
            system="너는 복지 정책 원문에서 금액만 추출하는 도구다. 절대 계산하거나 추측하지 않는다.",
        )
    except LLMCallError as exc:
        return None, f"LLM 규칙 추출 호출 실패: {exc}"

    try:
        parsed = json.loads(response)
        amount = parsed.get("amount")
        reason = parsed.get("reason", "")
    except (ValueError, AttributeError, TypeError):
        return None, f"LLM 응답을 JSON으로 파싱하지 못함 (추측 금지, 원본 미신뢰): {response[:200]!r}"

    if amount is None:
        return None, reason or "LLM이 원문에서 확정 금액을 추출하지 못함(조건부이거나 명시 안 됨)"
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return None, f"LLM이 숫자가 아닌 amount를 반환함 (신뢰하지 않음): {amount!r}"

    note = "LLM이 원문에서 추출한 금액"
    if reason:
        note += f" (근거: {reason})"
    return float(amount), note
