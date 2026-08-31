"""N11 중복수급 판정 노드.

xlsx 설계표 기준: eligibility_verdicts와 이전 노드가 전달한 claim_plan
(duplicate claim)을 바탕으로 정책별 중복수급 여부를 "가능"/"불가"/"조건부"/
"미확인" 4단계로 판정한다.

N9/N10과 동일하게, 판정 전 그 정책 문서를 vectorDB에서 한 번 더 검색해
재확인한다 - claim_plan의 문자열 근거만 그대로 믿지 않는다.

- 정책 metadata에 상호배타 관계(mutually_exclusive_with)를 직접 표현하는
  필드는 아직 없다 (xlsx 결정사항 시트: "미정, Gate1 계약 확장 필요"). 그
  전까지는 재검색한 chunk의 metadata에 그 필드가 실제로 존재하고, 사용자가
  이미 충족 판정을 받은 다른 정책과 겹칠 때만 "불가"로 판정한다.
- 명시적 조항(metadata)이 없으면 기본값은 "미확인"이다 - "가능"을 임의로
  단정하지 않는다 (원래 stub 설계 결정 유지).
- status 값: "가능" / "불가" / "조건부" / "미확인".

미해결 사항 (TODO, 팀 확인 필요):
mutually_exclusive_with 메타데이터 스키마 확장(Gate 1)이 아직 없어서, 이
구현은 "불가"를 낼 수 있는 유일한 경로가 그 필드가 우연히 이미 채워져
있는 경우뿐이다. 스키마가 생기기 전까지는 사실상 항상 "미확인"을 반환한다.
"조건부" 판정은 자연어 조항 해석(LLM)이 필요해 아직 만들지 않았다.

Issue #11 스펙 재검토(2026-08-31) 메모: 원래 스펙 문구는 "검색된 복지제도와
중복 지원 불가능한 지원제도를 검색하여 LIST로 추가"로, 이 정책 자기 자신의
metadata를 읽는 게 아니라 다른 정책들을 능동적으로 검색해 충돌 목록을
찾아내는 걸 의도한 것으로 보인다. 지금 구현은 그렇게까지는 안 하고, 이미
eligible_policy_ids로 알고 있는 정책들과의 교집합만 본다 - 정부24 원천
데이터에 mutually_exclusive_with에 대응하는 실제 필드가 없어서(N9의
age_start/age_end처럼 확인된 원천 필드가 없음), 능동 검색으로 바꾼다 해도
검색할 대상 자체가 없다. 원천 데이터에 이 관계를 표현하는 필드가 생기기
전까지는 구조를 크게 바꾸는 대신 이 결정사항을 명시적으로 남겨둔다.
"""

from __future__ import annotations

from collections import defaultdict

from rag_design.contracts import EvidenceStatus, SourceType
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
)

from ..state import ClaimDraft, DuplicateVerdict, GraphState

_UNCERTAIN_STATUSES = {
    EvidenceStatus.UNSUPPORTED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.CONFLICT,
}

_RECHECK_TOP_K = 3


def check_duplicate_benefit(state: GraphState, store: ChromaVectorStore) -> dict:
    """state["eligibility_verdicts"]와 state["claim_plan"](duplicate claim,
    이전 노드가 전달)을 바탕으로 state["duplicate_verdicts"]를 채워 반환한다
    (partial state update).

    store: N9/N10과 동일하게 재확인용 vectorDB 검색에 쓰는 ChromaVectorStore
    (또는 동일한 ``search(...)`` 시그니처를 가진 객체).
    """
    # E17 기준: N11은 eligibility_verdicts 전체(충족/미충족/미확인 모두)를
    # 입력으로 받는다 - N10과 달리 "충족"만으로 걸러내지 않는다.
    policy_ids_with_verdict = {
        verdict["policy_id"] for verdict in state.get("eligibility_verdicts", [])
    }
    eligible_policy_ids = {
        verdict["policy_id"]
        for verdict in state.get("eligibility_verdicts", [])
        if verdict.get("verdict") == "충족"
    }

    claims_by_policy: dict[str, list[ClaimDraft]] = defaultdict(list)
    for claim in state.get("claim_plan", []):
        if claim.get("claim_type") != "duplicate":
            continue
        if claim["policy_id"] not in policy_ids_with_verdict:
            continue
        claims_by_policy[claim["policy_id"]].append(claim)

    verdicts: list[DuplicateVerdict] = []
    for policy_id, claims in claims_by_policy.items():
        relevant = [
            claim
            for claim in claims
            if EvidenceStatus(claim["status"]) is not EvidenceStatus.NOT_APPLICABLE
        ]
        if not relevant or {EvidenceStatus(c["status"]) for c in relevant} & _UNCERTAIN_STATUSES:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "미확인",
                    "conflicts_with": [],
                    "condition_note": "중복수급 근거가 없거나 불확실함 (재검색 생략)",
                }
            )
            continue

        try:
            recheck_chunks = store.search(
                SourceType.SUBSIDY,
                f"{policy_id} 중복수급 병급 제한",
                query_id=f"{state.get('query_id', 'n11')}-{policy_id}-recheck",
                top_k=_RECHECK_TOP_K,
                search_filter=VectorSearchFilter(metadata_equals={"doc_id": policy_id}),
            )
        except CollectionNotFoundError:
            # 아직 정책이 하나도 색인되지 않은 상태 - 근거를 못 찾은 것과 동일하게
            # 취급한다 (여기서 예외를 흘려보내면 그래프 전체가 죽는다).
            recheck_chunks = ()
        if not recheck_chunks:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "미확인",
                    "conflicts_with": [],
                    "condition_note": "재검색에서 해당 정책 근거를 다시 찾지 못함",
                }
            )
            continue

        conflicts = _find_confirmed_conflicts(
            recheck_chunks, eligible_policy_ids - {policy_id}
        )
        if conflicts:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "불가",
                    "conflicts_with": sorted(conflicts),
                    "condition_note": "재검색한 문서의 상호배타 metadata에 명시된 정책과 충돌",
                }
            )
            continue

        # metadata에 상호배타 필드 자체가 없으면 "가능"을 임의로 단정하지 않는다.
        verdicts.append(
            {
                "policy_id": policy_id,
                "status": "미확인",
                "conflicts_with": [],
                "condition_note": (
                    "상호배타 조항 metadata(mutually_exclusive_with)가 아직 없어 판정 불가"
                ),
            }
        )

    return {"duplicate_verdicts": verdicts}


def _find_confirmed_conflicts(recheck_chunks, other_eligible_policy_ids: set) -> set:
    """재검색한 chunk metadata의 mutually_exclusive_with 필드에, 이미 충족
    판정을 받은 다른 정책 id가 실제로 들어있는 경우만 충돌로 인정한다.
    """
    conflicts: set = set()
    for retrieved in recheck_chunks:
        excluded = retrieved.chunk.metadata.get("mutually_exclusive_with") or ()
        conflicts |= set(excluded) & other_eligible_policy_ids
    return conflicts
