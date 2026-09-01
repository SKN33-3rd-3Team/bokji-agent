"""N2: 적합성 체크 노드 (다중 슬롯 하드 게이트).

지역만 검사하던 게이트를 프로필 슬롯까지 확대했다. 하드 게이트 대상은
``slot_schema.HARD_GATE_SLOTS``이고, ``marital_status``/``household_types``/
``pregnancy_status``/``interests``/``household_size``/``children_count``는
소프트 슬롯이라 여기서 검사하지 않는다.

여기서 모은 값은 검색 필터까지 연결된다. 지역·나이·소득·취업상태는 하드
필터, 성별·장애여부는 소프트 필터다(``slot_schema.HARD_FILTER_SLOTS`` /
``SOFT_FILTER_SLOTS``). 다만 슬롯 값을 그대로 ``where``에 넣지는 않는다.
참고자료 ``RAG설계_조건부_요소``가 소득을 C등급(필터 금지)으로 둔 이유가
"필터를 걸면 정답이 조용히 사라진다"이므로, 필터 조립은 반드시
``slot_schema.resolve_filter_slots``를 거쳐 미확인 값 제외·문서 fail-open·
소득 상한 비교 규칙을 적용한 뒤에 한다.

되묻기에는 상한이 있다. 규칙 기반 추출기는 사용자가 "말하기 싫어요"라고
답해도 값을 못 채우므로, 상한이 없으면 N2 <-> N3가 무한히 돈다. 상한에 닿은
슬롯은 ``"unknown"`` 센티넬로 확정하고 진행한다. 그 결과 게이트를 통과한
뒤에는 프로필 하드 게이트 슬롯이 ``None``으로 남지 않는다.

이 노드 자체는 그래프 분기를 만들지 않는다. 실제 conditional edge는 그래프
조립 단계(후속 작업)에서 ``route_after_slot_completeness``가 반환하는
라우팅 키를 사용한다.

State 계약(참고자료 "State_연결부" 시트 E2/E3/E4):
- 입력: ``slots``, ``slot_ask_counts``
- 출력: ``missing_slots``, ``slots``(포기한 슬롯의 센티넬 반영),
  ``region_fallback_applied``(지역을 끝내 못 받아 전국 범위로 좁혔을 때)
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from rag_design.contracts import NATIONAL_REGION_NAME, RegionScope

from ..slot_schema import (
    MAX_SLOT_ASKS,
    PROFILE_HARD_GATE_SLOTS,
    REGION_SLOT,
    UNKNOWN,
    is_valid_slot_value,
    parse_birth_date,
)
from ..state import GraphState, SlotState

RouteKey = Literal["sufficient", "insufficient"]
_SUFFICIENT: RouteKey = "sufficient"
_INSUFFICIENT: RouteKey = "insufficient"


def check_slot_completeness(state: GraphState) -> dict:
    """하드 게이트 슬롯이 모두 확정됐는지 판정한다."""

    slots: SlotState = state.get("slots", {})
    ask_counts = state.get("slot_ask_counts", {})
    reference_date = state.get("as_of")
    if reference_date is not None and type(reference_date) is not date:
        raise ValueError("state['as_of'] must be a date")

    missing: list[str] = []
    updates: dict[str, object] = {}
    region_fallback = False

    if not _has_region(slots):
        if ask_counts.get(REGION_SLOT, 0) >= MAX_SLOT_ASKS:
            # 지역은 센티넬로 둘 수 없다. 검색 자체가 지역 필터를 요구하므로
            # "미확인"으로 진행하면 조건 없는 전량 검색이 된다. 그래서 전국
            # 단위 제도로 범위를 좁혀 답하고, 그 사실을 상태에 남긴다.
            # 계속 되묻는 것보다 "전국 제도만 안내했다"고 밝히는 쪽이 낫다.
            updates["region_scope"] = RegionScope.NATIONAL.value
            updates["region_names"] = [NATIONAL_REGION_NAME]
            region_fallback = True
        else:
            missing.append(REGION_SLOT)

    for field in PROFILE_HARD_GATE_SLOTS:
        if _has_profile_value(slots, field, reference_date=reference_date):
            continue
        if ask_counts.get(field, 0) >= MAX_SLOT_ASKS:
            # 물어볼 만큼 물어봤다. 계속 되묻는 대신 "미확인"으로 확정한다.
            # 미확인 값은 필터로 쓰지 않고, 답변에서 "문서에서 확인되지 않음"
            # 과 같은 방식으로 그대로 드러난다.
            updates[field] = UNKNOWN
            continue
        missing.append(field)

    result: dict = {"missing_slots": missing}
    if updates:
        result["slots"] = {**slots, **updates}
    if region_fallback:
        result["region_fallback_applied"] = True
    return result


def _has_region(slots: SlotState) -> bool:
    """지역 슬롯이 확정됐는지 본다 (fail-closed).

    계약에 없는 값이나 빈 문자열을 "충분"으로 통과시키면 지역이 확정되지
    않은 채 N4 검색으로 넘어간다.
    """

    try:
        scope = RegionScope(slots.get("region_scope"))
    except ValueError:
        return False
    if scope is RegionScope.UNKNOWN:
        return False
    # scope는 regional인데 region_names가 비어 있는 불일치 상태도 지역이
    # 확정되지 않은 것으로 본다(계약상 regional은 비어 있을 수 없다).
    if scope is RegionScope.REGIONAL and not slots.get("region_names"):
        return False
    return True


def _has_profile_value(
    slots: SlotState, field: str, *, reference_date: date | None = None
) -> bool:
    """프로필 슬롯이 채워졌는지 본다.

    ``birth_date``는 열거형이 아니라 N1이 이미 실제 날짜인지 검증한 ISO
    문자열이므로 존재 여부만 본다. 나머지는 계약에 있는 열거형 값일 때만
    채워진 것으로 인정한다.
    """

    value = slots.get(field)
    if field == "birth_date":
        # 존재 여부만 보면 안 된다. 게이트는 "값이 있다"로 통과시켰는데 필터
        # 조립(resolve_filter_slots)은 "날짜가 아니다"로 연령 조건을 건너뛰면,
        # 나이 조건 없이 검색이 진행되는데 아무도 그 사실을 모른다. 두 곳이
        # 같은 판정 함수를 쓰게 한다 - 회귀 방지.
        return parse_birth_date(value, reference_date) is not None
    return is_valid_slot_value(field, value)


def route_after_slot_completeness(state: GraphState) -> RouteKey:
    """N2 이후 conditional edge(E3/E4)가 사용할 라우팅 키를 반환한다."""

    return _INSUFFICIENT if state.get("missing_slots") else _SUFFICIENT


def needs_general_law_reference(state: GraphState) -> bool:
    """N2a(일반 법령 참고 검색)를 실행할지 알려준다.

    N2a는 "지역을 몰라서 지역별 제도를 못 찾는 동안 지역과 무관한 법률이라도
    안내한다"는 노드다(참고자료 "노드_Agent" 시트 N2a). 그러므로 부족한 것이
    성별·소득처럼 지역이 아닌 슬롯일 때는 실행할 이유가 없다. 게이트가
    다중 슬롯으로 바뀌면서 "부족 == 지역 부족"이 더 이상 성립하지 않아
    별도 헬퍼로 분리했다.
    """

    return REGION_SLOT in state.get("missing_slots", [])
