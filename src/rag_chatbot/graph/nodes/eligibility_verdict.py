"""N9 자격 판정 노드.

state에서 받은 사용자 정보(slots)와 이전 노드(N4~N7)가 전달한 정책 정보
(claim_plan - N6/N7에서 근거·시행일·충돌·안전까지 검증된 상태)를 바탕으로,
후보 정책별로 사용자가 지원자격에 적합한지 "충족" / "미충족" / "미확인"을 판정한다.

claim_plan은 이미 검증된 근거이지만, 이 노드는 그 근거가 가리키는 정책 문서를
vectorDB에서 한 번 더 검색해 재확인한다 - 재검색으로 얻은 chunk의 구조화
metadata(age_start/age_end 등)를 slots와 직접 대조해서, "근거 문장이 뒷받침된다"는
것과 "이 사용자가 그 조건을 실제로 만족한다"는 것을 구분한다. 새로운 정책을
찾는 게 아니라, 이미 claim_plan이 가리키는 그 정책 문서를 doc_id로 좁혀서
다시 확인하는 것이므로 새로운 근거를 만들어내는 게 아니다.

- 충족: 관련 eligibility claim이 모두 SUPPORTED이고, 재검색한 chunk의 구조화
  조건(age_start/age_end)과 slots 사이에 위반이 없음.
- 미충족: 재검색한 chunk의 구조화 조건과 slots가 명백히 어긋남
  (예: age_start/age_end 범위를 벗어남). 이 경우만 "위반이 확인됐다"고 본다.
- 미확인: 그 외 전부 - claim 근거가 없거나(UNSUPPORTED/PARTIAL/CONFLICT),
  재검색에서 해당 정책 chunk를 다시 찾지 못했거나(컬렉션이 아예 없는 경우 포함),
  슬롯/문서 어느 한쪽에 구조화 조건이 없어 비교 자체가 불가능한 경우.

근거 문장에 명시되지 않은 조건은 절대 판단하지 않고 미확인을 반환한다
(추측 금지).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from rag_design.contracts import EvidenceStatus, RetrievedChunk, SourceType
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
)

from ..state import ClaimDraft, EligibilityVerdict, GraphState

_UNCERTAIN_STATUSES = {
    EvidenceStatus.UNSUPPORTED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.CONFLICT,
}

_RECHECK_TOP_K = 3


def determine_eligibility(state: GraphState, store: ChromaVectorStore) -> dict:
    """state["slots"](사용자 정보)와 state["claim_plan"](이전 노드가 전달한,
    검증 완료된 정책 정보)을 바탕으로 state["eligibility_verdicts"]를 채워
    반환한다 (partial state update).

    store: 재확인용 vectorDB 검색에 쓰는 ChromaVectorStore(또는 동일한
    ``search(...)`` 시그니처를 가진 객체 - 테스트에서는 대체 구현을 넣을 수
    있다). LangGraph 그래프 조립 시 이 store 인스턴스는 체크포인트에 저장되는
    state에 넣지 않고, ``functools.partial(determine_eligibility, store=store)``
    형태로 노드 함수에 주입한다.
    """
    slots = state.get("slots", {})
    claims_by_policy: dict[str, list[ClaimDraft]] = defaultdict(list)
    for claim in state.get("claim_plan", []):
        if claim.get("claim_type") != "eligibility":
            continue
        claims_by_policy[claim["policy_id"]].append(claim)

    verdicts: list[EligibilityVerdict] = []
    for policy_id, claims in claims_by_policy.items():
        relevant = [
            claim
            for claim in claims
            if EvidenceStatus(claim["status"]) is not EvidenceStatus.NOT_APPLICABLE
        ]
        if not relevant:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "verdict": "미확인",
                    "reasons": ["판정 가능한 자격 조건 근거가 없음"],
                }
            )
            continue

        statuses = {EvidenceStatus(claim["status"]) for claim in relevant}

        if statuses & _UNCERTAIN_STATUSES:
            reasons = [
                reason
                for claim in relevant
                if EvidenceStatus(claim["status"]) in _UNCERTAIN_STATUSES
                for reason in claim.get("reasons", [])
            ]
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "verdict": "미확인",
                    "reasons": reasons or ["근거가 불충분하거나 상충함"],
                }
            )
            continue

        # 여기까지 왔으면 관련 claim이 전부 SUPPORTED. 같은 정책 문서를
        # vectorDB에서 한 번 더 검색해 구조화 자격 조건을 재확인한다.
        try:
            recheck_chunks = store.search(
                SourceType.SUBSIDY,
                f"{policy_id} 지원자격",
                query_id=f"{state.get('query_id', 'n9')}-{policy_id}-recheck",
                top_k=_RECHECK_TOP_K,
                search_filter=VectorSearchFilter(metadata_equals={"doc_id": policy_id}),
            )
        except CollectionNotFoundError:
            # 아직 어떤 정책도 색인되지 않은 상태 (컬렉션 자체가 없음) - 근거를
            # 찾지 못한 것과 동일하게 취급한다. 여기서 예외를 그대로 흘려보내면
            # 그래프 전체가 죽는다.
            recheck_chunks = ()
        if not recheck_chunks:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "verdict": "미확인",
                    "reasons": ["재검색에서 해당 정책 근거를 다시 찾지 못함"],
                }
            )
            continue

        violations = _find_structured_violations(recheck_chunks, slots)
        if violations:
            verdicts.append(
                {"policy_id": policy_id, "verdict": "미충족", "reasons": violations}
            )
            continue

        reasons = [reason for claim in relevant for reason in claim.get("reasons", [])]
        verdicts.append({"policy_id": policy_id, "verdict": "충족", "reasons": reasons})

    return {"eligibility_verdicts": verdicts}


def _find_structured_violations(
    chunks: Iterable[RetrievedChunk], slots: dict
) -> list[str]:
    """age_start/age_end 등 구조화 metadata와 사용자 slots를 대조해, 문서에
    명시적으로 적힌 조건과 명백히 어긋나는 경우만 위반으로 판단한다. 슬롯이나
    metadata 어느 한쪽이 없으면 비교하지 않는다 (애매한 경우 위반으로 단정하지
    않음 - 그런 경우는 호출부에서 이미 "충족"으로 이어지므로, 이후 미확인
    처리가 필요하면 이 함수가 아니라 상위 판정 규칙을 조정한다).
    """
    age = slots.get("age")
    violations: list[str] = []
    if age is None:
        return violations
    for retrieved in chunks:
        metadata = retrieved.chunk.metadata
        age_start = metadata.get("age_start")
        age_end = metadata.get("age_end")
        if age_start is not None and age < age_start:
            violations.append(
                f"연령 조건 미충족: 최소 {age_start}세부터 지원 (사용자 age={age})"
            )
        if age_end is not None and age > age_end:
            violations.append(
                f"연령 조건 미충족: 최대 {age_end}세까지 지원 (사용자 age={age})"
            )
    return violations
