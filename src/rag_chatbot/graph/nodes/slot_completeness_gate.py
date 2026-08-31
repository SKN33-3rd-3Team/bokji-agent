"""N2: 적합성 체크 노드 (지역 하드 게이트).

``age``/``interests``/``household_size``/``children_count``는 소프트
조건이라 이 노드에서 값을 검사하지 않는다(참고자료 "노드_Agent" 시트 N2
역할). ``region``만 하드 게이트로 판정해, 지역별 보조금 검색(N4)으로 보낼지
지역이 없어 참고 법령 검색(N2a) 경로로 보낼지 결정한다.

이 노드 자체는 그래프 분기를 만들지 않는다. 실제 conditional edge는 그래프
조립 단계(후속 작업)에서 ``route_after_slot_completeness``가 반환하는
라우팅 키를 사용한다.

State 계약(참고자료 "State_연결부" 시트 E2/E3/E4):
- 입력: ``slots``
- 출력: ``missing_slots`` (부족하면 ``["region"]``, 충분하면 ``[]``)
"""

from __future__ import annotations

from typing import Literal

from rag_design.contracts import RegionScope

from ..state import GraphState

RouteKey = Literal["sufficient", "insufficient"]
_SUFFICIENT: RouteKey = "sufficient"
_INSUFFICIENT: RouteKey = "insufficient"


def check_slot_completeness(state: GraphState) -> dict:
    """지역 슬롯이 확정됐는지만 하드 게이트로 판정한다."""

    slots = state.get("slots", {})

    # 하드 게이트는 fail-closed로 판정한다. 계약에 없는 값이나 빈 문자열을
    # "충분"으로 통과시키면 지역이 확정되지 않은 채 N4 검색으로 넘어간다.
    try:
        scope = RegionScope(slots.get("region_scope"))
    except ValueError:
        return {"missing_slots": ["region"]}

    if scope is RegionScope.UNKNOWN:
        return {"missing_slots": ["region"]}
    # scope는 regional인데 region_names가 비어 있는 불일치 상태도 지역이
    # 확정되지 않은 것으로 본다(계약상 regional은 비어 있을 수 없다).
    if scope is RegionScope.REGIONAL and not slots.get("region_names"):
        return {"missing_slots": ["region"]}
    return {"missing_slots": []}


def route_after_slot_completeness(state: GraphState) -> RouteKey:
    """N2 이후 conditional edge(E3/E4)가 사용할 라우팅 키를 반환한다."""

    return _INSUFFICIENT if "region" in state.get("missing_slots", []) else _SUFFICIENT
