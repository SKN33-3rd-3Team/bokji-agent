"""N3: 추가 정보 요청 노드 (interrupt).

N2가 하드 게이트 슬롯을 부족하다고 표시하면 이 노드가 사용자에게 직접
되묻는다. N2a가 채운 ``general_law_references``가 있으면 지역과 무관한 참고
법령 안내를 함께 붙인다.

지역이 부족한 턴에는 지역만 묻고, 지역이 확정된 뒤에 나머지 프로필 슬롯을
묻는다. 지역은 검색 자체가 성립하려면 반드시 필요한 유일한 슬롯이라
우선순위가 다르고, 여섯 항목을 한 번에 몰아 물으면 답변율이 떨어진다.

되묻는 슬롯마다 ``slot_ask_counts``를 올린다. N2는 이 횟수가 상한에 닿으면
해당 슬롯을 센티넬로 확정하고 진행하므로, 사용자가 답을 주지 않아도
N2 <-> N3 루프가 끝난다.

재입력은 이 노드가 아니라 N1로 라우팅한다(Edge E6 - 참고자료 "노드_Agent"
시트 N3 "확인 필요" 비고: 과거 설계와 다른 점). 실제 LangGraph
interrupt/checkpointer 연결은 그래프 조립 단계(후속 작업)의 책임이며, 이
노드는 ``needs_input``/``followup_question``/``slot_ask_counts``만 채운
partial state를 반환한다.

State 계약(참고자료 "State_연결부" 시트 E5/E6):
- 입력: ``missing_slots``, ``general_law_references``, ``slot_ask_counts``
- 출력: ``needs_input``, ``followup_question``, ``slot_ask_counts``
"""

from __future__ import annotations

from ..llm_gateway import generate_followup_question
from ..slot_schema import REGION_SLOT
from ..state import GraphState


def request_missing_slot_input(state: GraphState) -> dict:
    """부족한 슬롯을 사용자에게 안내하고 재입력을 요청한다."""

    missing_slots = state.get("missing_slots", [])
    if not missing_slots:
        raise ValueError(
            "request_missing_slot_input requires a non-empty missing_slots"
        )

    # 지역이 섞여 있으면 이번 턴은 지역만 묻는다.
    asked = [REGION_SLOT] if REGION_SLOT in missing_slots else list(missing_slots)

    general_law_references = state.get("general_law_references", [])
    question = generate_followup_question(len(general_law_references), asked)

    # 원본 dict을 in-place로 바꾸면 checkpointer가 든 과거 스냅샷까지
    # 오염된다(N1의 리스트 복사와 같은 이유).
    ask_counts = dict(state.get("slot_ask_counts", {}))
    for slot in asked:
        ask_counts[slot] = ask_counts.get(slot, 0) + 1

    return {
        "needs_input": True,
        "followup_question": question,
        "slot_ask_counts": ask_counts,
    }
