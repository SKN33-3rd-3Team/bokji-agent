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

판정 경로는 두 가지다 (2026-08-31 확장).

1. **metadata 경로 -> "불가"**: chunk metadata의 ``mutually_exclusive_with``에
   이미 충족 판정을 받은 다른 정책 id가 실제로 들어 있을 때만. 가장 강한 근거
   이지만, 정부24 원천 데이터에 이 필드가 없어서 현실에서는 거의 안 걸린다.
2. **원문 조항 경로 -> "조건부"**: 재검색한 chunk 본문에 중복수급 제한
   조항이 있으면 그 문장을 근거로 인용해 "조건부"로 판정한다.

2번을 추가한 이유: 원천 데이터를 실제로 세어보니(2026-08-31, 정부24 문서
10,963건 / 44,903섹션) 중복 관련 표현이 **447개 섹션(1.0%)**에 실재한다 -
지원대상 188, 지원내용 229, 선정기준 30. 예: "유치원 이용시간에
아이돌봄서비스 등과 중복지원 불가". metadata 필드가 없다는 이유로 이걸 전부
버리고 "판정 불가"만 돌려주는 건 있는 근거를 안 쓰는 것이었다.

**"조건부"까지만 하고 "불가"로 올리지 않는 이유**: 조항 문장은 대부분
특정 제도명을 명시하지 않거나("중복수급에 해당되는 경우"), 명시해도 그
제도의 policy_id를 알 수 없다. 그래서 "이 사용자의 다른 정책과 실제로
충돌하는가"는 판정할 수 없다. 할 수 있는 말은 "이 제도에는 중복 제한
조항이 있으니 확인이 필요하다"까지이고, 그 이상은 추측이다.

조항이 **없을 때도 "가능"이라고 하지 않는다.** 조항이 안 적혀 있는 것과
중복이 허용되는 것은 다르다(원천 데이터의 99%에는 애초에 언급이 없다).
"미확인"으로 두되, 근거를 못 찾은 것인지 조항이 없었던 것인지는 구분해서
사유에 남긴다.

여전히 못 하는 것: 정책 A와 B의 상호배타 관계를 자동으로 판정하는 것.
원천 데이터에 그 관계를 표현하는 필드가 생겨야 가능하다.

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

import re
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

# N9/N10보다 크게 잡는다. 중복 제한 조항은 지원대상·선정기준·지원내용 중
# 어디에 있을지 모르는데, top_k가 작으면 조항이 있는 섹션을 아예 못 가져와서
# "조항 없음"으로 잘못 결론 내린다. 정책 하나의 섹션 수(보통 4~7개)를 덮는다.
_RECHECK_TOP_K = 8

# 중복수급 제한을 뜻하는 표현. 원천 데이터에서 실제로 쓰인 표기를 모았다.
# "중복"만으로는 "중복 지원 가능"까지 걸리므로 제한 의미가 분명한 형태만 본다.
_RESTRICTION_PATTERN = re.compile(
    r"(중복\s*(지원|수급|수혜|지급|신청)?\s*(불가|제한|불허|배제|안\s*됨|불가능)"
    r"|중복하여\s*(지원|수급|받을)"
    r"|중복수급에\s*해당"
    r"|중복\s*수혜\s*불가"
    r"|병급\s*(불가|제한|조정)"
    r"|동시에\s*(지원|수급)\s*(받을\s*수\s*없|불가)"
    # "타 사업과 중복"만으로는 부족하다. "다른 사업과 중복 지원 가능합니다"
    # 까지 제한으로 읽어버려 정반대 판정이 된다(테스트로 잡은 오탐).
    r"|(타|다른)\s*사업과\s*중복\s*(지원|수급)?\s*(불가|제한|불허|배제))"
)
# 근거로 인용할 문장의 최대 길이. 너무 길면 화면에서 읽히지 않는다.
_CLAUSE_MAX_CHARS = 200
# 한 정책에서 인용할 조항 수 상한.
_MAX_CLAUSES = 3


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
                search_filter=VectorSearchFilter(metadata_equals={"source_id": policy_id}),
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

        # metadata 경로에서 못 걸렀으면 원문 조항을 본다.
        clauses = _find_restriction_clauses(recheck_chunks)
        if clauses:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "조건부",
                    "conflicts_with": [],
                    "condition_note": (
                        "이 제도에 중복수급 제한 조항이 있습니다. 해당되는지 "
                        "확인이 필요합니다 - 원문: " + " / ".join(clauses)
                    ),
                }
            )
            continue

        # 조항이 없다고 "가능"이라고 하지 않는다. 안 적혀 있는 것과 허용되는
        # 것은 다르다(원천 데이터의 99%에는 애초에 언급이 없다).
        verdicts.append(
            {
                "policy_id": policy_id,
                "status": "미확인",
                "conflicts_with": [],
                "condition_note": (
                    "이 제도 문서에서 중복수급 제한 조항을 찾지 못했습니다. "
                    "다만 문서에 적혀 있지 않을 뿐일 수 있어 '중복 가능'으로 "
                    "단정하지 않습니다 - 신청 기관에 확인하세요."
                ),
            }
        )

    return {"duplicate_verdicts": verdicts}


def _find_restriction_clauses(recheck_chunks) -> list[str]:
    """재검색한 chunk 본문에서 중복수급 제한 조항 문장을 뽑는다.

    조항이 있다는 사실과 그 원문을 그대로 전달하는 것까지만 한다 - 그 조항이
    이 사용자의 다른 정책과 실제로 충돌하는지는 판단하지 않는다. 조항 문장이
    대부분 특정 제도명을 명시하지 않거나, 명시해도 그 제도의 policy_id를 알
    수 없기 때문이다. 여기서 더 나가면 추측이 된다.
    """

    clauses: list[str] = []
    for retrieved in recheck_chunks:
        text = retrieved.chunk.text or ""
        for match in _RESTRICTION_PATTERN.finditer(text):
            clause = _surrounding_sentence(text, match.start(), match.end())
            if clause and clause not in clauses:
                clauses.append(clause)
            if len(clauses) >= _MAX_CLAUSES:
                return clauses
    return clauses


def _surrounding_sentence(text: str, start: int, end: int) -> str:
    """조항이 걸린 지점을 포함하는 문장을 잘라낸다.

    원문 그대로를 인용해야 N6/N14의 "근거가 원문에 실제로 있는지" 검증과
    어긋나지 않고, 사용자도 출처를 확인할 수 있다.
    """

    # 문장 경계로 쓸 만한 구분자. 정부24 원문은 마침표 없이 "○"/"-"로 항목을
    # 나누는 경우가 많아 함께 본다.
    boundaries = ("\n", "○", "•", "※", ". ", "]")
    left = max(
        (text.rfind(marker, 0, start) + len(marker) for marker in boundaries),
        default=0,
    )
    right_candidates = [
        position
        for position in (text.find(marker, end) for marker in boundaries)
        if position != -1
    ]
    right = min(right_candidates) if right_candidates else len(text)
    clause = " ".join(text[left:right].split()).strip(" -·")
    if len(clause) > _CLAUSE_MAX_CHARS:
        clause = clause[:_CLAUSE_MAX_CHARS].rstrip() + "..."
    return clause


def _find_confirmed_conflicts(recheck_chunks, other_eligible_policy_ids: set) -> set:
    """재검색한 chunk metadata의 mutually_exclusive_with 필드에, 이미 충족
    판정을 받은 다른 정책 id가 실제로 들어있는 경우만 충돌로 인정한다.
    """
    conflicts: set = set()
    for retrieved in recheck_chunks:
        excluded = retrieved.chunk.metadata.get("mutually_exclusive_with") or ()
        conflicts |= set(excluded) & other_eligible_policy_ids
    return conflicts
