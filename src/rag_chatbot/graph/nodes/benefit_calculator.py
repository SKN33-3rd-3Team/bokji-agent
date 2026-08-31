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

import re
from collections import defaultdict

from rag_design.contracts import EvidenceStatus, SourceType
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
)

from ...llm import LLMCallError, LLMClient, loads_json_object
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
    slots = state.get("slots") or {}
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
        if structured_amount is None:
            amount, note = _resolve_amount_without_metadata(chunk.text, llm_client)
        else:
            amount = float(structured_amount)
            note = "재검색한 chunk metadata의 구조화 금액 필드를 그대로 사용"

        amounts.append(
            _build_benefit_amount(
                policy_id=policy_id,
                amount=amount,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
                note=note,
                slots=slots,
            )
        )

    return {"benefit_amounts": amounts}


def _build_benefit_amount(
    *,
    policy_id: str,
    amount: float | None,
    chunk_id: str,
    chunk_text: str,
    note: str,
    slots: dict,
) -> BenefitAmount:
    """금액에 성격(주기/한도/지급 단위)과 총액 산술을 붙여 BenefitAmount를 만든다.

    금액을 못 구한 경우에도 같은 모양을 유지한다 - 화면에서 필드 유무를
    따로 분기하지 않아도 되게 하기 위함.
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
    }
    if amount is None:
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
) -> tuple[float | None, str]:
    """구조화 금액 필드가 없을 때 금액을 정한다: LLM 우선, 규칙은 보조.

    LLM을 먼저 쓰는 이유는 "월 20만원 지원"과 "본인부담금 5만원"처럼 문맥으로
    구분해야 하는 숫자를 규칙보다 잘 가리기 때문이다. 다만 LLM이 **판단해서**
    금액이 없다고 한 경우에는 그 판단을 존중하고 규칙으로 뒤집지 않는다 -
    규칙으로 덮어쓰면 조건부 금액을 확정 금액인 것처럼 만들 수 있다.

    규칙 경로로 넘어가는 것은 LLM에게 물어보지 못했을 때뿐이다(미연결,
    호출 실패, 응답 파싱 실패). 이때는 아무 금액도 못 주는 것보다 원문에
    명시된 단일 금액이라도 뽑아주는 편이 낫고, 어디서 나온 값인지
    calculation_note에 분명히 남긴다.
    """

    llm_amount, llm_note, consulted = _extract_amount_via_llm(chunk_text, llm_client)
    if consulted:
        return llm_amount, llm_note

    rule_amount, rule_note = _extract_amount_by_rules(chunk_text)
    if llm_note:
        # LLM을 붙였는데 실패한 경우 - 그 사실을 숨기지 않는다.
        return rule_amount, f"{rule_note} / {llm_note}"
    return rule_amount, f"{rule_note} (LLM 미연결)"


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


def _extract_amount_by_rules(chunk_text: str) -> tuple[float | None, str]:
    """원문에 명시된 금액을 정규식으로 뽑는다. 계산하지 않는다.

    LLM을 못 쓰는 상황(미연결/호출 실패)의 보조 경로다. 확정적인 단일 금액이
    보일 때만 값을 돌려주고, 애매하면 사유를 남기고 ``None``을 돌려준다.
    """

    candidates: list[float] = []
    for match in _AMOUNT_PATTERN.finditer(chunk_text):
        head = chunk_text[max(0, match.start() - _NON_BENEFIT_WINDOW) : match.start()]
        if any(word in head for word in _NON_BENEFIT_CONTEXT):
            continue
        digits = match.group(1).replace(",", "")
        unit = match.group(2) or ""
        multiplier = next(m for u, m in _AMOUNT_UNIT_MULTIPLIERS if u == unit)
        value = float(digits) * multiplier
        if value <= 0:
            continue
        if value not in candidates:
            candidates.append(value)

    if not candidates:
        return None, "원문에 명시된 확정 금액이 없음 (규칙 추출)"
    if len(candidates) > 1:
        shown = ", ".join(f"{value:,.0f}원" for value in candidates[:5])
        return None, (
            f"원문에 금액이 여러 개 있어 단일 금액으로 확정할 수 없음 "
            f"(조건별 차등이거나 본인부담금이 섞였을 수 있음: {shown})"
        )
    return candidates[0], "원문에 명시된 금액을 규칙으로 추출 (LLM 미사용)"


def _extract_amount_via_llm(
    chunk_text: str, llm_client: LLMClient | None
) -> tuple[float | None, str, bool]:
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
        return None, "", False

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
        return None, f"LLM 규칙 추출 호출 실패: {exc}", False

    try:
        # 코드펜스나 앞뒤 설명이 붙어 나와도 JSON만 잘라서 읽는다. 예전에는
        # json.loads()를 그대로 써서, LLM이 제대로 답했는데도 파싱이 터져
        # 금액이 버려지는 일이 있었다.
        parsed = loads_json_object(response)
        amount = parsed.get("amount")
        reason = parsed.get("reason", "")
    except (ValueError, AttributeError, TypeError):
        return (
            None,
            f"LLM 응답을 JSON으로 파싱하지 못함 (추측 금지, 원본 미신뢰): {response[:200]!r}",
            False,
        )

    if amount is None:
        return (
            None,
            reason or "LLM이 원문에서 확정 금액을 추출하지 못함(조건부이거나 명시 안 됨)",
            True,
        )
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return None, f"LLM이 숫자가 아닌 amount를 반환함 (신뢰하지 않음): {amount!r}", False

    note = "LLM이 원문에서 추출한 금액"
    if reason:
        note += f" (근거: {reason})"
    return float(amount), note, True
