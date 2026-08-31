"""N3: 추가 정보 요청 노드 (interrupt).

N2가 지역 정보를 하드 게이트로 판정해 부족하다고 표시하면 이 노드가
사용자에게 지역을 직접 되묻는다. N2a가 채운 ``general_law_references``가
있으면 지역과 무관한 참고 법령 안내를 함께 붙인다.

재입력은 이 노드가 아니라 N1로 라우팅한다(Edge E6 - 참고자료 "노드_Agent"
시트 N3 "확인 필요" 비고: 과거 설계와 다른 점). 실제 LangGraph
interrupt/checkpointer 연결은 그래프 조립 단계(후속 작업)의 책임이며, 이
노드는 ``needs_input``/``followup_question``만 채운 partial state를
반환한다.

State 계약(참고자료 "State_연결부" 시트 E5/E6):
- 입력: ``missing_slots``, ``general_law_references``
- 출력: ``needs_input``, ``followup_question``
"""

from __future__ import annotations

from ..llm_gateway import generate_followup_question
from ..state import GraphState


def request_missing_region_input(state: GraphState) -> dict:
    """지역 슬롯 부족을 사용자에게 직접 안내하고 재입력을 요청한다."""

    missing_slots = state.get("missing_slots", [])
    if "region" not in missing_slots:
        raise ValueError(
            "request_missing_region_input requires missing_slots to include 'region'"
        )

    general_law_references = state.get("general_law_references", [])
    question = generate_followup_question(len(general_law_references))
    return {"needs_input": True, "followup_question": question}
