"""N3: 추가 정보 요청 노드 (interrupt).

N2가 하드 게이트 슬롯을 부족하다고 표시하면 이 노드가 사용자에게 직접
되묻는다. N2a가 채운 ``general_law_references``가 있으면 지역과 무관한 참고
법령 안내를 함께 붙인다.

부족한 항목은 지역을 포함해 **한 번에 모아서** 묻는다(2026-08-31 변경).
예전에는 지역이 부족하면 지역만 먼저 묻고 그다음 턴에 프로필 슬롯을
물었는데(지역이 검색 성립의 전제라는 이유), 실제로 돌려보니 되묻기 왕복이
두 배로 늘어 대화가 길어지고 슬롯별 되묻기 상한(``MAX_SLOT_ASKS``)에 먼저
닿아 값이 ``unknown``으로 확정돼버리는 문제가 더 컸다.

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
from ..state import GraphState


def request_missing_slot_input(state: GraphState) -> dict:
    """부족한 슬롯을 사용자에게 안내하고 재입력을 요청한다."""

    missing_slots = state.get("missing_slots", [])
    if not missing_slots:
        raise ValueError(
            "request_missing_slot_input requires a non-empty missing_slots"
        )

    # 부족한 항목을 전부 한 번에 묻는다(지역 포함).
    asked = list(missing_slots)

    general_law_references = state.get("general_law_references", [])
    region_conflict = state.get("region_conflict")
    # 충돌 재확인일 때는 지역을 번호 목록에서 뺀다 - 아래 충돌 문장과, 채팅
    # 값이 미리 선택된 폼 위젯으로 이미 설명되므로 번호 목록에 또 넣으면
    # 중복이다(request_missing_slots.py 문서 및 llm_gateway.generate_followup_
    # question 문서 참고).
    exclude_from_list = {"region"} if region_conflict else None
    question = generate_followup_question(
        len(general_law_references), asked, exclude_from_list=exclude_from_list
    )

    # 회원 프로필 지역과 이번 대화에서 말한 지역이 달라 되묻는 경우엔, 그
    # 사실을 명시적으로 알려준다(2026-09-15 추가) - 그냥 "거주 지역이
    # 필요해요"만 보이면 "회원가입 때 이미 넣었는데 왜 또 묻지?"로 헷갈린다.
    if region_conflict and "region" in missing_slots:
        conflict_sentence = (
            f"회원 정보에는 거주 지역이 '{region_conflict['profile']}'로 "
            f"돼 있는데, 방금은 '{region_conflict['chat']}'이라고 하셨어요. "
            "어느 지역 기준으로 알아볼지 다시 알려주세요."
        )
        question = f"{conflict_sentence}\n\n{question}" if question else conflict_sentence

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
