"""N10a: 지원금 계산에 필요한 추가 정보 요청 노드 (interrupt).

N10(benefit_calculator)이 원문에서 조건부/구간별 금액 규칙(소득구간·취업
상태·장애여부·혼인상태·임신출산상태에 따라 금액이 달라지는 경우)을 찾았지만,
그 규칙이 가리키는 슬롯 값을 아직 몰라서 금액을 확정하지 못했을 때 이 노드가
사용자에게 그 슬롯만 콕 집어 되묻는다.

N2/N3(slot_completeness_gate/request_missing_slots)와 같은 interrupt 패턴을
그대로 따른다 - 차이는 "자격 판정에 필요한 하드 게이트 슬롯"이 아니라 "이미
자격은 충족했고 금액 계산에만 필요한 소프트 슬롯"을 묻는다는 것뿐이다. 그래서
state["missing_slots"]가 아니라 별도 state["calc_missing_slots"]를 읽는다.
되묻기 상한은 state["slot_ask_counts"]를 N2/N3와 그대로 공유한다 - 같은
슬롯을 두 노드가 각자 무한정 물으면 안 되기 때문이다(benefit_calculator.py의
_select_tier_amount가 상한 도달 여부를 이미 확인하고 나서만
calc_missing_slots에 슬롯을 담으므로, 이 노드는 상한을 다시 검사하지 않고
그대로 믿는다).

재입력 라우팅(2026-09-09 변경 - 처음에는 N3와 똑같이 N1(slot_parser)로
돌아가게 했다가, "정책지원금 계산 중에는 처음부터 다시 돌지 않고 하던
작업에 이어서 진행해 달라"는 피드백을 받고 바꿨다): 여기서 되묻는
슬롯(marital_status/pregnancy_status)은 N4(정책 검색)의 필터 조건도,
N5~N8(근거 추출·검증)의 입력도 아니다 - N10(benefit_calculator)이 이미
"충족"으로 확정된 정책의 금액 구간을 고르는 데만 쓴다. 그래서 이 답변
때문에 N1부터 다시 돌면(N2 게이트, N4 벡터 검색, N5 claim 추출, N6/N7
문서·근거 검증까지 전부 재실행) 이미 끝난, 이 답변과 무관한 일을
그대로 다시 하는 셈이다. 대신 슬롯 파싱을 이 노드가 직접 하고
(``merge_calc_slot_answer`` - slot_parser.py가 쓰는 것과 같은
llm_gateway.extract_slots + slot_schema.is_valid_slot_value 조합을
재사용한다), N9(eligibility_verdict)로 바로 돌아간다(builder.py의
_await_calc_info_input/그래프 배선 참고). N9는 claim_plan을 다시 만들지
않고 doc_id로 좁힌 재확인 검색만 하므로(eligibility_verdict.py 모듈
docstring 참고) 비용이 작고, N9 -> N10/N11(E16/E17) 두 갈래를 그대로
다시 태워서 result_assembly가 두 결과를 모두 받고 조립하게 만든다 -
N10 하나만 다시 태우면 duplicate_benefit 쪽 edge가 이번 라운드에
발화하지 않아 result_assembly가 조립할 근거가 한쪽만 남는다.

건너뛰기 옵션(2026-09-08 추가): 지원금 계산은 원래 자동으로 진행되고,
추가 정보가 필요할 때만 이 노드가 되묻는다. 그런데 사용자가 그 정보를 굳이
주고 싶지 않을 수도 있다 - "정보를 몰라서" 뿐 아니라 "그냥 지금 있는
정보로만 결과를 보고 싶어서"도 있을 수 있다. 그래서 실제 값을 답하는 것
외에 "그냥 넘어가겠다"는 답도 인정한다(``is_calc_skip_response``). 건너뛰면
``apply_calc_skip``이 - ``merge_calc_slot_answer``가 이미 채운 값은 건드리지
않고 - 그래도 여전히 비어 있는 슬롯만 UNKNOWN 센티넬로 확정한다. N10이 다시
실행되면 _select_tier_amount가 UNKNOWN을 보고 다시 묻지 않고 그대로 계산
불가로 확정한다 - MAX_SLOT_ASKS 상한까지 기다리지 않고 첫 번째 되묻기에서
바로 끝낼 수 있게 하는 것이 이 옵션의 목적이다.

State 계약:
- 입력: ``calc_missing_slots``, ``calc_missing_choices``, ``slot_ask_counts``,
  ``slots``, ``calc_choice_answers``
- 출력: ``needs_input``, ``followup_question``, ``slot_ask_counts``, ``slots``,
  ``calc_choice_answers``

선택형(other_choice) 확장(2026-09-11 추가): benefit_calculator.py가 슬롯
하나로 표현되지 않는 "선택형" 조건부 규칙(예: 자연분만/제왕절개)을 만나면
``calc_missing_slots`` 대신(또는 같이) ``calc_missing_choices``에
``{"policy_id": str, "labels": list[str]}``를 채운다. 이 답변은 slot_schema에
미리 정의된 열거형이 아니고 정책마다 라벨이 달라 전역 ``slots``에 넣지
않는다 - 대신 ``calc_choice_answers``(``policy_id -> 매칭된 라벨``)에
정책별로 담는다. 매칭(자유 답변 -> 라벨)은 ``merge_calc_choice_answer``가
한다 - 부분 문자열 매칭을 먼저 시도하고, 애매하면 LLM에게 "주어진 라벨
중 하나만" 고르게 한다(새 라벨을 만들지 못하게 강제 - 추측 금지 원칙).
"""

from __future__ import annotations

from ...llm import LLMCallError, LLMClient
from ..llm_gateway import extract_slots
from ..slot_schema import SLOT_ENUMS, UNKNOWN, is_valid_slot_value
from ..state import GraphState, SlotState

# N3의 _SLOT_ASK_ITEMS(llm_gateway.py)는 하드 게이트 슬롯만 다룬다. 여기서
# 다루는 슬롯은 전부 소프트 슬롯(slot_schema.SOFT_SLOTS)이라 그 표에 없으므로
# 별도 표를 둔다 - 문구도 "맞춤 제도를 찾으려면"이 아니라 "정확한 금액을
# 계산하려면"이어야 맥락에 맞다.
_CALC_SLOT_LABELS: dict[str, str] = {
    "marital_status": "혼인 상태 (미혼/기혼/이혼/사별)",
    "pregnancy_status": "임신/출산 상태",
    "household_size": "가구원 수",
    "children_count": "자녀 수",
}
_UNKNOWN_CALC_SLOT_LABEL = "추가 정보"
_CALC_ASK_INTRO = "정확한 지원금액을 계산하려면 아래 정보가 필요해요."
_CALC_SKIP_NOTICE = (
    "모르시거나 말씀하기 어려우면 '모름'이라고 답하셔도 됩니다. "
    "이 경우 정확한 금액 대신 안내만 드려요."
)


def _policy_titles(state: GraphState) -> dict[str, str]:
    """이번 턴에 나온 정책들의 ``{policy_id: 제목}``.

    선택형(other_choice) 되묻기 질문에서 "이 정책은..."처럼 정책을
    구분해 보여주기 위한 것이다. duplicate_benefit.py의 같은 이름
    함수와 로직이 같다(청크 첫 줄이 제목, chunking.py가 붙인 prefix) -
    노드 모듈을 서로 독립적으로 유지하려고 각자 따로 둔다.
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


def generate_calc_followup_question(
    missing_fields: list[str],
    missing_choices: list[dict] | None = None,
    policy_titles: dict[str, str] | None = None,
) -> str:
    """지원금 계산에 필요한 슬롯/선택 옵션을 되묻는 문구를 만든다.

    request_calc_info_input만 호출하며, "실제로 부족한 경우에만 호출한다"는
    전제는 호출자가 검증한다(llm_gateway.generate_followup_question과 같은
    관례). missing_choices의 각 항목({"policy_id":.., "labels": [...]})은
    policy_titles로 정책명을 찾아 "[정책명] 어떤 방식에 해당하시나요?
    (라벨1 / 라벨2 중 선택)" 형태로 덧붙인다 - 정책이 여러 개일 때
    사용자가 어느 정책 얘기인지 구분할 수 있게 한다.
    """

    lines = [_CALC_ASK_INTRO]
    number = 0
    for field in missing_fields:
        number += 1
        lines.append(f"{number}. {_CALC_SLOT_LABELS.get(field, _UNKNOWN_CALC_SLOT_LABEL)}")
    for choice in missing_choices or []:
        number += 1
        policy_id = choice.get("policy_id", "")
        title = (policy_titles or {}).get(policy_id, policy_id)
        options = " / ".join(choice.get("labels") or [])
        lines.append(f"{number}. [{title}] 어떤 방식에 해당하시나요? ({options} 중 선택)")
    lines.append(_CALC_SKIP_NOTICE)
    return "\n".join(lines)


def request_calc_info_input(state: GraphState) -> dict:
    """지원금 계산에 필요한 슬롯/선택 옵션을 사용자에게 안내하고 재입력을
    요청한다."""

    missing = state.get("calc_missing_slots", [])
    missing_choices = state.get("calc_missing_choices", [])
    if not missing and not missing_choices:
        raise ValueError(
            "request_calc_info_input requires a non-empty calc_missing_slots"
            " or calc_missing_choices"
        )

    policy_titles = _policy_titles(state) if missing_choices else {}
    question = generate_calc_followup_question(
        list(missing), list(missing_choices), policy_titles
    )

    # 원본 dict을 in-place로 바꾸면 checkpointer가 든 과거 스냅샷까지
    # 오염된다(request_missing_slots.py와 같은 이유).
    ask_counts = dict(state.get("slot_ask_counts", {}))
    for field in missing:
        ask_counts[field] = ask_counts.get(field, 0) + 1
    for choice in missing_choices:
        key = f"choice:{choice.get('policy_id', '')}"
        ask_counts[key] = ask_counts.get(key, 0) + 1

    return {
        "needs_input": True,
        "followup_question": question,
        "slot_ask_counts": ask_counts,
    }


def merge_calc_slot_answer(
    resumed_user_input: str,
    missing_fields: list[str],
    existing_slots: SlotState,
    llm_client: LLMClient | None = None,
) -> SlotState:
    """되묻기 재개 답변에서 ``missing_fields``만 뽑아 ``existing_slots``에 병합한다.

    N1(slot_parser.parse_slots)이 재입력을 파싱하는 것과 같은 재료
    (``llm_gateway.extract_slots`` + ``slot_schema.is_valid_slot_value``)를
    쓰지만, 이 노드는 N1을 다시 거치지 않고 직접 파싱한다(모듈 docstring의
    "재입력 라우팅" 참고 - N9로 바로 돌아가려면 슬롯 파싱을 이 노드가
    끝내둬야 한다). N1과 달리 지역·나이·관심사 등은 다루지 않는다 - 이
    인터럽트가 묻는 것은 ``missing_fields``(marital_status/pregnancy_status
    등 지원금 계산용 소프트 슬롯)뿐이고, 사용자가 답변에 곁들인 다른 정보는
    (있어도) 이 노드의 책임이 아니다 - 그런 정보까지 반영하려면 N1을 다시
    거쳐야 하는데, 그게 바로 이 변경이 피하려는 전체 재실행이다.

    ``missing_fields``에 없는 필드는 추출 결과에 값이 있어도 무시한다.
    각 필드가 ``slot_schema.SLOT_ENUMS``에 있으면 열거형 계약을 통과한
    값만 받아들이고(fail-closed, slot_parser.py의 ``_ENUM_FIELDS`` 처리와
    동일), 없으면(예: household_size/children_count처럼 숫자 슬롯) 추출된
    값을 그대로 받아들인다(``_SCALAR_FIELDS`` 처리와 동일).
    """

    extracted = extract_slots(
        resumed_user_input,
        existing_slots,
        llm_client=llm_client,
        asked_slots=missing_fields,
    )

    merged: SlotState = dict(existing_slots)
    for field in missing_fields:
        value = extracted.get(field)
        if value is None:
            continue
        if field in SLOT_ENUMS:
            if is_valid_slot_value(field, value):
                merged[field] = value
        else:
            merged[field] = value

    return merged

def _match_choice_label(
    user_input: str, labels: list[str], llm_client: LLMClient | None = None
) -> str | None:
    """자유 답변을 주어진 라벨 목록 중 하나로 매칭한다. 확신 없으면 None.

    먼저 단순 부분 문자열 매칭을 시도한다(공백 제거, 대소문자 무시) -
    "제왕절개요"처럼 라벨을 그대로 포함한 답변은 이걸로 충분하고 LLM
    호출도 아낄 수 있다. 정확히 하나만 걸리면 그걸 쓰고, 0개 또는 2개
    이상 걸리면(애매함) 조용히 하나를 고르지 않고 LLM로 넘긴다.

    LLM 매칭도 주어진 라벨 중 하나만 그대로 고르게 강제한다 - 새 라벨을
    만들거나 라벨을 변형해서 답하지 못하게 프롬프트로 막는다(추측 금지
    원칙 - N10 계산 자체와 같은 원칙을 되묻기 답변 해석에도 적용한다).
    """
    normalized_input = user_input.replace(" ", "").casefold()
    substring_matches = [
        label for label in labels if label.replace(" ", "").casefold() in normalized_input
    ]
    if len(substring_matches) == 1:
        return substring_matches[0]

    if llm_client is None:
        return None

    options = "\n".join(f"- {label}" for label in labels)
    prompt = (
        "사용자의 답변이 아래 옵션 중 어느 것을 가리키는지 골라라. 반드시 "
        "아래 옵션 중 하나를 원문 그대로 출력하거나, 해당하는 옵션이 "
        "없으면 NONE만 출력하라. 다른 텍스트나 설명은 절대 출력하지 "
        "마라.\n\n"
        f"[옵션]\n{options}\n\n"
        f"[사용자 답변]\n{user_input}"
    )
    try:
        response = llm_client.complete(
            prompt,
            system=(
                "너는 사용자 답변을 주어진 옵션 중 하나로만 분류하는 도구다. "
                "새 옵션을 만들지 않는다."
            ),
        )
    except LLMCallError:
        return None

    matched = response.strip()
    return matched if matched in labels else None


def merge_calc_choice_answer(
    resumed_user_input: str,
    missing_choices: list[dict],
    existing_choice_answers: dict[str, str],
    llm_client: LLMClient | None = None,
) -> dict[str, str]:
    """되묻기 재개 답변을 선택형(policy_id별 라벨) 질문에 매칭해 병합한다.

    merge_calc_slot_answer(슬롯 하나짜리 값)와 짝을 이루는 함수다 - 다만
    여기서 매칭하는 값은 slot_schema에 미리 정의된 열거형이 아니라 이
    정책 문서에서 방금 추출된, 정책마다 다른 자유 라벨 집합이라 전역
    슬롯(``state["slots"]``)에 넣지 않고 별도 ``calc_choice_answers``에
    담는다.

    매칭에 실패한 정책은 결과에 넣지 않는다 - 그러면 _select_tier_amount가
    (재질문 상한 전이면) 다시 같은 질문을 하거나, 상한에 도달했으면
    "확인 불가"로 확정한다. 조용히 아무 라벨이나 골라 채우지 않는다.
    """
    merged = dict(existing_choice_answers)
    for choice in missing_choices:
        policy_id = choice.get("policy_id")
        labels = choice.get("labels") or []
        if not policy_id or not labels:
            continue
        matched = _match_choice_label(resumed_user_input, labels, llm_client=llm_client)
        if matched is not None:
            merged[policy_id] = matched
    return merged


# 사용자가 "정보를 안 주고 그냥 넘어가겠다"는 뜻으로 흔히 쓰는 표현.
# llm_gateway._DONT_KNOW_MARKERS("모름"류)를 포함하면서, 이 노드에서만
# 의미가 통하는 명시적 건너뛰기 표현("스킵", "생략", "그냥 계산" 등)도
# 더한다 - "모른다"와 "알지만 굳이 안 주고 싶다"는 다른 말이지만, 이
# 노드 입장에서는 둘 다 "이 슬롯 없이 진행"으로 같은 처리를 받는다.
_CALC_SKIP_MARKERS = (
    "스킵", "생략", "건너뛰", "넘어가", "패스",
    "필요없", "필요 없", "괜찮아요", "괜찮습니다", "됐어요", "됐습니다",
    "그냥 계산", "그냥 진행", "그냥 알려", "안 줄", "안 알려",
    "모름", "모르", "몰라", "글쎄", "말하기 싫", "밝히고 싶지", "비공개",
    "노코멘트",
)


def is_calc_skip_response(user_input: str) -> bool:
    """사용자가 계산에 필요한 추가 정보 제공을 건너뛰겠다고 답했는지 본다.

    llm_gateway._DONT_KNOW_MARKERS와 같은 한계를 그대로 안고 간다 - 단순
    부분 문자열 매칭이라 "모르겠지만 일단 알려주세요"처럼 반대 의도가
    섞인 문장도 건너뛰기로 잡힐 수 있다. 이 노드가 묻는 것은 지원금
    계산에만 쓰이는 좁은 질문(혼인 상태/임신 상태 등)이라 오탐의 영향이
    제한적이고, 상한(MAX_SLOT_ASKS)에 도달하면 어차피 같은 결과(UNKNOWN
    확정)로 수렴하므로 이 정도 근사는 받아들인다.
    """

    return any(marker in user_input for marker in _CALC_SKIP_MARKERS)


def apply_calc_skip(state: GraphState, update: dict, resumed_user_input: str) -> dict:
    """건너뛰기 응답이면, 그래도 여전히 비어 있는 슬롯만 UNKNOWN으로 확정한다.

    builder.py의 ``_await_calc_info_input``이 ``merge_calc_slot_answer``로
    실제 답변을 슬롯에 병합한 **다음** 호출한다 - 그래서 ``update["slots"]``
    를 기준으로 판단한다(``merge_calc_slot_answer``가 이미 채운 필드는 여기서
    UNKNOWN으로 덮어쓰지 않는다). 예를 들어 "혼인 상태는 기혼이고 나머지는
    모르겠어요"처럼 한 슬롯은 답하고 다른 슬롯은 건너뛰는 경우, marital_status는
    실제 답변이 남고 pregnancy_status만 UNKNOWN으로 확정된다.

    ``interrupt()`` 자체는 LangGraph 실행 컨텍스트 밖에서 단위 테스트할 수
    없으므로, 실제 판단 로직은 이렇게 별도 순수 함수로 빼서 ``interrupt()``
    없이도 검증할 수 있게 한다.

    건너뛰기가 아니거나, 물어볼 슬롯/선택 옵션이 없었거나, 병합 후 채울
    것이 이미 다 찼으면 ``update``를 그대로 돌려준다(변경 없음). 그
    외에는 ``update["slots"]``에 남은 빈 슬롯과 ``update["calc_choice_
    answers"]``에 아직 없는 정책 답변을 UNKNOWN으로 채운 새 딕셔너리를
    얹어 돌려준다 - 원본 ``state["slots"]``/``state["calc_choice_
    answers"]``를 in-place로 바꾸지 않는다(request_missing_slots.py와
    같은 이유로, checkpointer가 든 과거 스냅샷 오염을 막는다).

    선택형(other_choice)도 슬롯과 같은 UNKNOWN 센티넬을 재사용한다 -
    "물어봤지만 확인 못 함"이라는 의미는 슬롯이든 선택형이든 같고,
    _select_tier_amount가 이미 슬롯 쪽에서 UNKNOWN을 그렇게 해석하므로
    선택형에도 새 센티넬을 따로 만들지 않는다.
    """

    if not is_calc_skip_response(resumed_user_input):
        return update

    missing = state.get("calc_missing_slots") or []
    missing_choices = state.get("calc_missing_choices") or []
    if not missing and not missing_choices:
        return update

    changed = False

    updated_slots = dict(update.get("slots") or state.get("slots") or {})
    for field in missing:
        if updated_slots.get(field) is None:
            updated_slots[field] = UNKNOWN
            changed = True

    updated_choice_answers = dict(
        update.get("calc_choice_answers") or state.get("calc_choice_answers") or {}
    )
    for choice in missing_choices:
        policy_id = choice.get("policy_id")
        if policy_id and policy_id not in updated_choice_answers:
            updated_choice_answers[policy_id] = UNKNOWN
            changed = True

    if not changed:
        return update

    return {**update, "slots": updated_slots, "calc_choice_answers": updated_choice_answers}
