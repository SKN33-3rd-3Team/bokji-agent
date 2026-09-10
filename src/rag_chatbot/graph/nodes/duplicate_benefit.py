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
from .document_verification import merge_evidence_chunks

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

# ── 조항 성격 분류 ──────────────────────────────────────────────────
# 원천 문서의 "중복" 표현은 한 가지가 아니다. 247개 조항을 전수 분류한 결과:
#   other     164건(66%)  "에너지바우처와 중복지원 불가", "참전명예수당과 중복지급 안됨"
#   household  65건(26%)  "1가구당 중복지원 불가", "기수혜자 중복 수혜 불가"
#   header     18건( 7%)  "※ 중복수혜불가 조건" (조건 내용이 안 적혀 있음)
# 이걸 전부 "중복수급 제한 조항이 있습니다"로 보여주면, 신청 횟수 제한일 뿐인
# household를 사용자가 "다른 제도와 못 받는다"로 읽는다.

# 같은 제도 재신청 / 가구·세대 단위 제한을 가리키는 표현.
_HOUSEHOLD_MARKERS = re.compile(
    r"가구당|세대당|1인\s*1회|1회에\s*한|본인에\s*한|기수혜|이미\s*(지원|수혜)"
    r"|동일\s*(주소지|세대|가구|인)|재신청|중복\s*신청"
)
# 다른 제도를 가리키는 표현. 상대가 문장 안에 등장한다는 신호.
_OTHER_PROGRAM_MARKERS = re.compile(
    r"타\s*(기관|사업|지자체|공공기관|시도|부처|법인|단체)|다른\s*(사업|제도|기관)"
    r"|중앙부처|중앙정부|유사\s*사업|와\s*중복|과\s*중복"
    r"|바우처|수급자|장학금|수당|이용권|지원사업|지원금|보조금|급여|공제|연금"
    # "○ 중복불가서비스 : 유아학비(누리과정) 지원, 영유아보육료 지원" 처럼
    # 콜론 뒤에 상대를 나열하는 형태. 나열되는 이름이 "OOO 지원"이면 위의
    # 어휘 목록에 하나도 안 걸려서 이 패턴이 없으면 통째로 놓친다.
    r"|중복\s*불가\s*서비스"
    r"|중복[가-힣\s]{0,8}(불가|제한|배제)\s*[::]\s*\S"
)
# 조건 내용 없이 제목만 있는 조항("※ 중복수혜불가 조건").
# 길이로만 자르면 "1가구당 중복지원 불가"(12자)처럼 뜻이 분명한 조항까지
# 헤더로 잘못 분류된다. 그래서 **제한 표현 자체를 지우고 남는 게 있는지**로
# 판단한다 - 남는 게 "조건"뿐이면 헤더, "1가구당"이 남으면 헤더가 아니다.
_BULLET_PREFIX = re.compile(r"^[※○◦*\-·ㆍ□■●\s]+")
_HEADER_RESIDUE_WORDS = frozenset({"", "조건", "대상", "항목", "여부", "사항"})

CLAUSE_KIND_OTHER = "other"
CLAUSE_KIND_HOUSEHOLD = "household"
CLAUSE_KIND_HEADER = "header"


def classify_clause(clause: str) -> str:
    """중복 조항 한 문장을 other / household / header 로 분류한다."""

    body = _BULLET_PREFIX.sub("", clause).strip()
    residue = _RESTRICTION_PATTERN.sub(" ", body)
    residue = re.sub(r"[\s:：·,、/()]+", "", residue)
    if residue in _HEADER_RESIDUE_WORDS:
        return CLAUSE_KIND_HEADER
    has_other = bool(_OTHER_PROGRAM_MARKERS.search(clause))
    has_household = bool(_HOUSEHOLD_MARKERS.search(clause))
    if has_household and not has_other:
        return CLAUSE_KIND_HOUSEHOLD
    if has_other:
        return CLAUSE_KIND_OTHER
    # 상대도 가구 표현도 없으면 "다른 제도"라고 단정하지 않는다.
    return CLAUSE_KIND_HOUSEHOLD


def find_named_conflicts(
    clauses: list[str], titles_by_policy_id: dict[str, str], self_policy_id: str
) -> dict[str, str]:
    """조항 문장 안에 **같은 답변에 함께 나온 다른 정책의 제목**이 있으면 찾는다.

    전체 10,968건과 대조하지 않고 이번 답변의 정책들(보통 5개)하고만 맞춰본다.
    이유가 둘이다.

    1. 정확하다. 전체 제목 사전과 대조하면 "보훈명예수당"처럼 지자체마다 같은
       이름을 쓰는 정책이 엉뚱하게 걸린다(실측: 전체 대조 시 관계쌍 177건 중
       133건이 서로 다른 지역이었다). 같은 답변에 뜬 정책끼리는 지역 조건이
       이미 맞춰져 있어서 이 오답이 생기지 않는다.
    2. 싸다. 5x5 문자열 비교라 비용이 사실상 0이다.

    돌려주는 값은 ``{policy_id: 근거 문장}``이다 - 어느 문장 때문에 충돌로
    판정했는지 화면에서 보여줄 수 있어야 한다.
    """

    found: dict[str, str] = {}
    for clause in clauses:
        for policy_id, title in titles_by_policy_id.items():
            if policy_id == self_policy_id or policy_id in found:
                continue
            name = (title or "").strip()
            # 너무 짧은 제목은 우연히 걸린다("지원", "수당" 같은 것).
            if len(name) < 4 or name not in clause:
                continue
            found[policy_id] = clause
    return found


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
    retried_policy_ids: set[str] = set()
    for claim in state.get("claim_plan", []):
        if claim.get("doc_retry_count", 0) > 0:
            retried_policy_ids.add(claim["policy_id"])
        if claim.get("claim_type") != "duplicate":
            continue
        if claim["policy_id"] not in policy_ids_with_verdict:
            continue
        claims_by_policy[claim["policy_id"]].append(claim)

    # N4가 실어 보낸 전체 섹션을 쓴다. 없으면(예전 계약·단위 테스트) 정책마다
    # 재검색하는 기존 경로로 떨어진다.
    full_by_policy: dict[str, list] = defaultdict(list)
    for retrieved in state.get("subsidy_full_chunks") or []:
        full_by_policy[retrieved.chunk.metadata.get("source_id")].append(retrieved)

    titles_by_policy_id = _policy_titles(state)

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
                    "clause_kind": None,
                    "restriction_clauses": [],
                    "household_clauses": [],
                    "condition_note": "중복수급 근거가 없거나 불확실함 (재검색 생략)",
                }
            )
            continue

        recheck_chunks = full_by_policy.get(policy_id) or ()
        # N6 재시도에서 보존된 청크는 전체 문서라는 보장이 없다.
        if not recheck_chunks or policy_id in retried_policy_ids:
            try:
                found = store.search(
                    SourceType.SUBSIDY,
                    f"{policy_id} 중복수급 병급 제한",
                    query_id=f"{state.get('query_id', 'n11')}-{policy_id}-recheck",
                    top_k=_RECHECK_TOP_K,
                    search_filter=VectorSearchFilter(metadata_equals={"source_id": policy_id}),
                )
            except CollectionNotFoundError:
                # 추가 조회가 불가능해도 이미 보존한 근거는 유지한다.
                found = ()
            recheck_chunks = merge_evidence_chunks(list(recheck_chunks), list(found))
        if not recheck_chunks:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "미확인",
                    "conflicts_with": [],
                    "clause_kind": None,
                    "restriction_clauses": [],
                    "household_clauses": [],
                    "condition_note": "재검색에서 해당 정책 근거를 다시 찾지 못함",
                }
            )
            continue

        # metadata 경로(mutually_exclusive_with)는 가장 강한 근거라 먼저 본다.
        # 다만 정부24 원천에 이 필드가 한 건도 없어서 실제로는 거의 안 걸린다
        # (실측 0/10,968). 나중에 계약이 확장될 때를 위해 남겨둔다.
        conflicts = _find_confirmed_conflicts(
            recheck_chunks, eligible_policy_ids - {policy_id}
        )
        if conflicts:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "불가",
                    "conflicts_with": sorted(conflicts),
                    "clause_kind": CLAUSE_KIND_OTHER,
                    "restriction_clauses": [],
                    "household_clauses": [],
                    "condition_note": "재검색한 문서의 상호배타 metadata에 명시된 정책과 충돌",
                }
            )
            continue

        # 원문 조항을 성격별로 나눈다. 셋을 한 덩어리로 보여주면
        # "1가구 1회"가 "다른 제도와 중복 불가"로 읽힌다.
        buckets: dict[str, list[str]] = defaultdict(list)
        for clause in _find_restriction_clauses(recheck_chunks):
            buckets[classify_clause(clause)].append(clause)
        other_clauses = buckets[CLAUSE_KIND_OTHER]
        household_clauses = buckets[CLAUSE_KIND_HOUSEHOLD]
        header_clauses = buckets[CLAUSE_KIND_HEADER]

        # (a) 다른 제도 제한이고, 상대가 이번 답변 안에 실제로 있는 경우.
        named = find_named_conflicts(other_clauses, titles_by_policy_id, policy_id)
        if named:
            names = ", ".join(
                titles_by_policy_id.get(pid, pid) for pid in sorted(named)
            )
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "불가",
                    "conflicts_with": sorted(named),
                    "clause_kind": CLAUSE_KIND_OTHER,
                    "restriction_clauses": sorted(set(named.values())),
                    "household_clauses": household_clauses,
                    "condition_note": (
                        f"{names}와(과) 중복수급이 불가합니다 - 원문: "
                        + " / ".join(sorted(set(named.values())))
                    ),
                }
            )
            continue

        # (a) 다른 제도 제한이지만 상대를 특정하지 못한 경우.
        if other_clauses:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "조건부",
                    "conflicts_with": [],
                    "clause_kind": CLAUSE_KIND_OTHER,
                    "restriction_clauses": other_clauses,
                    "household_clauses": household_clauses,
                    "condition_note": (
                        "다른 제도와 중복수급이 제한되는 조항이 있습니다. 어떤 제도인지는 "
                        "문서에 특정돼 있지 않아 신청 기관에 확인이 필요합니다 - 원문: "
                        + " / ".join(other_clauses)
                    ),
                }
            )
            continue

        # (b) 같은 제도 재신청·가구 단위 제한. 중복수급 판정이 아니다.
        if household_clauses:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "미확인",
                    "conflicts_with": [],
                    "clause_kind": CLAUSE_KIND_HOUSEHOLD,
                    "restriction_clauses": [],
                    "household_clauses": household_clauses,
                    "condition_note": (
                        "다른 제도와의 중복 제한은 문서에서 찾지 못했습니다. 다만 신청 "
                        "횟수·가구 단위 제한이 있습니다 - 원문: "
                        + " / ".join(household_clauses)
                    ),
                }
            )
            continue

        # (c) "※ 중복수혜불가 조건"처럼 제목만 있고 내용이 없는 경우.
        if header_clauses:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "status": "미확인",
                    "conflicts_with": [],
                    "clause_kind": CLAUSE_KIND_HEADER,
                    "restriction_clauses": [],
                    "household_clauses": [],
                    "condition_note": (
                        "문서에 중복수혜 제한 항목이 표시돼 있으나 구체적인 조건이 "
                        "적혀 있지 않습니다. 공식 문서에서 직접 확인해 주세요."
                    ),
                }
            )
            continue

        # 조항이 없다고 "가능"이라고 하지 않는다. 안 적혀 있는 것과 허용되는
        # 것은 다르다(원천 데이터의 98%에는 애초에 언급이 없다).
        verdicts.append(
            {
                "policy_id": policy_id,
                "status": "미확인",
                "conflicts_with": [],
                "clause_kind": None,
                "restriction_clauses": [],
                "household_clauses": [],
                "condition_note": (
                    "이 제도 문서에서 중복수급 제한 조항을 찾지 못했습니다. "
                    "다만 문서에 적혀 있지 않을 뿐일 수 있어 '중복 가능'으로 "
                    "단정하지 않습니다 - 신청 기관에 확인하세요."
                ),
            }
        )

    return {"duplicate_verdicts": verdicts}


def _policy_titles(state: GraphState) -> dict[str, str]:
    """이번 답변에 나온 정책들의 ``{policy_id: 제목}``.

    제목은 chunk 본문 첫 줄이다(chunking.py가 ``f"{제목}\n지역: ...\n{heading}"``
    형태로 prefix를 붙인다). 전체 섹션이 있으면 그걸, 없으면 N4가 고른 대표
    청크를 쓴다.
    """

    titles: dict[str, str] = {}
    for field in ("subsidy_full_chunks", "subsidy_chunks"):
        for retrieved in state.get(field) or []:
            policy_id = retrieved.chunk.metadata.get("source_id")
            if not policy_id or policy_id in titles:
                continue
            first_line = (retrieved.chunk.text or "").split("\n", 1)[0].strip()
            if first_line:
                titles[policy_id] = first_line
    return titles


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
