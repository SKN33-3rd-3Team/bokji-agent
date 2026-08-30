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

미해결 사항 (TODO, 팀 확인 필요):
xlsx Metadata 시트의 calculation_rule 필드("신규 - LLM 추출 결과 캐싱 여부
결정 필요")가 아직 chunk에 없다. 그래서 이 구현은 chunk metadata에 이미
숫자로 들어있는 값(amount/benefit_amount 키)만 그대로 쓰고, 자연어 문장에서
금액을 추출하는 로직은 넣지 않았다 (추측 금지). 그런 chunk가 없다면 이
노드는 항상 amount=None을 반환한다 - N10 프롬프트(규칙 추출용 LLM 호출)가
먼저 연결돼야 실제 계산이 가능해진다.
"""

from __future__ import annotations

from collections import defaultdict

from rag_design.contracts import EvidenceStatus, SourceType
from rag_design.vector_store import ChromaVectorStore, VectorSearchFilter

from ..state import BenefitAmount, ClaimDraft, GraphState

_UNCERTAIN_STATUSES = {
    EvidenceStatus.UNSUPPORTED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.CONFLICT,
}

_RECHECK_TOP_K = 3
_AMOUNT_METADATA_KEYS = ("amount", "benefit_amount")


def calculate_benefit_amount(state: GraphState, store: ChromaVectorStore) -> dict:
    """state["eligibility_verdicts"](충족인 정책만)와 state["claim_plan"]
    (amount claim, 이전 노드가 전달)을 바탕으로 state["benefit_amounts"]를
    채워 반환한다 (partial state update).

    store: N9와 동일하게 재확인용 vectorDB 검색에 쓰는 ChromaVectorStore(또는
    동일한 ``search(...)`` 시그니처를 가진 객체). LangGraph 그래프 조립 시
    ``functools.partial(calculate_benefit_amount, store=store)``로 주입한다.
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
        recheck_chunks = store.search(
            SourceType.SUBSIDY,
            f"{policy_id} 지원금액",
            query_id=f"{state.get('query_id', 'n10')}-{policy_id}-recheck",
            top_k=_RECHECK_TOP_K,
            search_filter=VectorSearchFilter(metadata_equals={"doc_id": policy_id}),
        )
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
            amounts.append(
                {
                    "policy_id": policy_id,
                    "amount": None,
                    "rule_chunk_id": chunk.chunk_id,
                    "calculation_note": (
                        "근거 chunk에 구조화된 금액 필드가 없어 계산 규칙 추출이 "
                        "필요함 (LLM 규칙 추출 단계 미구현)"
                    ),
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
