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

# 충돌 재확인 문장에 쓰는 짧은 슬롯 라벨. streamlit_ui/constants.py의
# SLOT_LABELS_KO와 같은 어휘를 쓰되, 그래프 노드가 UI 레이어를 import하면
# 안 되므로(레이어 위반) 이 파일에 따로 둔다. 하드 게이트 슬롯 5개 +
# household_types(2026-09-15, 지역 충돌 재확인을 다른 프로필 슬롯까지
# 확장하면서 추가) - employment_status/veteran_status는 여기 없다(각각
# "프로필 값 자체가 없어 충돌이 생길 수 없음"/"팀 결정으로 대화 중 재확인
# 대상에서 제외"라 slot_conflicts에 절대 나타나지 않는다, service.ask()
# docstring 참고).
_CONFLICT_FIELD_LABELS: dict[str, str] = {
    "region": "거주 지역",
    "gender": "성별",
    "birth_date": "생년월일",
    "income_bracket": "소득 수준",
    "disability_status": "장애 등록 여부",
    "household_types": "가구 유형",
}


def _conflict_sentence(field: str, conflict: dict[str, str]) -> str:
    label = _CONFLICT_FIELD_LABELS.get(field, field)
    return (
        f"회원 정보에는 {label}이(가) '{conflict['profile']}'로 돼 있는데, "
        f"방금은 '{conflict['chat']}'이라고 하셨어요. 어느 쪽이 맞는지 다시 "
        "알려주세요."
    )


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
    slot_conflicts = state.get("slot_conflicts") or {}
    # 충돌 재확인 대상인 슬롯은 번호 목록에서 뺀다 - 아래 충돌 문장과, 채팅
    # 값이 미리 선택된 폼 위젯으로 이미 설명되므로 번호 목록에 또 넣으면
    # 중복이다(request_missing_slots.py 문서 및 llm_gateway.generate_followup_
    # question 문서 참고).
    exclude_from_list = set(slot_conflicts) if slot_conflicts else None
    question = generate_followup_question(
        len(general_law_references), asked, exclude_from_list=exclude_from_list
    )

    # 회원 프로필 값과 이번 대화에서 말한 값이 달라 되묻는 경우엔, 그
    # 사실을 명시적으로 알려준다(2026-09-15 추가) - 그냥 "거주 지역이
    # 필요해요"만 보이면 "회원가입 때 이미 넣었는데 왜 또 묻지?"로 헷갈린다.
    # 여러 슬롯이 동시에 충돌하면(드물지만 한 턴에 지역·성별을 같이 정정하는
    # 경우) 문장을 여러 줄로 이어붙인다.
    conflict_sentences = [
        _conflict_sentence(field, slot_conflicts[field])
        for field in missing_slots
        if field in slot_conflicts
    ]
    if conflict_sentences:
        conflict_block = "\n".join(conflict_sentences)
        question = f"{conflict_block}\n\n{question}" if question else conflict_block

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
