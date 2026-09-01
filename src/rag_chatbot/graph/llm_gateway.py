"""N1·N3처럼 LLM 호출이 필요한 노드가 사용하는 단일 진입점.

실제 LLM provider는 아직 팀이 정하지 않았다(``docs/RAG_DESIGN_PLAN.md``의
"생성" 항목 참고, requirements 파일에도 LLM SDK가 없음). 노드 파일이 이
모듈을 거쳐서만 LLM을 호출하게 분리해 두면, provider가 정해진 뒤에는 이
파일의 구현부만 교체하면 되고 노드 시그니처(``def <동사_명사>(state:
GraphState) -> dict:``)는 바뀌지 않는다.

지금 구현은 실제 자연어 이해가 아니라 규칙 기반 placeholder다. 출력
스키마는 참고자료 ``bokji_agent_graph_design.xlsx``의 "프롬프트" 시트
정의를 따른다.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Mapping, Sequence, TypedDict

from rag_design.contracts import CANONICAL_SIDO_NAMES

from .slot_schema import (
    REGION_SLOT,
    AgeSubject,
    DisabilityStatus,
    EmploymentStatus,
    Gender,
    HouseholdType,
    IncomeBracket,
    MaritalStatus,
    PregnancyStatus,
    parse_birth_date,
)

# 규칙표의 형태: (열거형 값, 단독 트리거, 마커 필요 트리거)
_RuleTable = tuple[tuple[Enum, tuple[str, ...], tuple[str, ...]], ...]


class ExtractedSlots(TypedDict, total=False):
    birth_date: str | None
    age_subject_signals: dict[str, bool]
    age_self_reported: int | None
    region_raw: str | None
    gender: str | None
    income_bracket: str | None
    disability_status: str | None
    employment_status: str | None
    marital_status: str | None
    household_types: list[str]
    pregnancy_status: str | None
    interests: list[str]
    household_size: int | None
    children_count: int | None


# 이메일/전화번호/주민등록번호처럼 보이는 패턴은 슬롯으로 추출하기 전에
# 미리 지운다(프롬프트 규칙: "이메일·전화번호·ID는 절대 추출하지 않는다").
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_PATTERN = re.compile(r"01[0-9]-?\d{3,4}-?\d{4}")
_RESIDENT_ID_PATTERN = re.compile(r"\d{6}-?[1-4]\d{6}")


# 생년월일을 받기 시작하면서 마스킹 대상이 하나 늘었다. 생년월일은 슬롯으로는
# 필요하지만(만 나이 계산) 검색 쿼리로 나가면 안 되는 값이라, "슬롯 추출용
# 마스킹"과 "외부 전송용 마스킹"을 분리한다.
_BIRTH_DATE_TEXT_PATTERN = re.compile(
    r"(?:19|20)\d{2}\s*(?:[-./년]\s*\d{1,2}\s*(?:[-./월]\s*\d{1,2}\s*일?)?)"
)


def redact_for_slot_extraction(text: str) -> str:
    """슬롯 값으로 절대 쓰지 않을 PII만 지운다.

    이메일·전화번호·주민등록번호는 프롬프트 규칙상 어떤 슬롯으로도 추출하지
    않으므로 추출 전에 지운다. 주민등록번호를 지우는 것은 앞 6자리에서
    생년월일을 역산하지 않겠다는 뜻이기도 하다 - 생년월일은 사용자가 직접
    말한 경우에만 받는다.
    """

    redacted = _EMAIL_PATTERN.sub(" ", text)
    redacted = _PHONE_PATTERN.sub(" ", redacted)
    return _RESIDENT_ID_PATTERN.sub(" ", redacted)


def redact_sensitive_text(text: str) -> str:
    """외부(embedding provider·vector DB)로 나가는 텍스트에서 PII를 지운다.

    슬롯 추출용 마스킹에 생년월일을 추가로 지운다. 원문을 그대로 보내면
    검색 로그에 PII가 남는다(``CONTRIBUTING.md`` 보안 항목). 생년월일은
    검색 품질에 기여하지 않으므로(나이 조건은 슬롯으로 이미 잡았다) 지워도
    잃는 것이 없다.
    """

    return _BIRTH_DATE_TEXT_PATTERN.sub(" ", redact_for_slot_extraction(text))


_AGE_PATTERN = re.compile(r"(\d{1,3})\s*(?:세|살)")
# 생년월일. 4자리 연도 형태만 받는다. "900101" 같은 6자리 축약형은 세기가
# 모호할 뿐 아니라 주민등록번호 앞자리와 구분되지 않아 의도적으로 제외한다.
_BIRTH_DATE_PATTERN = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?(?!\d)"
)
_HOUSEHOLD_PATTERN = re.compile(r"(\d{1,2})\s*인\s*가구")
_CHILDREN_PATTERN = re.compile(r"(?:자녀|아이)\s*(\d{1,2})\s*명")

# 열거형 슬롯의 규칙 기반 추출표.
#
# 각 규칙은 ``(열거형 값, 단독 트리거, 마커 필요 트리거)``다. 먼저 걸리는
# 값을 채택하므로 더 구체적인 표현을 앞에 둔다.
#
# 트리거를 두 종류로 나눈 이유가 핵심이다. "주거급여", "차상위", "구직",
# "여성" 같은 말은 **제도 이름이거나 일반 명사**라서, 등장했다는 사실만으로는
# 사용자의 상태를 뜻하지 않는다. "주거급여 얼마 받나요"는 질문이지 수급
# 사실이 아니다. 이걸 상태로 읽으면 소득 구간이 잘못 잡히고, 소득은 하드
# 필터이므로 자격이 되는 제도가 통째로 탈락한다. 그래서 이런 표현은 뒤에
# 자기서술 마커("입니다", "이고", "수급", "중이" 등)가 붙었을 때만 인정한다.
# "임신했", "장애가 있"처럼 그 자체로 상태를 서술하는 표현은 단독으로 받는다.
_SELF_ATTRIBUTION_MARKERS = (
    "입니다", "이에요", "예요", "이고", "이며", "이라", "인데", "이기도",
    "이었", "였", "중이", "중입니다", "상태", "해당", "대상자",
    "수급", "받고", "받는", "받아",
)
# 마커를 찾을 꼬리 길이. "차상위계층이고"처럼 트리거와 마커 사이에 한두
# 어절이 끼는 경우를 담되, 다음 문장까지 넘어가지 않을 만큼만 본다.
_SELF_ATTRIBUTION_WINDOW = 12

# 연령 조건이 누구를 가리키는지 알아내기 위한 신호. 값을 직접 정하지 않고
# "본인 언급이 있었나 / 다른 사람 언급이 있었나"만 돌려주며, 실제 판정은
# N1(slot_parser)이 이전 턴 값과 합쳐서 한다.
_AGE_SUBJECT_SELF_TRIGGERS = ("저는", "제가", "저의", "제 나이", "본인", "나는", "내가")
_AGE_SUBJECT_CHILD_TRIGGERS = (
    "우리 아이", "저희 아이", "제 아이", "우리 애", "저희 애", "애기",
    "아이가", "아이는", "아이 지원", "자녀", "아들", "딸", "손주", "손자", "손녀",
)
_AGE_SUBJECT_OTHER_TRIGGERS = (
    "부모님", "어머니", "아버지", "할머니", "할아버지", "조부모",
    "가구원", "피부양자", "모시고",
)

_GENDER_RULES = (
    (Gender.FEMALE, (), ("여성", "여자")),
    (Gender.MALE, (), ("남성", "남자")),
)
_DISABILITY_RULES = (
    (
        DisabilityStatus.REGISTERED,
        ("등록장애", "중증장애", "경증장애", "장애가 있", "장애인입니다",
         "장애인이에요"),
        ("장애인 등록", "장애등록"),
    ),
    (DisabilityStatus.NOT_REGISTERED, ("장애 없", "장애는 없", "비장애"), ()),
)
_EMPLOYMENT_RULES = (
    (
        EmploymentStatus.SELF_EMPLOYED,
        ("자영업을 하", "자영업 하", "장사를 하", "가게를 하"),
        ("자영업", "개인사업", "사업자", "프리랜서"),
    ),
    (
        EmploymentStatus.STUDENT,
        ("학교에 다니",),
        ("재학", "대학생", "고등학생", "학생", "휴학"),
    ),
    (
        EmploymentStatus.JOB_SEEKING,
        ("취업 준비", "취준", "퇴사했", "일자리를 찾", "직장을 찾"),
        ("구직", "실직", "퇴사"),
    ),
    (
        EmploymentStatus.EMPLOYED,
        ("직장 다니", "회사 다니", "일하고 있", "근무하고 있"),
        ("재직", "근무 중", "취업 중"),
    ),
    (EmploymentStatus.NOT_WORKING, ("일을 안 하", "일하지 않"), ("무직",)),
)
_MARITAL_RULES = (
    # "배우자 없이"가 기혼으로 읽히면 정반대 판정이 된다. 부정 표현을 먼저
    # 보고 그 뒤에 기혼 판단을 한다.
    (MaritalStatus.DIVORCED, ("이혼했", "이혼한", "이혼 후"), ("이혼",)),
    (MaritalStatus.BEREAVED, ("사별했", "사별한"), ("사별",)),
    (
        MaritalStatus.SINGLE,
        ("미혼", "결혼 안", "결혼하지 않", "배우자 없", "배우자가 없", "싱글"),
        (),
    ),
    (
        MaritalStatus.MARRIED,
        ("기혼", "결혼했", "결혼 했", "배우자와", "배우자가 있"),
        (),
    ),
)
_PREGNANCY_RULES = (
    (
        PregnancyStatus.POSTPARTUM,
        ("출산했", "출산 후", "출산한", "아이를 낳"),
        ("산후",),
    ),
    (
        PregnancyStatus.PREGNANT,
        ("임신 중", "임신중", "임신했", "임신한", "임신입니다", "출산 예정",
         "출산예정", "만삭"),
        ("임신",),
    ),
    (PregnancyStatus.NONE, ("임신 아니", "임신은 아니"), ()),
)
_HOUSEHOLD_TYPE_RULES = (
    (HouseholdType.SINGLE_PARENT, ("미혼모", "미혼부"), ("한부모",)),
    (HouseholdType.GRANDPARENT, (), ("조손",)),
    (HouseholdType.MULTICULTURAL, (), ("다문화", "결혼이민")),
    (HouseholdType.MULTI_CHILD, (), ("다자녀",)),
    (HouseholdType.NORTH_KOREAN_DEFECTOR, (), ("북한이탈", "탈북")),
    (HouseholdType.CARE_LEAVER, (), ("보호종료", "자립준비청년")),
    (HouseholdType.FACILITY_LEAVER, (), ("시설퇴소",)),
    (
        HouseholdType.SINGLE_PERSON,
        ("혼자 살", "혼자 삽니다"),
        ("1인 가구", "1인가구"),
    ),
)
# 소득은 금액이 아니라 구간으로만 받는다. 사용자가 아는 것("기초생활수급자",
# "차상위")과 제도가 쓰는 기준(소득인정액)을 이어주는 표현만 매핑한다.
# 급여 이름(생계급여·의료급여·주거급여)은 전부 마커 필요다 - 제도 이름을
# 수급 사실로 읽으면 소득 하드 필터가 틀어진다.
_INCOME_KEYWORD_RULES = (
    (
        IncomeBracket.UNDER_30,
        ("기초생활수급자", "기초수급자"),
        ("기초생활수급", "기초수급", "생계급여"),
    ),
    (IncomeBracket.PCT_30_50, (), ("차상위", "의료급여", "주거급여")),
)
# 조사("중위소득의 50%")까지 받는다. 조사 하나로 소득 구간이 통째로 빠지면
# 필터가 아예 안 걸린다.
_INCOME_PERCENT_PATTERN = re.compile(
    r"중위\s*소득\s*(?:대비\s*)?(?:의\s*)?(\d{1,3})\s*%"
)
# "100% 초과"를 "100% 이하" 구간으로 읽으면 정반대 판정이 된다.
_INCOME_ABOVE_MARKERS = ("초과", "넘", "이상", "위")
_INCOME_BRACKET_UPPER_BOUNDS = (
    (30, IncomeBracket.UNDER_30),
    (50, IncomeBracket.PCT_30_50),
    (75, IncomeBracket.PCT_50_75),
    (100, IncomeBracket.PCT_75_100),
    (150, IncomeBracket.PCT_100_150),
)

_INTEREST_KEYWORDS = (
    "육아", "출산", "보육", "주거", "주택", "취업", "일자리", "창업",
    "교육", "장학", "의료", "건강", "돌봄", "노인", "장애인", "저소득",
    "청년", "다문화", "한부모",
)

# 지역 후보를 "원문 그대로" 잘라내기 위한 표현. 정규화(공식 명칭 변환)는
# 이 모듈이 아니라 N1 노드(rag_chatbot.graph.nodes.slot_parser)의 책임이다.
#
# 접두 길이·접미사만으로 지역을 판정하는 일반화된 정규식(예: "...도로 끝나면
# 지역")은 "제도"·"정도"·"실시"처럼 흔한 일반 단어까지 지역으로 오인한다.
# 그래서 후보 어휘를 공식 시/도 전체 명칭(``CANONICAL_SIDO_NAMES``)과 자주
# 쓰는 축약형(``_BARE_SIDO_NAMES``)의 리터럴 목록으로 한정한다. 시군구
# 단독 명칭(예: "강남구", "중구")은 이 목록에 없으므로 애초에 region_raw로
# 잡히지 않고, 결과적으로 정규화 단계(``slot_parser._normalize_region``)에서
# unknown으로 처리되는 것과 동일한 결과를 유지한다(공통 validator에 시군구
# registry가 없어 임의로 해석하지 않는다는 팀 결정과 일치).
_BARE_SIDO_NAMES = (
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남",
    "제주",
)
# 사용자는 개편 전 명칭이나 구어체 정식 명칭도 그대로 쓴다("강원도",
# "전라남도"). 축약형만 알아듣고 정식 명칭을 놓치면 지역이 있는데도 unknown
# 으로 떨어져 N3 재질문 루프가 돈다. 정규화 표(_SIDO_ALIASES)와 짝을 맞춰
# 여기서도 후보로 잡는다.
_LEGACY_SIDO_NAMES = (
    "강원도", "제주도", "전라남도", "전라북도", "광주광역시",
)
# "전 지역"처럼 공백이 들어간 표현도 후보로 잡아야
# ``slot_parser._NATIONAL_ALIASES``가 실제로 도달 가능해진다.
_NATIONAL_PHRASES = ("전국단위", "전국", "전 지역")
# 시군구는 "성남시 분당구"처럼 2단계까지 올 수 있다(수집기
# ``region_utils.extract_region``와 동일한 깊이). 정규 시군구 registry가 없어
# 여기서는 형태만 보고 자르며, 실제 존재 여부는 검증하지 않는다.
_SIGUNGU_SUFFIX = r"(?:\s+[가-힣]{2,6}(?:시|군|구)){0,2}"
_KNOWN_REGION_NAMES = sorted(
    {
        *CANONICAL_SIDO_NAMES,
        *_BARE_SIDO_NAMES,
        *_LEGACY_SIDO_NAMES,
        *_NATIONAL_PHRASES,
    },
    key=len,
    reverse=True,
)
_REGION_PATTERN = re.compile(
    "(?:" + "|".join(re.escape(name) for name in _KNOWN_REGION_NAMES) + ")"
    + _SIGUNGU_SUFFIX
)


def extract_slots(user_input: str, existing_slots: Mapping[str, object]) -> ExtractedSlots:
    """자유 텍스트에서 슬롯 후보를 추출한다.

    실제 LLM 연결 전까지는 규칙 기반으로 동작하며, 프롬프트 시트가 요구하는
    출력 스키마 형태만 맞춘다. ``existing_slots``는 재입력 시 참고용으로
    전달받지만, 이 함수는 새로 인식된 값만 반환하고 기존 값과의 병합 판단은
    호출자(N1 노드)가 수행한다.

    나이는 ``birth_date``(생년월일)로 받는 것이 원칙이고, 사용자가 말한
    숫자는 ``age_self_reported``에 따로 담는다. 둘을 한 필드에 섞으면 만
    나이와 한국식 세는 나이가 구분되지 않아 경계에서 오판정이 난다.
    """

    del existing_slots  # 지금은 병합 판단에 쓰지 않음 (호출자 책임).

    redacted = redact_for_slot_extraction(user_input)

    age_match = _AGE_PATTERN.search(redacted)
    household_match = _HOUSEHOLD_PATTERN.search(redacted)
    children_match = _CHILDREN_PATTERN.search(redacted)
    region_match = _REGION_PATTERN.search(redacted)
    interests = [keyword for keyword in _INTEREST_KEYWORDS if keyword in redacted]

    return {
        "birth_date": _extract_birth_date(redacted),
        "age_subject_signals": _extract_age_subject_signals(redacted),
        "age_self_reported": int(age_match.group(1)) if age_match else None,
        "region_raw": region_match.group(0) if region_match else None,
        "gender": _match_rules(redacted, _GENDER_RULES),
        "income_bracket": _extract_income_bracket(redacted),
        "disability_status": _match_rules(redacted, _DISABILITY_RULES),
        "employment_status": _match_rules(redacted, _EMPLOYMENT_RULES),
        "marital_status": _match_rules(redacted, _MARITAL_RULES),
        "household_types": _match_all_rules(redacted, _HOUSEHOLD_TYPE_RULES),
        "pregnancy_status": _match_rules(redacted, _PREGNANCY_RULES),
        "interests": interests,
        "household_size": int(household_match.group(1)) if household_match else None,
        "children_count": int(children_match.group(1)) if children_match else None,
    }


def _extract_age_subject_signals(text: str) -> dict[str, bool]:
    """이번 턴에 본인·타인 중 누구를 말했는지 신호만 뽑는다.

    최종 판정을 여기서 하지 않는 이유는, 주체가 대화 전체에 걸쳐 결정되기
    때문이다. "우리 아이 지원 뭐 있나요" 다음 턴에 지역만 답했다고 해서
    주체가 본인으로 돌아가면 안 된다. 누적 판단은 N1이 한다.
    """

    return {
        "self": any(trigger in text for trigger in _AGE_SUBJECT_SELF_TRIGGERS),
        "child": any(trigger in text for trigger in _AGE_SUBJECT_CHILD_TRIGGERS),
        "other": any(trigger in text for trigger in _AGE_SUBJECT_OTHER_TRIGGERS),
    }


def _states_about_self(text: str, trigger: str) -> bool:
    """트리거 뒤에 자기서술 마커가 붙었는지 본다.

    "차상위계층입니다"는 사용자의 상태이고 "차상위계층 지원 뭐 있나요"는
    질문이다. 둘을 구분하지 못하면 후자가 소득 하드 필터가 되어, 실제로는
    자격이 되는 제도가 조용히 탈락한다.
    """

    position = text.find(trigger)
    while position != -1:
        tail = text[position + len(trigger) : position + len(trigger) + _SELF_ATTRIBUTION_WINDOW]
        if any(marker in tail for marker in _SELF_ATTRIBUTION_MARKERS):
            return True
        position = text.find(trigger, position + 1)
    return False


def _rule_matches(text: str, standalone: tuple[str, ...], needs_marker: tuple[str, ...]) -> bool:
    """규칙 하나가 성립하는지 본다."""

    if any(trigger in text for trigger in standalone):
        return True
    return any(
        trigger in text and _states_about_self(text, trigger)
        for trigger in needs_marker
    )


def _match_rules(text: str, rules: _RuleTable) -> str | None:
    """먼저 걸리는 규칙의 열거형 값을 반환한다. 없으면 ``None``."""

    for value, standalone, needs_marker in rules:
        if _rule_matches(text, standalone, needs_marker):
            return value.value
    return None


def _match_all_rules(text: str, rules: _RuleTable) -> list[str]:
    """겹쳐서 성립할 수 있는 값을 모두 반환한다 (가구 유형 등)."""

    return [
        value.value
        for value, standalone, needs_marker in rules
        if _rule_matches(text, standalone, needs_marker)
    ]


def _extract_birth_date(text: str) -> str | None:
    """생년월일을 ISO 문자열로 뽑는다. 실제 존재하는 날짜만 통과시킨다.

    미래 날짜와 비현실적인 나이는 여기서 버린다. 오타 하나가 그대로 만
    나이가 되어 자격 판정을 뒤집는 것보다, 값이 없어서 한 번 더 묻는 쪽이
    안전하다(fail-closed).
    """

    match = _BIRTH_DATE_PATTERN.search(text)
    if match is None:
        return None
    year, month, day = (int(group) for group in match.groups())
    # 실제 존재하는 날짜인지, 미래·비현실적 나이가 아닌지는 slot_schema가
    # 한 곳에서 판정한다(N2 게이트도 같은 함수를 쓴다).
    parsed = parse_birth_date(f"{year:04d}-{month:02d}-{day:02d}")
    return parsed.isoformat() if parsed is not None else None


def _extract_income_bracket(text: str) -> str | None:
    """소득 구간을 뽑는다. 금액만 말한 경우에는 아무 값도 만들지 않는다.

    "월 250만원"에서 소득인정액을 계산하려면 가구원 수·근로소득 공제·재산
    환산이 모두 필요하다(참고자료 C군). 여기서 추정해 구간을 붙이면 실제로는
    자격이 되는 제도가 조용히 탈락하고, 사라진 정답은 복구할 방법이 없다.
    그래서 금액은 의도적으로 무시하고 N3가 구간을 다시 묻게 둔다.
    """

    percent_match = _INCOME_PERCENT_PATTERN.search(text)
    if percent_match is not None:
        return _bracket_for_percent(
            int(percent_match.group(1)),
            text[percent_match.end() : percent_match.end() + 6],
        )

    return _match_rules(text, _INCOME_KEYWORD_RULES)


def _bracket_for_percent(percent: int, tail: str) -> str:
    """비율을 구간으로 바꾼다. "초과"가 붙으면 한 칸 위 구간으로 읽는다.

    "중위소득 100% 초과"를 "100% 이하" 구간으로 잡으면 정반대 판정이 된다.
    소득은 상한 비교 하드 필터라 방향이 뒤집히면 대상이 아닌 제도가 통과하고
    대상인 제도가 빠진다.
    """

    is_above = any(marker in tail for marker in _INCOME_ABOVE_MARKERS)
    for upper_bound, bracket in _INCOME_BRACKET_UPPER_BOUNDS:
        if percent < upper_bound or (percent == upper_bound and not is_above):
            return bracket.value
    return IncomeBracket.OVER_150.value


_REGION_QUESTION = (
    "거주하시는 지역을 알려주시면 그 지역에 맞는 지원 제도를 안내해드릴 수 있어요. "
    "시/도 이름(예: 서울특별시, 부산광역시)부터 말씀해주세요."
)
# 슬롯별 질문. 순서는 slot_schema.PROFILE_HARD_GATE_SLOTS를 따른다.
_SLOT_QUESTIONS: dict[str, str] = {
    "birth_date": (
        "생년월일을 알려주세요(예: 1990-01-01). "
        "복지제도 연령 기준이 대부분 만 나이라, 생년월일로 계산해야 정확합니다."
    ),
    "gender": "성별을 알려주세요(여성/남성).",
    "income_bracket": (
        "소득 수준을 대략 알려주세요. "
        "기초생활수급/차상위 여부 또는 기준중위소득 대비 비율(예: 중위소득 60%)로 "
        "말씀해주시면 됩니다."
    ),
    "disability_status": "장애인 등록 여부를 알려주세요(등록/미등록).",
    "employment_status": (
        "현재 취업 상태를 알려주세요(재직/구직/자영업/학생/무직)."
    ),
}
_UNKNOWN_SLOT_QUESTION = "추가 정보가 필요합니다."
_SKIP_NOTICE = "모르시거나 말씀하기 어려운 항목은 '모름'이라고 답하셔도 됩니다."
_REFERENCE_NOTICE = " 지역과 무관하게 적용되는 관련 법령 참고 링크도 함께 안내해드릴게요."


def generate_followup_question(
    reference_count: int, missing_slots: Sequence[str] | None = None
) -> str:
    """부족한 슬롯을 사용자에게 되묻는 문구를 만든다.

    N3(``rag_chatbot.graph.nodes.request_missing_slots``)만 호출하며,
    "슬롯이 실제로 부족한 경우에만 호출한다"는 전제는 호출자가 검증한다 -
    이 함수는 전제를 다시 검사하지 않고 문구 조립만 담당한다(두 곳에서
    같은 불변식을 중복 검증하지 않기 위함).

    지역이 부족하면 지역만 먼저 묻는다. 지역은 유일한 하드 필터 슬롯이라
    없으면 검색 자체가 성립하지 않는 반면, 나머지 프로필 슬롯은 판정 단계에서
    쓰이므로 우선순위가 다르다. 한 번에 여섯 가지를 몰아 물으면 답변율이
    떨어진다는 실무적 이유도 있다.

    실제 문구 생성 방식(규칙 기반 vs 모델)은 참고자료 결정사항 시트 9번에
    "확인 필요"로 남아 있다. 모델 Fine-tuning은
    ``docs/PROJECT_COMPLIANCE.md``가 정한 Baseline 이전 비범위 항목이므로,
    이 placeholder는 규칙 기반 템플릿만 사용한다.
    """

    slots = list(missing_slots) if missing_slots else [REGION_SLOT]

    if REGION_SLOT in slots:
        question = _REGION_QUESTION
    else:
        asks = [_SLOT_QUESTIONS.get(slot, _UNKNOWN_SLOT_QUESTION) for slot in slots]
        question = " ".join([*asks, _SKIP_NOTICE])

    if reference_count:
        question += _REFERENCE_NOTICE
    return question
