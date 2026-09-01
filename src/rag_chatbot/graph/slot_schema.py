"""슬롯 어휘와 게이트 등급, 나이 계산을 모은 모듈.

N1(추출)·N2(게이트)·N3(재질문)가 같은 어휘를 쓰게 하려고 한 곳에 모았다.
``rag_design.contracts``에 넣지 않은 이유는 그 파일이 수집·UI 팀과 공유하는
*문서* 계약이고, 여기 있는 값은 답변 그래프 내부의 *사용자 슬롯* 어휘라서다
(공용 파일 변경은 제안 -> 리뷰 절차가 필요하다 - ``CONTRIBUTING.md``).

등급 근거는 ``RAG설계_조건부_요소`` 참고자료의 A/B/C 등급을 따른다.
중요한 구분이 하나 있다.

- **하드 게이트(수집)**: 값이 없으면 N3가 사용자에게 되묻는 슬롯.
- **하드 필터(검색)**: N4가 vector 또는 raw sidecar 조건으로 쓰는 슬롯.

이 둘은 같지 않다. 참고자료는 소득·취업상태를 C등급(필터 금지)으로
분류하지만, 현재 N4는 공식 raw 지원조건의 exact code만 쓰고 결측·미매핑을
범주별 fail-open하는 조건으로 연결한다. 아래 ``FILTERABLE_SLOTS``가 이
보수적 검색 조건에 실제로 쓰는 슬롯 목록이다.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Mapping, TypedDict

# 사용자에게 물어봤지만 값을 얻지 못했을 때 쓰는 센티넬.
# ``None``(아직 안 물어봄)과 구분해야 Document Card의 "추출 실패 시 센티넬
# 처리 건수"(참고자료 §11)를 셀 수 있다.
UNKNOWN = "unknown"


class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"
    UNKNOWN = UNKNOWN


class DisabilityStatus(str, Enum):
    REGISTERED = "registered"
    NOT_REGISTERED = "not_registered"
    UNKNOWN = UNKNOWN


class EmploymentStatus(str, Enum):
    EMPLOYED = "employed"
    SELF_EMPLOYED = "self_employed"
    JOB_SEEKING = "job_seeking"
    STUDENT = "student"
    NOT_WORKING = "not_working"
    UNKNOWN = UNKNOWN


class IncomeBracket(str, Enum):
    """기준중위소득 대비 구간.

    금액(``월 250만원``)이 아니라 구간으로 받는다. 참고자료 C군 설명대로
    가구원 수·공제·재산환산 없이 금액에서 소득인정액을 계산할 수 없고,
    추정해서 필터를 걸면 정답이 조용히 사라지기 때문이다. 사용자가 금액만
    말하면 구간으로 변환하지 않고 ``None``으로 둔다.
    """

    UNDER_30 = "under_30"
    PCT_30_50 = "pct_30_50"
    PCT_50_75 = "pct_50_75"
    PCT_75_100 = "pct_75_100"
    PCT_100_150 = "pct_100_150"
    OVER_150 = "over_150"
    UNKNOWN = UNKNOWN


class MaritalStatus(str, Enum):
    SINGLE = "single"
    MARRIED = "married"
    DIVORCED = "divorced"
    BEREAVED = "bereaved"
    UNKNOWN = UNKNOWN


class PregnancyStatus(str, Enum):
    NONE = "none"
    PREGNANT = "pregnant"
    POSTPARTUM = "postpartum"
    UNKNOWN = UNKNOWN


class AgeSubject(str, Enum):
    """연령 조건이 누구를 가리키는지.

    참고자료 §8 세 번째 함정. 아동수당은 아동 나이가 기준이지만 신청자는
    보호자이고, 노인 돌봄도 마찬가지다. 이걸 구분하지 못하면 41세 학부모의
    "우리 아이 지원 뭐 있나요"에서 본인 나이 41로 필터가 걸려 아동 제도가
    전부 탈락한다.
    """

    SELF = "self"
    CHILD = "child"
    HOUSEHOLD_MEMBER = "household_member"
    UNKNOWN = UNKNOWN


class HouseholdType(str, Enum):
    """가구 구성 유형. 하나만 고르는 값이 아니라 여러 개가 겹칠 수 있다."""

    SINGLE_PERSON = "single_person"
    SINGLE_PARENT = "single_parent"
    GRANDPARENT = "grandparent"
    MULTICULTURAL = "multicultural"
    MULTI_CHILD = "multi_child"
    NORTH_KOREAN_DEFECTOR = "north_korean_defector"
    CARE_LEAVER = "care_leaver"
    FACILITY_LEAVER = "facility_leaver"


# ---------------------------------------------------------------------------
# 게이트 등급
# ---------------------------------------------------------------------------

# 지역은 별도로 다룬다. 값이 하나가 아니라 region_scope/region_names 쌍이고,
# 참고자료에서 유일하게 $or 하드 필터가 필요한 A등급 요소라서다.
REGION_SLOT = "region"

# 값이 없으면 N3가 되묻는 프로필 슬롯. 순서가 곧 질문 순서다.
PROFILE_HARD_GATE_SLOTS: tuple[str, ...] = (
    "birth_date",
    "gender",
    "income_bracket",
    "disability_status",
    "employment_status",
)

HARD_GATE_SLOTS: tuple[str, ...] = (REGION_SLOT, *PROFILE_HARD_GATE_SLOTS)

# 있으면 쓰고 없으면 그냥 넘어가는 슬롯. N2는 이 값을 검사하지 않는다.
SOFT_SLOTS: tuple[str, ...] = (
    "marital_status",
    "household_types",
    "pregnancy_status",
    "interests",
    "household_size",
    "children_count",
)

# N4가 구조화 검색 조건으로 쓰는 슬롯. 지역·연령은 vector store에, 소득·
# 취업상태는 정부24 raw 지원조건 sidecar 후처리에 연결한다(팀 결정).
#
# 참고자료 ``RAG설계_조건부_요소``는 소득·취업상태를 C등급(필터 금지)으로
# 두고 있다. 그 경고의 실체는 "필터를 걸면 정답이 조용히 사라진다"이므로,
# 필터로 연결하되 사라지는 경로를 막는 규칙을 함께 둔다
# (``resolve_filter_slots`` 참고).
#
# 1. 값이 ``UNKNOWN`` 센티넬이면 그 슬롯은 아예 필터로 만들지 않는다.
# 2. 문서 쪽 기준이 없는 제도는 필터에서 탈락시키지 않는다(문서 fail-open).
#    소득 기준이 없는 제도는 "소득 무관"이지 "불일치"가 아니다.
# 3. 소득 구간은 raw JA 구간과 경계가 겹치는지를 비교할 수 있게 순서값으로
#    만든다. 같은 범주의 복수 active code는 OR로 처리한다.
HARD_FILTER_SLOTS: frozenset[str] = frozenset(
    {REGION_SLOT, "birth_date", "income_bracket", "employment_status"}
)
# B등급. 추출에 확신이 있을 때만 적용하고, 실패하면 미적용한다.
SOFT_FILTER_SLOTS: frozenset[str] = frozenset({"gender", "disability_status"})
FILTERABLE_SLOTS: frozenset[str] = HARD_FILTER_SLOTS | SOFT_FILTER_SLOTS

# 소득 구간 경계 overlap 비교에 쓰는 순서. 값이 클수록 소득이 높다.
INCOME_BRACKET_ORDER: dict[str, int] = {
    IncomeBracket.UNDER_30.value: 0,
    IncomeBracket.PCT_30_50.value: 1,
    IncomeBracket.PCT_50_75.value: 2,
    IncomeBracket.PCT_75_100.value: 3,
    IncomeBracket.PCT_100_150.value: 4,
    IncomeBracket.OVER_150.value: 5,
}

SLOT_ENUMS: dict[str, type[Enum]] = {
    "age_subject": AgeSubject,
    "gender": Gender,
    "income_bracket": IncomeBracket,
    "disability_status": DisabilityStatus,
    "employment_status": EmploymentStatus,
    "marital_status": MaritalStatus,
    "pregnancy_status": PregnancyStatus,
}

# 같은 슬롯을 몇 번까지 되물을지. 이 횟수를 넘기면 센티넬(UNKNOWN)로 확정하고
# 진행한다. 규칙 기반 추출기는 사용자가 "말하기 싫어요"라고 답해도 값을 못
# 채우므로, 상한이 없으면 N2 <-> N3가 무한히 돈다.
MAX_SLOT_ASKS = 2

# 필터를 걸지 못한 이유.
SKIP_NOT_CONFIRMED = "not_confirmed"
SKIP_SUBJECT_NOT_SELF = "age_subject_not_self"

# 사람 나이로 받아들일 수 있는 상한. 수집·검색 계약과 같은 120으로 맞춘다.
MAX_PLAUSIBLE_AGE = 120


class FilterPlan(TypedDict):
    """N4에 넘기는 필터 계획.

    ``hard``는 반드시 적용하고, ``soft``는 추출 신뢰도가 낮으면 N4가 떨어뜨려도
    되는 조건이다. ``skipped``는 미확인이라 걸지 않은 슬롯 목록으로, Document
    Card의 센티넬 처리 건수 집계에 그대로 쓴다(참고자료 §11).
    """

    hard: dict[str, dict[str, object]]
    soft: dict[str, dict[str, object]]
    skipped: list[str]
    # 건너뛴 이유. "미확인이라 못 걸었다"와 "연령 주체가 본인이 아니라 걸면
    # 안 된다"는 다른 사건이고, 답변에서도 다르게 설명해야 한다.
    skipped_reasons: dict[str, str]


def is_valid_slot_value(field: str, value: object) -> bool:
    """열거형 슬롯 값이 계약에 있는 값인지 본다 (fail-closed)."""

    enum_type = SLOT_ENUMS.get(field)
    if enum_type is None:
        return False
    if not isinstance(value, str):
        return False
    try:
        enum_type(value)
    except ValueError:
        return False
    return True


def calculate_ages(birth_date: date, reference_date: date) -> tuple[int, int]:
    """``(만 나이, 연 나이)``를 함께 계산한다.

    둘 다 계산하는 이유는 참고자료 §8의 첫 번째 함정 때문이다. 복지제도는
    대부분 만 나이 기준이지만 일부 청년 정책은 출생연도(연 나이) 기준이라,
    하나만 들고 있으면 경계에 있는 사람이 조용히 탈락한다.

    만 나이는 생일이 지났는지로 계산한다. 2월 29일생은 윤년이 아닌 해에
    3월 1일부터 나이를 먹는 것으로 본다(튜플 비교의 자연스러운 결과이자
    민법 해석과 같다).
    """

    had_birthday = (reference_date.month, reference_date.day) >= (
        birth_date.month,
        birth_date.day,
    )
    year_age = reference_date.year - birth_date.year
    return year_age - (0 if had_birthday else 1), year_age


def parse_birth_date(value: str | None, reference_date: date | None = None) -> date | None:
    """ISO 문자열을 실제 날짜로 바꾼다. 미래·비현실적 날짜는 거부한다.

    검증을 여기 한 곳에 모은 이유는, 같은 판정을 추출(N1)과 게이트(N2)가
    따로 하면 서로 어긋나기 때문이다. 게이트는 "값이 있다"로 통과시켰는데
    필터 조립은 "날짜가 아니다"로 건너뛰면, 연령 조건 없이 검색이 진행되면서
    아무도 그 사실을 모른다.

    미래 날짜와 상한을 넘는 나이는 거부한다. 오타 하나가 그대로 만 나이가
    되어 자격 판정을 뒤집는 것보다, 값이 없어서 한 번 더 묻는 쪽이 안전하다
    (fail-closed).
    """

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None

    today = reference_date or date.today()
    if parsed > today:
        return None
    if calculate_ages(parsed, today)[0] > MAX_PLAUSIBLE_AGE:
        return None
    return parsed


def resolve_filter_slots(
    slots: Mapping[str, object], *, reference_date: date | None = None
) -> FilterPlan:
    """확정된 슬롯을 N4가 쓸 필터 계획으로 바꾼다.

    이 함수가 "하드 게이트로 모은 값"과 "검색에 실제로 거는 조건" 사이의
    유일한 통로다. N4가 ``slots``를 직접 읽어 vector/sidecar 조건을 만들면
    아래 안전장치를 우회하게 되므로, 필터는 반드시 여기를 거쳐 만든다.

    안전장치 세 가지:

    1. ``UNKNOWN`` 센티넬과 빈 값은 필터로 만들지 않는다. "모른다"는
       "해당하지 않는다"가 아니다.
    2. ``allow_missing``으로 문서 쪽 기준이 없는 제도를 살려 둔다. 소득
       기준이 적혀 있지 않은 제도는 소득 무관이지 불일치가 아니다.
    3. 기존 FilterPlan 키 ``max_bracket_rank``는 호환을 위해 유지한다. N4
       sidecar는 이 값을 사용자 구간의 순서값으로 읽어 JA 구간 경계와 겹쳐
       비교하며, 문자열 등가 비교는 하지 않는다.

    나이는 ``birth_date`` 자체가 아니라 거기서 파생한 만 나이·연 나이를
    넘긴다. 제도 문서의 ``age_basis``에 따라 어느 쪽을 쓸지 N4/N9가 고르며,
    불명확하면 참고자료 §8대로 둘 다 만족하는 쪽으로 넓게 매칭한다.
    """

    hard: dict[str, dict[str, object]] = {}
    soft: dict[str, dict[str, object]] = {}
    skipped: list[str] = []
    skipped_reasons: dict[str, str] = {}

    for field in sorted(FILTERABLE_SLOTS):
        condition, reason = _build_condition(
            field, slots, reference_date=reference_date
        )
        target = hard if field in HARD_FILTER_SLOTS else soft
        if condition is None:
            skipped.append(field)
            skipped_reasons[field] = reason or SKIP_NOT_CONFIRMED
            continue
        target[field] = condition

    return {
        "hard": hard,
        "soft": soft,
        "skipped": skipped,
        "skipped_reasons": skipped_reasons,
    }


def _build_condition(
    field: str,
    slots: Mapping[str, object],
    *,
    reference_date: date | None = None,
) -> tuple[dict[str, object] | None, str | None]:
    """슬롯 하나를 필터 조건으로 바꾼다. 걸 수 없으면 ``(None, 이유)``."""

    if field == REGION_SLOT:
        names = slots.get("region_names") or []
        if not isinstance(names, list) or not names:
            return None, SKIP_NOT_CONFIRMED
        # 광역·기초 제도가 중첩 유효하므로 계층 전체를 $or로 넘긴다
        # (참고자료 D군 - 주소지가 $or를 요구하는 유일한 A등급 요소).
        return {"any_of": list(names), "allow_missing": False}, None

    if field == "birth_date":
        return _build_age_condition(slots, reference_date=reference_date)

    value = slots.get(field)
    if not is_valid_slot_value(field, value) or value == UNKNOWN:
        # 미확인은 필터로 만들지 않는다. 여기서 걸면 정답이 조용히 사라진다.
        return None, SKIP_NOT_CONFIRMED

    if field == "income_bracket":
        return {
            "max_bracket_rank": INCOME_BRACKET_ORDER[str(value)],
            "allow_missing": True,
        }, None

    return {"equals": value, "allow_missing": True}, None


def _build_age_condition(
    slots: Mapping[str, object],
    *,
    reference_date: date | None = None,
) -> tuple[dict[str, object] | None, str | None]:
    """연령 조건을 만든다. 주체가 본인으로 확정될 때만 만든다.

    참고자료 §8이 요구하는 안전장치다. 아동수당은 아동 나이가 기준인데
    신청자는 보호자이고, 노인 돌봄도 마찬가지다. 연령 주체를 확정하지 못한
    채 신청자 나이로 필터를 걸면, 41세 학부모의 "우리 아이 지원 뭐 있나요"
    에서 아동 제도가 전부 탈락한다.

    그래서 주체가 본인이 아니거나 불명확하면 연령 필터를 통째로 생략한다.
    검색이 넓어지는 것은 되돌릴 수 있지만, 탈락한 제도는 되돌릴 수 없다.
    """

    subject = slots.get("age_subject")
    if subject != AgeSubject.SELF.value:
        return None, SKIP_SUBJECT_NOT_SELF

    # 파생값(age)만 보고 조건을 만들면, 생년월일이 깨진 상태에서 예전 턴에
    # 계산해 둔 나이로 필터가 걸린다. 게이트와 같은 판정 함수로 근거부터
    # 확인한다.
    if parse_birth_date(slots.get("birth_date"), reference_date) is None:
        return None, SKIP_NOT_CONFIRMED
    age = slots.get("age")
    year_age = slots.get("age_year_based")
    if not isinstance(age, int) or not isinstance(year_age, int):
        return None, SKIP_NOT_CONFIRMED
    return {"age": age, "age_year_based": year_age, "allow_missing": True}, None
