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
  단계 중 "가구원수 x 단가"류는 compute_total()이 이미 처리한다(원문에
  "1인당"과 가구원수 슬롯이 모두 있을 때만). 2026-09-08 추가, 2026-09-11
  확장, 2026-09-12 gender/age 추가: 소득구간/취업상태/장애여부/혼인상태/
  임신출산상태/성별(열거형) + 자녀수/가구원수/만나이(정수 구간) + 자유
  선택형 옵션(예: 자연분만/제왕절개)에
  따라 금액이 달라지는 조건부/구간별 규칙은 _extract_tiered_rule_via_llm()이
  규칙만 추출하고 _select_tier_amount()가 사용자 슬롯/선택 답변과
  결정론적으로 대조해 고른다. 규칙이 가리키는 값을 아직 모르면
  needs_more_info=True로 표시해 새 노드(N10a, request_calc_info)가
  사용자에게 되묻게 한다 - 슬롯 하나가 필요하면 그 슬롯 이름을
  (state["calc_missing_slots"]), 선택형 옵션이 필요하면 정책별 라벨
  목록을(state["calc_missing_choices"]) 각각 되묻는다. 차량가액·소득
  실수령액처럼 원문에 없는 외부 값에 비율(%)을 곱해야 계산되는 규칙은
  여전히 다루지 않는다 - 그 외부 값은 슬롯도 원문도 아니라 추측 없이는
  알 수 없어, LLM이 조건부 규칙으로 인식하지 않고 variable=null로 두게
  프롬프트에서 명시적으로 막는다. 이 경우 기존 단일 금액 경로
  (_resolve_amount_without_metadata)로 넘어가고, 대개 단일 금액을
  확정하지 못해 amount=None("확인 불가")으로 남는다.
- 정부24 원천 데이터 어디에도 실제 지원 금액 숫자 필드가 없는 것으로
  확인됨 - LLM 추출 대상이 될 원문은 지원내용 섹션의 자연어 문장뿐이라,
  금액이 아예 존재하지 않는 서비스형 정책과 진짜 계산 실패를 구분하는
  문제가 남아있음.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

from rag_design.contracts import EvidenceStatus, SourceType
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
)

from ...llm import LLMCallError, LLMClient, loads_json_object
from ..slot_schema import (
    MAX_SLOT_ASKS,
    UNKNOWN,
    DisabilityStatus,
    EmploymentStatus,
    Gender,
    IncomeBracket,
    MaritalStatus,
    PregnancyStatus,
)
from ..state import BenefitAmount, ClaimDraft, GraphState

_UNCERTAIN_STATUSES = {
    EvidenceStatus.UNSUPPORTED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.CONFLICT,
}

_RECHECK_TOP_K = 3
_AMOUNT_METADATA_KEYS = ("amount", "benefit_amount")

# 2026-09-11: N10의 두 LLM 호출(_extract_tiered_rule_via_llm,
# _extract_amount_via_llm)은 규칙/금액 추출에 실패하면 그대로
# amount=None으로 떨어지는 유일한 경로라 규칙 기반 폴백이 없다.
# 반면 전역 LLM_MAX_NEW_TOKENS는 응답 속도를 위해 일부러
# (1024) 설정돼 있어(.env 참고), 추론형 모델(Qwen3.5-9B 등)이 내부
# "생각" 토큰을 쓰면 이 두 호출만 finish_reason='length'로 실패하는
# 사례가 실제로 관측됐다(사용자 리포트: 성공 11회/실패 4건). 다른
# 노드(N1/N5/N9/N13)의 속도는 그대로 유지하면서 이 두 호출만 별도로
# 더 넉넉한 토큰 예산을 쓰도록 전용 환경변수를 둔다 - 전역 값을
# 올리면 사용자가 명시적으로 원치 않는 전체 응답 지연이 재발한다.
_BENEFIT_CALC_MAX_NEW_TOKENS = int(
    os.environ.get("LLM_MAX_NEW_TOKENS_BENEFIT_CALC") or 8192
)


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
    slots = state.get("slots") or {}
    ask_counts = state.get("slot_ask_counts", {})
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
    # N10a(request_calc_info)가 사용자에게 되물을 슬롯 이름을 모은다.
    # set으로 모아 정책이 여러 개라도 같은 슬롯을 중복해서 묻지 않는다.
    calc_missing_slots: set[str] = set()
    # "선택형"(other_choice) 규칙이 아직 사용자의 선택을 몰라서 되물어야
    # 하는 정책별 옵션 목록. calc_missing_slots와 달리 슬롯 이름이 아니라
    # 정책마다 다른 라벨 집합을 담아야 해서 별도 리스트로 모은다.
    calc_missing_choices: list[dict] = []
    choice_answers = state.get("calc_choice_answers") or {}
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
                    metadata_equals={"source_id": policy_id, "section_type": "support_details"}
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
        needs_more_info = False
        missing: str | dict | None = None
        amount_range: tuple[float, float] | None = None
        if structured_amount is None:
            amount, note, needs_more_info, missing, amount_range = _resolve_amount(
                chunk.text,
                llm_client,
                slots,
                ask_counts,
                policy_id=policy_id,
                choice_answers=choice_answers,
            )
        else:
            amount = float(structured_amount)
            note = "재검색한 chunk metadata의 구조화 금액 필드를 그대로 사용"

        missing_calc_fields: list[str] = []
        if isinstance(missing, dict):
            calc_missing_choices.append(missing)
            missing_calc_fields = [f"choice:{missing.get('policy_id', policy_id)}"]
        elif missing is not None:
            calc_missing_slots.add(missing)
            missing_calc_fields = [missing]

        amounts.append(
            _build_benefit_amount(
                policy_id=policy_id,
                amount=amount,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
                note=note,
                slots=slots,
                needs_more_info=needs_more_info,
                missing_calc_fields=missing_calc_fields,
                amount_range=amount_range,
            )
        )

    return {
        "benefit_amounts": amounts,
        # 정렬은 테스트/로그 안정성을 위한 것일 뿐 의미는 없다.
        "calc_missing_slots": sorted(calc_missing_slots),
        "calc_missing_choices": calc_missing_choices,
    }


def _build_benefit_amount(
    *,
    policy_id: str,
    amount: float | None,
    chunk_id: str,
    chunk_text: str,
    note: str,
    slots: dict,
    needs_more_info: bool = False,
    missing_calc_fields: list[str] | None = None,
    amount_range: tuple[float, float] | None = None,
) -> BenefitAmount:
    """금액에 성격(주기/한도/지급 단위)과 총액 산술을 붙여 BenefitAmount를 만든다.

    금액을 못 구한 경우에도 같은 모양을 유지한다 - 화면에서 필드 유무를
    따로 분기하지 않아도 되게 하기 위함.

    amount_range: "90-110만원"처럼 조건 구분 없이 범위로만 적힌 금액
    (min, max) - 있을 때만 amount_min/amount_max를 채운다(2026-09-11
    추가). amount와 amount_range가 동시에 값을 가지는 일은 없다 -
    _resolve_amount가 둘 중 하나만 채워서 넘긴다.
    """

    entry: BenefitAmount = {
        "policy_id": policy_id,
        "amount": amount,
        "rule_chunk_id": chunk_id,
        "calculation_note": note,
        "period": None,
        "is_maximum": False,
        "per_unit": None,
        "total_amount": None,
        "needs_more_info": needs_more_info,
        "missing_calc_fields": missing_calc_fields or [],
        "amount_min": None,
        "amount_max": None,
        "total_amount_min": None,
        "total_amount_max": None,
    }
    if amount is None:
        if amount_range is not None:
            amount_min, amount_max = amount_range
            entry["amount_min"], entry["amount_max"] = amount_min, amount_max
            # period/per_unit(예: "월", "1인당")은 범위에도 의미가 있으니
            # 채운다. is_maximum은 범위에는 적용하지 않는다(2026-09-11
            # 확정) - 원문의 "최대"가 실제로 금액을 수식하는지, 아니면
            # "최대 3년간"처럼 다른 걸 수식하는지 위치 정보 없이는 알 수
            # 없다(바로 이 오판이 T1의 "최대 90원" 버그 원인이었다).
            # 하한/상한 중 어느 쪽에 "최대"를 붙일지도 근거가 없다.
            context = analyze_amount_context(chunk_text)
            entry["period"] = context["period"]
            entry["per_unit"] = context["per_unit"]
            # 총액(월 단가 x 개월수, 1인당 x 가구원수)은 하한/상한에
            # 각각 같은 산술을 적용할 수 있다 - 대표 금액을 고르는 것과
            # 달리, 둘 다 실제 원문 근거(개월수/가구원수)가 있을 때만
            # 계산하므로 "최대" 오판 같은 위험이 없다.
            total_min, _ = compute_total(amount_min, context, slots)
            total_max, total_max_note = compute_total(amount_max, context, slots)
            if total_min is not None and total_max is not None:
                entry["total_amount_min"] = total_min
                entry["total_amount_max"] = total_max
                entry["calculation_note"] = f"{note} / 총액 계산(범위): {total_max_note}"
        return entry

    context = analyze_amount_context(chunk_text)
    entry["period"] = context["period"]
    entry["is_maximum"] = context["is_maximum"]
    entry["per_unit"] = context["per_unit"]

    total, total_note = compute_total(amount, context, slots)
    if total is not None:
        entry["total_amount"] = total
        entry["calculation_note"] = f"{note} / 총액 계산: {total_note}"
    return entry


# ---------------------------------------------------------------------------
# 지원금 "계산기": 원문에서 금액의 성격(주기/한도/지급 단위)을 읽고, 근거가
# 충분할 때만 총액을 산술한다.
# ---------------------------------------------------------------------------
#
# 왜 필요한가: 금액만 뽑으면 "200,000원"이 월인지 연인지 1회인지, 확정인지
# 상한인지 알 수 없다. 화면에 "200,000원"이라고만 띄우면 사용자는 그만큼
# 받는다고 읽는데, 원문이 "월 최대 20만원"이면 완전히 다른 말이다.
#
# 원천 데이터 실측(2026-08-31, 정부24 지원내용 섹션 10,963건 중 단일 금액이
# 잡히는 2,259건 기준):
#   최대/한도 표현 42.5% · 월 23.2% · 1인당 18.0% · 1회성 15.2%
#   연/년 13.8% · 개월수 명시 11.6% · 가구당 4.6%
#   월 단가와 개월수가 함께 있어 연간 총액을 산술할 수 있는 경우 4.6%
# 즉 "최대"를 무시하면 절반 가까이가 과대 표기가 된다.

_PERIOD_PATTERNS = (
    # 앞에 오는 것이 우선. "월 20만원을 12개월"은 월 단가로 읽는다.
    ("month", re.compile(r"(매월|월\s*(최대|최고)?\s*\d|월액|월\s*지급|1개월당|개월당)")),
    ("year", re.compile(r"(매년|연\s*(최대|최고)?\s*\d|연간|1년당|년당|연액)")),
    ("once", re.compile(r"(1회에\s*한|1회성|일시금|일시\s*지급|한\s*번만|1인\s*1회)")),
)
_MAXIMUM_PATTERN = re.compile(r"(최대|최고|한도|이내|까지|범위\s*에서)")
_PER_UNIT_PATTERNS = (
    ("person", re.compile(r"(1인당|1명당|인당|1인\s*기준|1명\s*기준)")),
    ("household", re.compile(r"(가구당|1가구당|세대당|1세대당|가구\s*기준)")),
)
_DURATION_MONTHS_PATTERN = re.compile(r"(\d{1,3})\s*개월")
# 산술을 허용할 개월수 상한. 오타나 무관한 숫자로 터무니없는 총액이 나오는
# 것을 막는다(예: "120개월" 같은 값은 지원 기간이 아닐 가능성이 높다).
_MAX_DURATION_MONTHS = 60
# 가구원 수 상한. 슬롯 값이 이상하면 곱하지 않는다.
_MAX_HOUSEHOLD_SIZE = 15

_PERIOD_LABELS = {"month": "월", "year": "연", "once": "1회"}


def analyze_amount_context(chunk_text: str) -> dict:
    """원문에서 금액의 성격을 읽는다. 금액 자체는 다루지 않는다.

    반환: ``{"period", "is_maximum", "per_unit", "duration_months"}``.
    확신할 수 없는 항목은 ``None``/``False``로 둔다 - 여기서 추측하면 그
    추측이 그대로 화면의 금액 표기가 된다.
    """

    period = next(
        (name for name, pattern in _PERIOD_PATTERNS if pattern.search(chunk_text)), None
    )
    per_unit = next(
        (name for name, pattern in _PER_UNIT_PATTERNS if pattern.search(chunk_text)), None
    )
    duration = None
    match = _DURATION_MONTHS_PATTERN.search(chunk_text)
    if match is not None:
        months = int(match.group(1))
        if 0 < months <= _MAX_DURATION_MONTHS:
            duration = months

    return {
        "period": period,
        "is_maximum": bool(_MAXIMUM_PATTERN.search(chunk_text)),
        "per_unit": per_unit,
        "duration_months": duration,
    }


def compute_total(amount: float, context: dict, slots: dict | None) -> tuple[float | None, str]:
    """근거가 충분할 때만 총액을 계산한다. 아니면 ``(None, 사유)``.

    두 가지 산술만 한다 - 둘 다 원문에 근거가 명시된 경우다.

    1. 월 단가 x 지원 개월수 (원문에 둘 다 적혀 있을 때)
    2. 1인당 단가 x 가구원수 (원문이 "1인당"이라 하고 사용자 가구원수를 알 때)

    "월 20만원"만 있고 기간이 안 적혀 있으면 12를 곱하지 않는다. 지원 기간을
    모르는데 1년치로 단정하면 실제와 다른 금액을 확정값처럼 보여주게 된다.
    """

    steps: list[str] = []
    total = amount

    if context.get("period") == "month" and context.get("duration_months"):
        months = context["duration_months"]
        total *= months
        steps.append(f"월 {amount:,.0f}원 x {months}개월")

    if context.get("per_unit") == "person":
        household_size = (slots or {}).get("household_size")
        if isinstance(household_size, int) and 1 < household_size <= _MAX_HOUSEHOLD_SIZE:
            total *= household_size
            steps.append(f"1인당 금액 x 가구원 {household_size}명")

    if not steps:
        return None, ""

    prefix = "최대 " if context.get("is_maximum") else ""
    return total, f"{prefix}{' , '.join(steps)} = {total:,.0f}원"


def _resolve_amount_without_metadata(
    chunk_text: str, llm_client: LLMClient | None
) -> tuple[float | None, str, tuple[float, float] | None]:
    """구조화 금액 필드가 없을 때 금액을 정한다: LLM 우선, 규칙은 보조.

    LLM을 먼저 쓰는 이유는 "월 20만원 지원"과 "본인부담금 5만원"처럼 문맥으로
    구분해야 하는 숫자를 규칙보다 잘 가리기 때문이다. 다만 LLM이 **판단해서**
    금액이 없다고 한 경우에는 그 판단을 존중하고 규칙으로 뒤집지 않는다 -
    규칙으로 덮어쓰면 조건부 금액을 확정 금액인 것처럼 만들 수 있다.

    규칙 경로로 넘어가는 것은 LLM에게 물어보지 못했을 때뿐이다(미연결,
    호출 실패, 응답 파싱 실패). 이때는 아무 금액도 못 주는 것보다 원문에
    명시된 단일 금액이라도 뽑아주는 편이 낫고, 어디서 나온 값인지
    calculation_note에 분명히 남긴다.

    반환의 세 번째 요소(amount_range)는 LLM이 "90-110만원"처럼 조건 구분
    없는 범위로 판단했을 때, 또는 LLM에 물어보지 못해 규칙 경로로 넘어간
    경우에도 원문이 그런 범위 형태면 채워진다(2026-09-11 추가, 규칙
    경로도 범위를 지원하도록 확장 - _extract_range_by_rules 참고).
    """

    llm_amount, llm_note, consulted, llm_range = _extract_amount_via_llm(
        chunk_text, llm_client
    )
    if consulted:
        return llm_amount, llm_note, llm_range

    rule_amount, rule_note = _extract_amount_by_rules(chunk_text)
    rule_range = _extract_range_by_rules(chunk_text) if rule_amount is None else None
    if llm_note:
        # LLM을 붙였는데 실패한 경우 - 그 사실을 숨기지 않는다.
        return rule_amount, f"{rule_note} / {llm_note}", rule_range
    return rule_amount, f"{rule_note} (LLM 미연결)", rule_range


# ---------------------------------------------------------------------------
# 조건부/구간별 금액 규칙 (2026-09-08 추가).
# ---------------------------------------------------------------------------
#
# 왜 필요한가: 원문에 "소득 구간별로 10만원 또는 30만원 차등 지급"처럼
# 적혀 있으면, 그동안은 _extract_amount_by_rules가 "금액이 여러 개라 확정할
# 수 없음"으로 amount=None을 남기고 끝났다(조건별 차등과 본인부담금 혼재를
# 구분 못 하니 안전한 선택이었다). 하지만 사용자의 소득 구간을 이미 알고
# 있다면 실제로는 계산할 수 있는 경우가 많다.
#
# 여기서도 모듈 상단 원칙("LLM은 규칙만 추출, 코드가 결정론적 산술")을
# 그대로 지킨다 - LLM은 "어떤 변수에 따라 얼마씩 갈리는지"만 원문 그대로
# 추출하고, 그 변수의 실제 슬롯 값과 대조해 금액 하나를 고르는 것은
# _select_tier_amount가 코드로 결정론적으로 한다.
#
# 대상 변수는 slot_schema 계약에 이미 있는 열거형 슬롯 6개 + 숫자 슬롯
# 3개로 한정한다(2026-09-11 확장, 2026-09-12 gender/age 추가 - 처음엔
# 열거형 5개뿐이었는데, 실데이터 전수 조사에서 marital_status/
# pregnancy_status로 실제 금액이 갈리는 사례는 거의 없고, 대신
# children_count(자녀수/출생순위)/household_size(가구원수) 조건이 훨씬
# 흔하다는 게 확인됐다 - "첫만남이용권"(첫째 200만원/둘째 이상
# 300만원), "임산부 교통비 바우처"(첫째 70만원/둘째 80만원/셋째 이상
# 100만원) 등. 이 둘은 slot_schema.SLOT_ENUMS에 없는 정수 슬롯이라 아래
# _TIER_NUMERIC_VARIABLES로 따로 추적하고, tiers도 열거형
# (match_values)이 아니라 구간(match_min/match_max)으로 표현한다 -
# _parse_numeric_tiers/_numeric_tier_matches 참고.
#
# gender(성별)/age(만 나이)는 2026-09-12에 추가했다 - 장애인가정
# 출산지원금(성별+장애여부 조건), 한부모가족 양육비(연령대 조건)처럼
# 실데이터에 흔한데 지금까지 계산에 연결되지 않았다. 이 둘은 다른
# 변수와 달리 "새로 물어볼 필요가 없다" - gender는 하드 게이트
# 슬롯이라 N10에 도달한 시점엔 이미 실제 값이거나 UNKNOWN으로
# 확정돼 있고(아래 income_bracket 등과 동일하게 처리), age는
# 하드 게이트 슬롯인 birth_date로부터 N1(slot_parser)이 이미 파생해
# 채워둔 값이다(연 나이 기준으로 갈리는 정책은 이번 범위 밖 - 나이
# 슬롯은 만 나이만 쓴다). 그래서 gender는 _TIER_VARIABLE_ENUMS에,
# age는 _TIER_NUMERIC_VARIABLES에 넣되 둘 다 _TIER_ASKABLE_SOFT_FIELDS
# 에는 넣지 않는다 - 값이 없으면 (극히 예외적인 경우에도) 되묻지 않고
# 바로 amount=None으로 fail-closed 처리한다(_select_tier_amount의
# "else" 분기, income_bracket과 동일한 경로).
#
# 그 밖의 변수(예: "근로일수", 서비스 종류 선택형)는 단순 "값 하나"
# 슬롯이 아니라 "어떤 옵션을 쓸지" 선택이 필요한 경우 variable=
# "other_choice"로 다룬다(아래 _OPEN_CHOICE_VARIABLE 참고). 그 외
# 정말 알려진 슬롯 어디에도 대응되지 않는 기준은 여전히 물어볼
# 슬롯/파서가 없어 amount=None + 사유로 처리한다.
_TIER_VARIABLE_ENUMS: dict[str, type] = {
    "income_bracket": IncomeBracket,
    "employment_status": EmploymentStatus,
    "disability_status": DisabilityStatus,
    "marital_status": MaritalStatus,
    "pregnancy_status": PregnancyStatus,
    "gender": Gender,
}
# 열거형이 아니라 정수 슬롯. children_count/household_size는
# slot_schema.SLOT_ENUMS에는 없지만 SOFT_SLOTS에는 있고,
# llm_gateway.extract_slots가 정수로 검증해서 채워준다. age(만 나이)는
# SlotState의 파생값으로 N1이 birth_date에서 계산해 채운다(slot_parser.py
# 참고) - 셋 다 tiers를 match_values 대신 match_min/match_max(둘 다
# 포함, None이면 무제한)로 표현한다.
_TIER_NUMERIC_VARIABLES = frozenset({"age", "children_count", "household_size"})
# LLM 프롬프트에 각 정수 변수가 무엇을 뜻하는지 알려주는 힌트 - 필드별로
# 뜻이 달라서(자녀수/가구원수/나이) 하나의 템플릿 문구로 뭉뚱그리면
# LLM이 age를 children_count로 착각할 위험이 있다.
_TIER_NUMERIC_VARIABLE_HINTS: dict[str, str] = {
    "children_count": "자녀 수(출생순위)",
    "household_size": "가구원 수",
    "age": "만 나이",
}
# marital_status/pregnancy_status/children_count/household_size는 전부
# 소프트 슬롯(slot_schema.SOFT_SLOTS)이라 N2 하드 게이트를 거치지 않는다
# - 값이 아직 None이면 N10a(request_calc_info)가 새로 물어볼 수 있다.
# income_bracket/employment_status/disability_status/gender는 하드
# 게이트라 N10에 도달한 시점엔 이미 실제 값이거나 UNKNOWN 센티넬로
# 확정돼 있다 - UNKNOWN이면 "이미 N2/N3가 물어봤지만 답을 못 받은 것"
# 으로 보고 N10에서 다시 묻지 않는다(하드 게이트 되묻기 상한을 우회하지
# 않는다). age는 하드 게이트 슬롯 자체는 아니지만(birth_date가 하드
# 게이트다) 그로부터 파생된 값이라 같은 이유로 여기에 넣지 않는다 -
# birth_date를 못 받아 age가 여전히 None인 극히 예외적인 경우에도
# N10에서 새로 묻지 않고 fail-closed로 amount=None 처리한다.
_TIER_ASKABLE_SOFT_FIELDS = frozenset(
    {"marital_status", "pregnancy_status", "children_count", "household_size"}
)

# "선택형" 조건부 규칙: slot_schema 열거형에 없는, 사용자가 고를 수 있는
# 옵션(예: 자연분만/제왕절개)에 따라 금액이 다른 경우를 위한 합성
# variable 값이다. 이 값이 오면 tiers는 match_values/match_min/match_max
# 대신 label 하나로만 매칭한다(_parse_choice_tiers/_select_tier_amount
# 참고) - 실제 매칭은 사용자의 자유 답변을 정책별 라벨 목록과 대조해서
# request_calc_info.py가 한다(전역 슬롯이 아니라 정책별 임시 답변,
# state["calc_choice_answers"]에 담긴다).
#
# 주의: 원문 금액이 외부 값(차량가액, 소득 실수령액 등)에 비율(%)을
# 곱해서 나오는 경우는 여기 해당하지 않는다 - 그 외부 값은 원문에도,
# 슬롯에도 없어 LLM이 "원문을 더 읽어서" 만들어낼 방법이 없다(추측 금지
# 원칙). 그런 경우는 여전히 variable=null로 두고 기존 단일 금액 경로가
# "확인 불가"로 남긴다(_extract_tiered_rule_via_llm 프롬프트 참고).
_OPEN_CHOICE_VARIABLE = "other_choice"

_HAN_CHARACTER_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _looks_like_broken_korean(text: str) -> bool:
    """LLM이 자유 서술형으로 쓴 reason에 한자가 섞였는지 확인한다.

    True면 그 reason은 화면에 그대로 보여주지 않고 고정된 한국어 문구로
    대체한다 - amount/variable/tiers 같은 구조화 필드는 영향받지 않는다.
    """
    return bool(_HAN_CHARACTER_PATTERN.search(text))


def _numeric_tier_matches(tier: dict, value: int) -> bool:
    """정수 슬롯 값이 tier의 구간(match_min~match_max, 둘 다 포함)에 드는지 본다.

    None인 쪽은 그쪽 경계가 없다는 뜻이다(예: match_max=None은 "OO 이상").
    """
    match_min = tier.get("match_min")
    match_max = tier.get("match_max")
    if match_min is not None and value < match_min:
        return False
    if match_max is not None and value > match_max:
        return False
    return True


def _format_numeric_range(tier: dict) -> str:
    """label이 없는 숫자 tier를 사람이 읽을 문구로 바꾼다(오류 메시지용)."""
    match_min = tier.get("match_min")
    match_max = tier.get("match_max")
    if match_min is not None and match_max is not None:
        if match_min == match_max:
            return str(match_min)
        return f"{match_min}~{match_max}"
    if match_min is not None:
        return f"{match_min} 이상"
    if match_max is not None:
        return f"{match_max} 이하"
    return ""


def _parse_enum_tiers(tiers_raw: list, valid_values: set[str]) -> list[dict]:
    """열거형 변수(income_bracket 등)의 tiers를 검증하고 겹치는 값을 버린다.

    먼저 계약값이 아닌 match_values를 걸러내며 tier 후보를 모은다. 겹치는
    값 판정은 전체 tier를 다 본 뒤에 해야 한다 - tier를 하나씩 확정지으면서
    판단하면, "employed/self_employed" tier가 먼저 확정된 뒤에야 나오는
    "self_employed" 단독 tier와 겹치는 걸 못 잡는다(먼저 확정된 tier 쪽은
    이미 seen_values에 없던 시점이라 그대로 남아버림 - 실제로 났던 버그,
    테스트로 회귀 방지).
    """
    raw_tiers: list[dict] = []
    value_counts: dict[str, int] = {}
    for tier in tiers_raw:
        if not isinstance(tier, dict):
            continue
        amount = tier.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
            continue
        match_values = [
            value
            for value in (tier.get("match_values") or [])
            if isinstance(value, str) and value in valid_values
        ]
        if not match_values:
            continue
        raw_tiers.append(
            {
                "match_values": match_values,
                "amount": float(amount),
                "label": str(tier.get("label") or "")[:200],
            }
        )
        for value in match_values:
            value_counts[value] = value_counts.get(value, 0) + 1

    # 같은 값이 2개 이상 tier에 걸치면 어느 쪽으로도 확정할 수 없다 - 겹치는
    # 값은 모든 tier에서 통째로 버려서 나중에 매칭 자체가 안 되게 한다
    # (조용히 하나를 고르지 않는다).
    ambiguous_values = {value for value, count in value_counts.items() if count > 1}

    tiers: list[dict] = []
    for tier in raw_tiers:
        match_values = [
            value for value in tier["match_values"] if value not in ambiguous_values
        ]
        if not match_values:
            continue
        tiers.append({**tier, "match_values": match_values})
    return tiers


def _parse_numeric_tiers(tiers_raw: list) -> list[dict]:
    """숫자 변수(children_count/household_size/age)의 tiers를 검증하고 겹치는
    구간을 버린다.

    match_min/match_max 중 최소 하나는 있어야 하고(둘 다 없으면 "무제한"
    이라는 의미 없는 tier), 하한이 상한보다 크면 버린다. 겹치는 구간은 두
    tier 모두 통째로 버린다 - _parse_enum_tiers와 같은 "조용히 하나를
    고르지 않는다" 원칙이다.
    """
    raw_tiers: list[dict] = []
    for tier in tiers_raw:
        if not isinstance(tier, dict):
            continue
        amount = tier.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
            continue
        match_min = tier.get("match_min")
        match_max = tier.get("match_max")
        if match_min is not None and (
            not isinstance(match_min, (int, float)) or isinstance(match_min, bool)
        ):
            continue
        if match_max is not None and (
            not isinstance(match_max, (int, float)) or isinstance(match_max, bool)
        ):
            continue
        if match_min is None and match_max is None:
            continue
        lo = int(match_min) if match_min is not None else None
        hi = int(match_max) if match_max is not None else None
        if lo is not None and hi is not None and lo > hi:
            continue
        raw_tiers.append(
            {
                "match_min": lo,
                "match_max": hi,
                "amount": float(amount),
                "label": str(tier.get("label") or "")[:200],
            }
        )

    def _overlaps(a: dict, b: dict) -> bool:
        a_lo = a["match_min"] if a["match_min"] is not None else float("-inf")
        a_hi = a["match_max"] if a["match_max"] is not None else float("inf")
        b_lo = b["match_min"] if b["match_min"] is not None else float("-inf")
        b_hi = b["match_max"] if b["match_max"] is not None else float("inf")
        return a_lo <= b_hi and b_lo <= a_hi

    ambiguous_indices: set[int] = set()
    for i in range(len(raw_tiers)):
        for j in range(i + 1, len(raw_tiers)):
            if _overlaps(raw_tiers[i], raw_tiers[j]):
                ambiguous_indices.add(i)
                ambiguous_indices.add(j)

    return [tier for idx, tier in enumerate(raw_tiers) if idx not in ambiguous_indices]

def _parse_choice_tiers(tiers_raw: list) -> list[dict]:
    """선택형(variable="other_choice") tiers를 검증하고 겹치는 라벨을 버린다.

    match_values/match_min/match_max는 쓰지 않는다 - label 자체가 매칭
    기준이다(사용자의 자유 답변을 이 label과 대조하는 것은
    request_calc_info.merge_calc_choice_answer가 한다). label이 비어
    있으면 매칭할 방법이 없으므로 버린다. 같은 라벨(대소문자/공백 무시)이
    2개 이상 tier에 나오면 어느 쪽인지 확정할 수 없어 둘 다 버린다 -
    _parse_enum_tiers/_parse_numeric_tiers와 같은 "조용히 하나를 고르지
    않는다" 원칙이다.
    """
    raw_tiers: list[dict] = []
    label_counts: dict[str, int] = {}
    for tier in tiers_raw:
        if not isinstance(tier, dict):
            continue
        amount = tier.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
            continue
        label = str(tier.get("label") or "").strip()[:200]
        if not label:
            continue
        raw_tiers.append({"label": label, "amount": float(amount)})
        key = label.casefold()
        label_counts[key] = label_counts.get(key, 0) + 1

    ambiguous = {key for key, count in label_counts.items() if count > 1}
    return [tier for tier in raw_tiers if tier["label"].casefold() not in ambiguous]

def _extract_tiered_rule_via_llm(
    chunk_text: str, llm_client: LLMClient | None
) -> tuple[dict | None, str]:
    """chunk 원문에서 "OO에 따라 금액이 달라지는" 조건부 규칙을 추출한다.

    LLM은 어떤 변수(variable)에 따라 금액이 갈리는지와, 각 구간에 해당하는
    값(열거형 값/정수 구간/선택 라벨)·금액만 원문 그대로 추출한다 - 대표값
    선택이나 산술은 하지 않는다(_select_tier_amount가 코드로 결정론적으로
    한다).

    반환: ``(rule, note)``. 유효한 규칙을 못 찾으면 ``(None, 사유)``.
    rule 형태: ``{"variable": <5개 열거형 필드 중 하나 | 정수 필드 2개
    중 하나 | "other_choice">, "tiers": [...]}`` - variable에 따라
    tier 형태가 다르다: 열거형이면 match_values, 정수면
    match_min/match_max, "other_choice"면 label만 쓴다.
    """
    if llm_client is None:
        return None, "LLM 미연결 - 조건부 규칙 인식 생략"

    enum_variable_lines = "\n".join(
        f'- "{field}" (열거형): '
        + ", ".join(f'"{m.value}"' for m in enum_type if m.value != UNKNOWN)
        for field, enum_type in _TIER_VARIABLE_ENUMS.items()
    )
    numeric_variable_lines = "\n".join(
        f'- "{field}" (정수, {_TIER_NUMERIC_VARIABLE_HINTS.get(field, "")} - '
        '"1"처럼 정확한 값이나 "2 이상"/"1~2"처럼 구간으로 표현 가능)'
        for field in sorted(_TIER_NUMERIC_VARIABLES)
    )
    variable_lines = f"{enum_variable_lines}\n{numeric_variable_lines}"
    prompt = (
        "다음은 복지 정책의 지원내용 원문이다. 아래 아홉 가지 변수 중 "
        "하나에 따라 지원 금액이 여러 구간(조건부/차등)으로 달라지는지 "
        "판단하라. 원문에 명시되지 않은 금액이나 조건을 새로 만들거나 "
        "추측하지 마라.\n\n"
        f"[변수와 값 목록]\n{variable_lines}\n\n"
        "위 아홉 변수에 해당하지 않더라도, 원문에 사용자가 직접 고를 수 "
        "있는 옵션(예: '자연분만/제왕절개', '시설이용/재가서비스'처럼 "
        "서비스 종류나 신청 방식 선택)에 따라 금액이 다르면 variable을 "
        '"other_choice"로 둬라. 단, 옵션에 따라 갈리는 게 아니라 '
        "차량가액·소득 실수령액처럼 원문에 없는 외부 값에 비율(%)을 "
        '곱하거나 한도를 적용해야 계산되는 경우는 "other_choice"에 '
        "해당하지 않는다 - 이런 경우는 variable을 null로 둬라(그 외부 "
        "값은 원문에도 없어 추측 없이는 알 수 없다).\n\n"
        "해당하는 조건부 규칙이 없으면(단일 금액이거나, 위 열 가지 "
        "어디에도 해당하지 않거나, 애매하면) variable을 null로 둬라.\n\n"
        "variable이 열거형이면 각 tier의 match_values에 해당 값을 넣고 "
        "match_min/match_max는 둘 다 null로 둬라. variable이 정수형"
        "(children_count/household_size/age)이면 반대로 match_values는 빈 "
        "배열로 두고, 각 tier의 match_min(하한, 정수)과 match_max(상한, "
        "정수)를 채워라 - 상한이 없으면(예: '3명 이상') match_max를 "
        "null로, 하한이 없으면(예: '2명 이하') match_min을 null로 둔다. "
        "예: '첫째아 200만원, 둘째아 이상 300만원'이면 첫 tier는 "
        "match_min=1, match_max=1, 둘째 tier는 match_min=2, "
        'match_max=null이다. variable이 "other_choice"면 match_values/'
        "match_min/match_max는 전부 비우고(빈 배열/null), 각 tier의 "
        'label에 원문 그대로의 옵션 이름(예: "자연분만")을 넣어라 - '
        "label이 곧 그 tier를 고르는 값이다.\n\n"
        "tiers의 amount는 항상 '원' 단위의 정수로 변환해서 반환하라 - "
        "원문 표기를 그대로 베끼지 마라. 예: '20만원' \u2192 200000, "
        "'90만원' \u2192 900000 ('90만원'을 90으로 반환하면 안 된다).\n\n"
        "reason은 반드시 한국어로만 작성하라 - 한자나 영어 단어를 섞지 마라.\n\n"
        '출력 형식(다른 텍스트 없이 이 JSON 하나만): '
        '{"variable": <위 목록의 필드명, "other_choice", 또는 null>, '
        '"tiers": [{"match_values": [<열거형일 때만, 위 값 목록 중 해당 '
        '값들>], "match_min": <정수형일 때만, 하한 또는 null>, '
        '"match_max": <정수형일 때만, 상한 또는 null>, '
        '"amount": <숫자>, "label": "<원문 표현 그대로, 한 줄 - '
        'other_choice면 옵션 이름>"}], '
        '"reason": "<한 줄 설명>"}\n\n'
        f"원문:\n{chunk_text}"
    )
    try:
        response = llm_client.complete(
            prompt,
            system=(
                "너는 복지 정책 원문에서 조건부 금액 규칙만 추출하는 도구다. "
                "절대 계산하거나 추측하지 않고, 원문에 없는 값은 만들지 않는다."
            ),
            max_tokens=_BENEFIT_CALC_MAX_NEW_TOKENS,
        )
    except LLMCallError:
        # 2026-09-11: 원래는 f"LLM 조건부 규칙 추출 호출 실패: {exc}"로
        # 예외 메시지를 그대로 노출했다 - 이 메시지 안에 "timeout_seconds를
        # 늘리거나 더 작은 모델을 쓰세요" 같은 운영자용 디버깅 안내까지
        # 들어 있어(llm/client.py의 LLMCallError 메시지 구성 참고) 사용자
        # 화면(needs_confirmation)에 그대로 노출됐다. 사용자 요청으로
        # 고정된 짧은 문구로 바꾼다 - 실패했다는 사실 자체는 그대로
        # amount=None으로 드러나므로 숨기는 게 아니다.
        return None, "계산 실패"

    try:
        parsed = loads_json_object(response)
    except (ValueError, AttributeError, TypeError):
        return None, "계산 실패"

    variable = parsed.get("variable")
    if variable is None:
        tier_reason = parsed.get("reason")
        if isinstance(tier_reason, str) and _looks_like_broken_korean(tier_reason):
            tier_reason = None
        return None, tier_reason or "원문에 조건부 규칙(변수/선택 옵션) 없음"
    is_numeric_variable = variable in _TIER_NUMERIC_VARIABLES
    is_choice_variable = variable == _OPEN_CHOICE_VARIABLE
    if not isinstance(variable, str) or (
        variable not in _TIER_VARIABLE_ENUMS
        and not is_numeric_variable
        and not is_choice_variable
    ):
        return None, "계산 실패"

    tiers_raw = parsed.get("tiers")
    if not isinstance(tiers_raw, list):
        return None, "계산 실패"

    if is_numeric_variable:
        tiers = _parse_numeric_tiers(tiers_raw)
    elif is_choice_variable:
        tiers = _parse_choice_tiers(tiers_raw)
    else:
        enum_type = _TIER_VARIABLE_ENUMS[variable]
        valid_values = {m.value for m in enum_type if m.value != UNKNOWN}
        tiers = _parse_enum_tiers(tiers_raw, valid_values)

    if not tiers:
        return None, "LLM이 조건부 규칙을 시사했지만 유효한 구간을 하나도 추출하지 못함"

    return {"variable": variable, "tiers": tiers}, ""


def _select_tier_amount(
    rule: dict,
    slots: dict,
    ask_counts: dict,
    *,
    policy_id: str = "",
    choice_answers: dict[str, str] | None = None,
) -> tuple[float | None, str, str | dict | None]:
    """규칙과 사용자 슬롯을 대조해 금액 하나를 결정한다 (코드가 결정론적으로).

    LLM이 만든 것은 규칙(변수 + 구간별 금액)뿐이고, 그 변수의 실제 슬롯
    값과 대조해 금액 하나를 고르는 것은 이 함수가 순수 코드로 한다 -
    LLM이 직접 "당신은 얼마"라고 답하게 하지 않는다.

    variable이 _TIER_NUMERIC_VARIABLES(children_count/household_size/age)면
    tier 매칭이 열거형 exact match가 아니라 구간(match_min~match_max)
    포함 여부다(_numeric_tier_matches 참고) - 이 경우 UNKNOWN 센티넬
    개념도 없다(정수 슬롯은 애초에 "unknown" 문자열 값을 갖지 않는다).

    반환: ``(amount, note, missing)``. ``missing``은 아직 값을 몰라서
    (그리고 아직 물어볼 수 있어서) N10a가 되물어야 할 대상이다 - 슬롯
    값 하나가 필요하면 그 슬롯 이름(``str``)을, variable이
    "other_choice"(선택형)라 라벨 목록에서 하나를 골라야 하면
    ``{"policy_id":.., "labels": [...]}``(``dict``)를 담는다. 물어볼
    수 없거나 필요 없으면 ``None``이다.
    """
    variable = rule["variable"]
    is_numeric_variable = variable in _TIER_NUMERIC_VARIABLES
    is_choice_variable = variable == _OPEN_CHOICE_VARIABLE

    if is_choice_variable:
        choice_answers = choice_answers or {}
        answer_label = choice_answers.get(policy_id)
        ask_key = f"choice:{policy_id}"
        if answer_label is None:
            if ask_counts.get(ask_key, 0) < MAX_SLOT_ASKS:
                labels = [tier["label"] for tier in rule["tiers"]]
                return (
                    None,
                    "정확한 금액 계산에 옵션 확인이 필요함",
                    {"policy_id": policy_id, "labels": labels},
                )
            return (
                None,
                "옵션을 확인하지 못해 조건부 금액을 계산할 수 없음 (재질문 상한 도달)",
                None,
            )
        if answer_label == UNKNOWN:
            return (
                None,
                "옵션을 확인하지 못해(미확인) 조건부 금액을 계산할 수 없음",
                None,
            )
        matches = [tier for tier in rule["tiers"] if tier["label"] == answer_label]
        if not matches:
            return (
                None,
                f"선택한 옵션('{answer_label}')이 원문의 옵션 목록과 일치하지 않음",
                None,
            )
        tier = matches[0]
        return tier["amount"], f"선택한 옵션('{answer_label}')에 따른 금액", None

    value = slots.get(variable)

    if value is None:
        if variable in _TIER_ASKABLE_SOFT_FIELDS:
            if ask_counts.get(variable, 0) < MAX_SLOT_ASKS:
                return None, f"정확한 금액 계산에 '{variable}' 확인이 필요함", variable
            return (
                None,
                f"'{variable}'을(를) 확인하지 못해 조건부 금액을 계산할 수 없음"
                " (재질문 상한 도달)",
                None,
            )
        # income_bracket/employment_status/disability_status는 하드 게이트라
        # 원칙적으로 이 시점엔 None일 수 없다(N2가 UNKNOWN으로라도 확정한다).
        # 그래도 방어적으로 처리한다 - fail-closed.
        return None, f"'{variable}' 슬롯 값을 확인할 수 없어 계산할 수 없음", None

    if not is_numeric_variable and value == UNKNOWN:
        return (
            None,
            f"'{variable}'을(를) 확인하지 못해(미확인) 조건부 금액을 계산할 수 없음",
            None,
        )

    if is_numeric_variable:
        if not isinstance(value, int) or isinstance(value, bool):
            # llm_gateway.extract_slots가 항상 정수로 검증해서 채우므로
            # 정상 경로에서는 여기 오지 않는다 - 방어적 fail-closed.
            return None, f"'{variable}' 값이 올바르지 않아 계산할 수 없음", None
        matches = [tier for tier in rule["tiers"] if _numeric_tier_matches(tier, value)]
    else:
        matches = [tier for tier in rule["tiers"] if value in tier["match_values"]]

    if not matches:
        if is_numeric_variable:
            shown = ", ".join(
                tier["label"] or _format_numeric_range(tier) for tier in rule["tiers"]
            )
        else:
            shown = ", ".join(
                tier["label"] or "/".join(tier["match_values"]) for tier in rule["tiers"]
            )
        return (
            None,
            f"'{variable}'={value}가 원문의 조건 구간({shown})과 일치하지 않음",
            None,
        )
    tier = matches[0]
    if is_numeric_variable:
        label = tier["label"] or _format_numeric_range(tier)
    else:
        label = tier["label"] or "/".join(tier["match_values"])
    return tier["amount"], f"'{variable}'={value} 조건({label})에 따른 금액", None

def _resolve_amount(
    chunk_text: str,
    llm_client: LLMClient | None,
    slots: dict,
    ask_counts: dict,
    *,
    policy_id: str = "",
    choice_answers: dict[str, str] | None = None,
) -> tuple[float | None, str, bool, str | dict | None, tuple[float, float] | None]:
    """구조화 금액 필드가 없을 때 금액을 정한다: 조건부 규칙을 먼저 보고,
    없으면 기존 단일 금액 경로(_resolve_amount_without_metadata)로 넘어간다.

    반환: ``(amount, note, needs_more_info, missing, amount_range)``.
    missing은 _select_tier_amount와 같은 의미다(슬롯 이름 str 또는
    선택형 {"policy_id":.., "labels":[...]} dict). amount_range는
    조건 구분 없는 범위(예: "90-110만원")일 때만 채워지고, 조건부
    규칙(tier)이 있을 때는 항상 None이다 - tier는 이미 슬롯 값/선택
    답변에 따라 단일 금액을 고르므로 범위 표시가 필요 없다.
    """
    rule, rule_note = _extract_tiered_rule_via_llm(chunk_text, llm_client)
    if rule is not None:
        amount, note, missing = _select_tier_amount(
            rule, slots, ask_counts, policy_id=policy_id, choice_answers=choice_answers
        )
        return amount, note, missing is not None, missing, None

    amount, note, amount_range = _resolve_amount_without_metadata(chunk_text, llm_client)
    if llm_client is not None and rule_note:
        # 조건부 규칙 인식은 시도했지만 해당 없음/실패였다는 사실을 남긴다
        # (실패를 숨기지 않는다는 모듈 원칙과 동일).
        note = f"{note} (조건부 규칙 확인: {rule_note})"
    return amount, note, False, None, amount_range


# 원문에 "이미 적혀 있는" 금액을 뽑는 규칙 경로.
#
# 왜 필요한가: 예전에는 LLM이 없으면 무조건 amount=None이었다. 그래서 실제
# 화면에는 늘 "지원금액 확인 필요"만 떴다 - 원문에 "월 20만원"이라고 대놓고
# 적혀 있는 정책도 마찬가지였다. LLM 연결이 끊기거나 크레딧이 떨어지면 금액
# 기능이 통째로 죽는 구조이기도 했다.
#
# 안전장치: 원문에서 찾은 서로 다른 금액이 2개 이상이면 **아무것도 고르지
# 않는다**(조건별 차등이거나 본인부담금이 섞인 경우). 임의로 대표값을
# 만들지 않는다는 이 노드의 원칙을 그대로 따른다.
_AMOUNT_UNIT_MULTIPLIERS = (
    ("억", 100_000_000),
    ("만", 10_000),
    ("", 1),
)
# "280,000원", "28만원", "28만 원", "1억원" 형태만 받는다. "1억 2천만원"처럼
# 단위가 섞인 표기는 의도적으로 인식하지 않는다 - 잘못 읽느니 미확인으로
# 두는 편이 안전하다.
_AMOUNT_PATTERN = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:,\d{3})*|\d+)\s*(억|만)?\s*원"
)
# 금액처럼 보이지만 지원금이 아닌 값이 붙는 표현. 이 단어가 금액 바로 앞
# 12자 안에 있으면 후보에서 뺀다.
_NON_BENEFIT_CONTEXT = (
    "본인부담", "자부담", "부담금", "납부", "수수료", "보증금", "이내 소득",
    "소득이", "미만인", "이하인", "초과", "재산",
)
_NON_BENEFIT_WINDOW = 12
# 대출·융자·보증 프로그램의 "한도"는 지원금이 아니라 빌릴 수 있는 상한이다.
# 줄 머리에 라벨로 붙는 형태("○ 융자 한도액 : 사업장당 15억 원")라 위의 12자
# 창으로는 닿지 않아, 줄 시작부터 확인한다(공백은 무시).
_LIMIT_LABELS = ("대출한도", "융자한도", "보증한도", "대출가능금액", "대출가능액")
# "승소가액 3억원 이상", "30만원 이상 농기계"처럼 뒤에 '이상'이 붙으면 지원
# 금액이 아니라 자격 문턱값이다. '이하'/'미만'은 "농가당 300만원 이하 지원"처럼
# 지원 상한 표현으로도 흔히 쓰여서 넣지 않는다(실측에서 정상 추출을 다수 지웠다).
_THRESHOLD_TAIL = "이상"
_THRESHOLD_TAIL_WINDOW = 4
_WHITESPACE = re.compile(r"\s+")

# "90-110만원"처럼 조건 구분 없이 하한~상한만 적힌 범위 표현(2026-09-11
# 추가) - LLM 없이도 인식해서 오프라인일 때도 범위를 보여줄 수 있게
# 하고, LLM이 준 min_amount/max_amount를 원문으로 교차 검증하는 데도
# 쓴다(아래 _extract_amount_via_llm 참고). 단위가 뒤쪽 숫자에만 붙는
# 표기("90-110만원")를 기본으로 삼는다 - 앞쪽 숫자에 단위가 없으면
# 뒤쪽 단위를 공유하는 것으로 본다.
_RANGE_PATTERN = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:,\d{3})*|\d+)\s*(억|만)?\s*[-~]\s*"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s*(억|만)?\s*원"
)
# 이 단어들이 범위 표현 앞뒤 가까이 있으면 "하나의 정책이 폭넓게 주는
# 금액"이 아니라 "조건별로 다른 두 금액을 우연히 대시(-)로 나열한 것"
# 일 가능성이 높다 - 그런 경우는 범위로 보지 않고 확정하지 않는다
# (조건부 차등을 범위로 잘못 뭉치면, 실제로는 못 받는 조건에서도 하한
# 금액을 받는 것처럼 보이게 된다).
_TIER_CONTEXT_KEYWORDS = (
    "구간", "등급", "유형", "소득", "취업", "장애", "혼인", "임신", "차등", "따라",
)
_TIER_CONTEXT_WINDOW = 15


def _extract_amount_by_rules(chunk_text: str) -> tuple[float | None, str]:
    """원문에 명시된 금액을 정규식으로 뽑는다. 계산하지 않는다.

    LLM을 못 쓰는 상황(미연결/호출 실패)의 보조 경로다. 확정적인 단일 금액이
    보일 때만 값을 돌려주고, 애매하면 사유를 남기고 ``None``을 돌려준다.
    """

    candidates: list[float] = []
    # 한도·문턱값 때문에 후보에서 뺀 금액이 있으면 남은 하나를 답으로 승격시키지
    # 않는다. 승격시키면 "융자 한도 15억"을 지운 자리에 무관한 숫자가 단일 금액으로
    # 올라온다(전체 코퍼스 실측에서 9건 발생했다).
    blocked = False
    # "90-110만원"의 "110만원"처럼 범위 표현의 일부인 매치는 독립된 단일
    # 금액 후보로 세지 않는다 - 세면 하한(90만원)을 버리고 상한만 확정
    # 금액인 것처럼 잘못 보여주게 된다(2026-09-11 T1 실사용 중 확인:
    # "90-110만원/월"이 "최대 110만원"으로 표시됨). 범위 자체는
    # _extract_range_by_rules가 별도로 다룬다.
    range_spans = [m.span() for m in _RANGE_PATTERN.finditer(chunk_text)]
    for match in _AMOUNT_PATTERN.finditer(chunk_text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        head = chunk_text[max(0, match.start() - _NON_BENEFIT_WINDOW) : match.start()]
        if any(word in head for word in _NON_BENEFIT_CONTEXT):
            continue
        line_start = chunk_text.rfind("\n", 0, match.start()) + 1
        line_head = _WHITESPACE.sub("", chunk_text[line_start : match.start()])
        if any(label in line_head for label in _LIMIT_LABELS):
            blocked = True
            continue
        tail = _WHITESPACE.sub(
            "", chunk_text[match.end() : match.end() + _THRESHOLD_TAIL_WINDOW]
        )
        if tail.startswith(_THRESHOLD_TAIL):
            blocked = True
            continue
        digits = match.group(1).replace(",", "")
        unit = match.group(2) or ""
        multiplier = next(m for u, m in _AMOUNT_UNIT_MULTIPLIERS if u == unit)
        value = float(digits) * multiplier
        if value <= 0:
            continue
        if value not in candidates:
            candidates.append(value)

    if blocked:
        return None, (
            "금액처럼 보이는 값이 대출·보증 한도이거나 자격 문턱값이라 "
            "지원금으로 확정할 수 없음 (규칙 추출)"
        )
    if not candidates:
        if range_spans:
            return None, "원문이 범위로만 명시되어 단일 금액을 확정할 수 없음 (규칙 추출)"
        return None, "원문에 명시된 확정 금액이 없음 (규칙 추출)"
    if len(candidates) > 1:
        shown = ", ".join(f"{value:,.0f}원" for value in candidates[:5])
        return None, (
            f"원문에 금액이 여러 개 있어 단일 금액으로 확정할 수 없음 "
            f"(조건별 차등이거나 본인부담금이 섞였을 수 있음: {shown})"
        )
    return candidates[0], "원문에 명시된 금액을 규칙으로 추출 (LLM 미사용)"


def _extract_range_by_rules(chunk_text: str) -> tuple[float, float] | None:
    """'90-110만원'처럼 조건 구분 없는 범위(하한~상한)를 정규식으로 뽑는다
    (2026-09-11 추가).

    두 군데서 쓴다: (1) LLM을 못 쓰는 상황에서 오프라인 범위 표시,
    (2) LLM이 min_amount/max_amount를 줬을 때 원문에 실제로 범위
    표현이 있는지 교차 검증(_extract_amount_via_llm 참고) - LLM 혼자만의
    판단을 그대로 믿지 않는다.

    매치가 정확히 하나가 아니거나(없거나 여러 개), 소득/취업/장애/혼인/
    임신 등 조건을 가르는 단어가 근처에 있으면(조건별 차등을 범위로
    착각할 위험) ``None``을 돌려준다 - 안전한 쪽으로만 판단한다.
    """

    matches = list(_RANGE_PATTERN.finditer(chunk_text))
    if len(matches) != 1:
        return None
    match = matches[0]

    window_start = max(0, match.start() - _TIER_CONTEXT_WINDOW)
    window_end = min(len(chunk_text), match.end() + _TIER_CONTEXT_WINDOW)
    surrounding = chunk_text[window_start:match.start()] + chunk_text[match.end():window_end]
    if any(word in surrounding for word in _TIER_CONTEXT_KEYWORDS):
        return None

    low_digits = match.group(1).replace(",", "")
    low_unit = match.group(2) or ""
    high_digits = match.group(3).replace(",", "")
    high_unit = match.group(4) or ""
    # "90-110만원"처럼 앞쪽 숫자에 단위가 없으면 뒤쪽 단위를 공유한다.
    effective_low_unit = low_unit or high_unit
    low_multiplier = next(m for u, m in _AMOUNT_UNIT_MULTIPLIERS if u == effective_low_unit)
    high_multiplier = next(m for u, m in _AMOUNT_UNIT_MULTIPLIERS if u == high_unit)
    low_value = float(low_digits) * low_multiplier
    high_value = float(high_digits) * high_multiplier
    if low_value <= 0 or high_value <= 0 or low_value >= high_value:
        return None
    return (low_value, high_value)


def _extract_amount_via_llm(
    chunk_text: str, llm_client: LLMClient | None
) -> tuple[float | None, str, bool, tuple[float, float] | None]:
    """chunk 원문에서 LLM으로 "이미 명시된 금액"만 뽑아낸다 (계산/추측 금지).

    반환값의 세 번째 요소는 **LLM에게 실제로 물어봤고 답을 읽는 데 성공했는가**
    이다. 이걸 구분하는 이유: LLM이 "원문에 확정 금액이 없다"고 판단해서
    None을 준 것과, 애초에 물어보지 못한 것(미연결/호출 실패/파싱 실패)은
    전혀 다른 상황이다. 전자는 그 판단을 존중해야 하고, 후자일 때만 규칙
    경로로 넘어가야 한다.

    DRAFT(팀 확인 필요, 확정 전): 프롬프트/출력 스키마가 아직 설계 중이라
    아래는 임시다. LLM이 계산식이 필요한 규칙(예: 소득 구간별 차등)까지
    만나면 amount를 null로 두도록 프롬프트에서 강제한다 - 산술 로직은 아직
    이 함수에 없다(위 모듈 docstring의 미해결 사항 참고).
    """
    if llm_client is None:
        return None, "", False, None

    prompt = (
        "다음은 복지 정책의 지원내용 원문이다. 사용자가 실제로 받는 지원 "
        "금액을 구조화된 JSON으로만 추출하라. 절대 새로운 숫자를 계산하거나 "
        "추측하지 마라 - 원문에 명시된 확정 금액이 없으면(예: 소득 구간별로 "
        "달라지는 경우, 금액이 아예 언급되지 않는 경우) amount를 null로 "
        "둬라.\n\n"
        "'90-110만원'처럼 조건 구분 없이 범위(하한~상한)로만 적혀 있어 "
        "단일 금액을 하나로 고를 수 없는 경우에는, amount는 null로 두고 "
        "대신 min_amount/max_amount에 각각 '원' 단위 정수로 하한과 "
        "상한을 넣어라(예: '90-110만원' \u2192 min_amount: 900000, "
        "max_amount: 1100000). 소득 구간별 차등처럼 조건에 따라 달라지는 "
        "경우나 하한/상한 중 하나만 있는 문턱값 표현(예: '30만원 이상')은 "
        "min_amount/max_amount도 채우지 말고 둘 다 null로 둬라.\n\n"
        "amount/min_amount/max_amount는 항상 '원' 단위의 정수로 변환해서 "
        "반환하라 - 원문 표기를 그대로 베끼지 마라. 예: '20만원' \u2192 "
        "200000, '1억 5천만원' \u2192 150000000, '90만원' \u2192 900000 "
        "('90만원'을 90으로 반환하면 절대 안 된다).\n\n"
        "다음 두 종류는 지원금이 아니므로 amount를 null로 둬라(원문에 다른 "
        "확정 금액이 없다면 - 이 규칙은 해당 숫자를 후보에서 빼라는 뜻이지, "
        "남은 다른 숫자를 대신 골라도 된다는 뜻이 아니다):\n"
        "1. 대출·융자·보증 한도(예: \"융자 한도액 15억원\", \"보증한도 2억원\") "
        "- 빌릴 수 있는 상한이지 받는 돈이 아니다.\n"
        "2. 뒤에 '이상'이 붙어 자격을 가르는 문턱값(예: \"매출액 1억원 "
        "이상\", \"연간 판매액 120만원 이상인 자\") - 지원금이 아니라 신청 "
        "자격 기준이다. 단 \"OO원 이하/이내 지원\"처럼 지원 상한을 나타내는 "
        "표현은 정상적인 지원금이니 그대로 추출하라. 이 두 경우에는 "
        "min_amount/max_amount도 채우지 마라.\n\n"
        "reason은 반드시 한국어로만 작성하라 - 한자나 영어 단어를 섞지 마라.\n\n"
        '출력 형식(다른 텍스트 없이 이 JSON 하나만): '
        '{"amount": <숫자 또는 null>, "min_amount": <숫자 또는 null>, '
        '"max_amount": <숫자 또는 null>, "reason": "<한 줄 설명>"}\n\n'
        f"원문:\n{chunk_text}"
    )
    try:
        response = llm_client.complete(
            prompt,
            system="너는 복지 정책 원문에서 금액만 추출하는 도구다. 절대 계산하거나 추측하지 않는다.",
            max_tokens=_BENEFIT_CALC_MAX_NEW_TOKENS,
        )
    except LLMCallError:
        # 2026-09-11: 예외 메시지를 그대로 노출하지 않는다 - 사용자 요청.
        # 자세한 이유(_extract_tiered_rule_via_llm의 같은 수정 참고)는
        # 그 함수 쪽 주석에 적었다.
        return None, "계산 실패", False, None

    try:
        # 코드펜스나 앞뒤 설명이 붙어 나와도 JSON만 잘라서 읽는다. 예전에는
        # json.loads()를 그대로 써서, LLM이 제대로 답했는데도 파싱이 터져
        # 금액이 버려지는 일이 있었다.
        parsed = loads_json_object(response)
        amount = parsed.get("amount")
        reason = parsed.get("reason", "")
    except (ValueError, AttributeError, TypeError):
        # 2026-09-11: 원본 응답 일부를 그대로 노출하지 않는다 - 사용자 요청.
        return None, "계산 실패", False, None

    if isinstance(reason, str) and _looks_like_broken_korean(reason):
        # 2026-09-11: Qwen이 reason에 한자를 섞어 쓴 사례 확인 - 사용자에게
        # 그대로 보여주지 않는다(위 _looks_like_broken_korean 참고).
        reason = ""

    if amount is None:
        # 2026-09-11: '90-110만원'처럼 조건 구분 없는 범위는 min_amount/
        # max_amount로 표시할 수 있다 - 원문에 이미 있는 정보를 "확인
        # 필요"로 뭉개지 않는다. 둘 다 숫자이고 하한 < 상한일 때만 믿는다.
        #
        # 다만 LLM 혼자만의 판단은 믿지 않는다 - "소득 구간별 10-30만원
        # 차등"처럼 진짜 조건부인 걸 범위로 착각해 min/max를 채울 위험이
        # 있다(_extract_tiered_rule_via_llm이 먼저 걸러내지만, 그쪽도
        # LLM 판단이라 실패할 수 있다). 그래서 원문에 실제로 범위 형태
        # 표현이 있는지 규칙(_extract_range_by_rules)으로 교차 검증하고,
        # 검증에 실패하면(원문에서 범위 패턴을 못 찾으면) LLM이 준
        # min/max는 버린다 - 틀린 범위를 보여주느니 "확인 필요"로 남기는
        # 편이 안전하다.
        amount_range: tuple[float, float] | None = None
        min_amount = parsed.get("min_amount")
        max_amount = parsed.get("max_amount")
        if (
            isinstance(min_amount, (int, float))
            and not isinstance(min_amount, bool)
            and isinstance(max_amount, (int, float))
            and not isinstance(max_amount, bool)
            and float(min_amount) < float(max_amount)
            and _extract_range_by_rules(chunk_text) is not None
        ):
            amount_range = (float(min_amount), float(max_amount))
        if amount_range is not None:
            return (
                None,
                reason
                or "원문에 범위(하한~상한)로만 금액이 명시되어 단일 금액을 확정할 수 없음",
                True,
                amount_range,
            )
        return (
            None,
            reason or "LLM이 원문에서 확정 금액을 추출하지 못함(조건부이거나 명시 안 됨)",
            True,
            None,
        )
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return None, "계산 실패", False, None

    note = "LLM이 원문에서 추출한 금액"
    if reason:
        note += f" (근거: {reason})"
    return float(amount), note, True, None
