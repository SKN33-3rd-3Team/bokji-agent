"""N1·N3처럼 LLM 호출이 필요한 노드가 사용하는 단일 진입점.

실제 LLM provider는 아직 팀이 정하지 않았다(``docs/RAG_DESIGN_PLAN.md``의
"생성" 항목 참고, requirements 파일에도 LLM SDK가 없음). 노드 파일이 이
모듈을 거쳐서만 LLM을 호출하게 분리해 두면, provider가 정해진 뒤에는 이
파일의 구현부만 교체하면 되고 노드 시그니처(``def <동사_명사>(state:
GraphState) -> dict:``)는 바뀌지 않는다.

슬롯 추출(``extract_slots``)은 2단계다(2026-08-31 변경):

1. 규칙 기반 추출을 **항상** 먼저 돌린다. 결정론적이고 네트워크가 필요
   없어서, LLM이 없거나 실패해도 그래프가 끝까지 돈다.
2. ``llm_client``가 주입되면 LLM에게 같은 발화를 다시 넣어 슬롯을 뽑고,
   계약(``slot_schema``)에 있는 값만 통과시킨 뒤 규칙 결과 위에 덮어쓴다.

이렇게 바꾼 이유: 규칙 기반 추출기는 "1955년 3월생이에요"(일자 없음),
"모름", "형편이 어려워요" 같은 실제 사용자 표현을 대부분 못 알아들어서
슬롯이 끝까지 안 채워졌다. 그러면 N2가 되묻기 상한(``MAX_SLOT_ASKS``)에
닿아 전부 ``unknown`` 센티넬로 확정해버리고, 결국 프로필 조건 없이 검색이
진행된다 - 사용자는 답을 다 했는데 아무것도 반영되지 않는 상태가 된다.

**개인정보 (2026-08-31 팀 승인 완료)**: LLM에 보내는 텍스트는
``redact_for_slot_extraction``으로 이메일·전화번호·주민등록번호만 지운다.
생년월일은 만 나이 계산에 반드시 필요한 슬롯이라 마스킹하지 않고 그대로
외부 LLM provider에 전달된다 - vector DB로 나가는 텍스트에 쓰는
``redact_sensitive_text``(생년월일도 지움)와 다른 점이며, **이 차이는
팀이 확인하고 승인한 것이다.** 승인 범위는 "슬롯 추출에 필요한 생년월일"
까지이고, 그 밖의 식별정보를 추가로 보내려면 다시 논의해야 한다.

질문 문구 생성(``generate_followup_question``)은 여전히 규칙 기반
템플릿이다 - 모델 Fine-tuning은 ``docs/PROJECT_COMPLIANCE.md``가 정한
Baseline 이전 비범위 항목이다.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Mapping, Sequence, TypedDict

from rag_design.contracts import CANONICAL_SIDO_NAMES

from ..llm import LLMCallError, LLMClient, loads_json_object
from .slot_schema import (
    HARD_GATE_SLOTS,
    MAX_PLAUSIBLE_AGE,
    REGION_SLOT,
    SLOT_ENUMS,
    UNKNOWN,
    AgeSubject,
    DisabilityStatus,
    EmploymentStatus,
    Gender,
    HouseholdType,
    IncomeBracket,
    MaritalStatus,
    PregnancyStatus,
    is_valid_slot_value,
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
    "지원금제도", "지원금", "실업급여", "청년수당",
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


def extract_slots(
    user_input: str,
    existing_slots: Mapping[str, object],
    llm_client: LLMClient | None = None,
    asked_slots: Sequence[str] | None = None,
) -> ExtractedSlots:
    """자유 텍스트에서 슬롯 후보를 추출한다 (규칙 + 선택적 LLM).

    ``existing_slots``는 재입력 시 참고용으로 전달받지만, 이 함수는 새로
    인식된 값만 반환하고 기존 값과의 병합 판단은 호출자(N1 노드)가 수행한다.

    나이는 ``birth_date``(생년월일)로 받는 것이 원칙이고, 사용자가 말한
    숫자는 ``age_self_reported``에 따로 담는다. 둘을 한 필드에 섞으면 만
    나이와 한국식 세는 나이가 구분되지 않아 경계에서 오판정이 난다.

    ``llm_client``가 없으면 예전과 완전히 동일하게 규칙 기반으로만 동작한다.
    있으면 LLM 결과를 규칙 결과 **위에** 덮어쓴다 - 자연어 이해는 LLM이 더
    잘하고, 규칙이 못 뽑은 자리를 채우는 것이 이 연동의 목적이기 때문이다.
    다만 LLM이 내놓은 값도 ``slot_schema`` 계약을 통과한 것만 받아들이고
    (fail-closed), 호출/파싱이 실패하면 규칙 결과를 그대로 쓴다.

    ``asked_slots``는 직전 턴에 N3가 되물은 슬롯 목록이다. 사용자가 "모름"
    이라고만 답했을 때 그 대답이 **어느 슬롯**에 대한 것인지는 이 맥락
    없이는 알 수 없어서 LLM 프롬프트에 함께 넣는다.
    """

    rule_based = _extract_slots_by_rules(user_input, asked_slots)
    if llm_client is None:
        return rule_based

    llm_values = _extract_slots_via_llm(user_input, asked_slots, llm_client)
    if not llm_values:
        # LLM 호출/파싱 실패, 또는 계약을 통과한 값이 하나도 없음. 규칙
        # 결과를 그대로 쓴다(그래프가 죽지 않는 것이 우선).
        return rule_based
    return _merge_rule_and_llm_slots(rule_based, llm_values)


SLOT_EXTRACTION_SYSTEM_PROMPT = (
    "당신은 한국 복지 상담 챗봇의 슬롯 추출기입니다. 사용자가 자기 자신 또는 "
    "가족에 대해 실제로 말한 사실만 JSON으로 뽑아냅니다. 말하지 않은 항목은 "
    "반드시 null(배열이면 빈 배열)로 두고, 추측하거나 지어내지 않습니다. "
    "JSON 객체 하나 외에는 아무것도 출력하지 않습니다."
)

# LLM이 채울 수 있는 열거형 슬롯. 값 목록은 slot_schema에서 그대로 뽑아
# 쓴다 - 프롬프트에 손으로 적어두면 계약이 바뀔 때 조용히 어긋난다.
_LLM_ENUM_FIELDS = (
    "gender",
    "income_bracket",
    "disability_status",
    "employment_status",
    "marital_status",
    "pregnancy_status",
)
_LLM_ENUM_DESCRIPTIONS = {
    "gender": "성별",
    "income_bracket": "기준중위소득 대비 소득 구간",
    "disability_status": "장애인 등록 여부",
    "employment_status": "취업 상태",
    "marital_status": "혼인 상태",
    "pregnancy_status": "임신/출산 상태",
}
_HOUSEHOLD_TYPE_VALUES = frozenset(member.value for member in HouseholdType)
_AGE_SUBJECT_SIGNAL_KEYS = ("self", "child", "other")
# LLM이 긴 배열/문자열을 쏟아내도 상태가 망가지지 않도록 상한을 둔다.
_LLM_MAX_LIST_ITEMS = 10
_LLM_MAX_INTEREST_LENGTH = 40
_LLM_MAX_HOUSEHOLD_SIZE = 30
_LLM_MAX_CHILDREN_COUNT = 20


def _enum_values_text(field: str) -> str:
    return ", ".join(f'"{member.value}"' for member in SLOT_ENUMS[field])


def build_slot_extraction_prompt(
    redacted_input: str, asked_slots: Sequence[str] | None = None
) -> str:
    """LLM 슬롯 추출 프롬프트를 만든다 (테스트에서 직접 검증할 수 있게 공개).

    ``redacted_input``은 이미 PII 마스킹을 거친 텍스트여야 한다.
    """

    enum_lines = "\n".join(
        f"- {field}: {_LLM_ENUM_DESCRIPTIONS[field]}. 가능한 값 {_enum_values_text(field)} 중 하나 또는 null"
        for field in _LLM_ENUM_FIELDS
    )
    asked_line = ""
    if asked_slots:
        asked_line = (
            "\n[직전에 챗봇이 물어본 항목]\n"
            + ", ".join(asked_slots)
            + "\n사용자가 '모름'/'말하기 싫어요'처럼 답했다면 위 항목들만 "
            '"unknown"으로 채우세요.\n'
        )

    return (
        f"[사용자 발화]\n{redacted_input}\n"
        f"{asked_line}"
        "\n[뽑아야 하는 필드]\n"
        '- birth_date: 생년월일 "YYYY-MM-DD". 연·월만 말했으면 일은 01로 '
        '채웁니다("1955년 3월생" -> "1955-03-01"). 나이만 말했으면 null.\n'
        "- age_self_reported: 사용자가 직접 말한 나이 숫자(정수) 또는 null\n"
        '- region_raw: 거주 지역 표현 그대로("서울", "경기도 성남시 분당구", '
        '"전국") 또는 null\n'
        f"{enum_lines}\n"
        f"- household_types: 배열. 가능한 값 {_enum_values_text_household()}\n"
        '- interests: 사용자가 찾거나 받고 싶은 복지 제도·급여·지원 분야의 '
        '핵심 표현 배열(예: "지원금제도", "실업급여", "청년수당", "육아", '
        '"주거", "일자리"). "OOO 받고 싶어", "OOO 관련 제도 알아봐줘", '
        '"OOO 지원 있나요?"처럼 요청한 대상은 OOO를 interests에 넣습니다. '
        '제도명이 길면 검색에 유용한 원문 표현을 보존하고, 없으면 []\n'
        "- household_size: 가구원 수(정수) 또는 null\n"
        "- children_count: 자녀 수(정수) 또는 null\n"
        '- age_subject_signals: {"self": bool, "child": bool, "other": bool}. '
        "이번 발화가 본인 이야기면 self, 자녀 이야기면 child, 그 밖의 가족이면 "
        "other를 true로 둡니다.\n"
        "\n[규칙]\n"
        "1. 사용자가 말하지 않은 항목은 null(배열이면 [])입니다. 추측 금지.\n"
        '2. "unknown"은 사용자가 모른다고 명시적으로 답한 항목에만 씁니다. '
        '언급 자체가 없으면 "unknown"이 아니라 null입니다.\n'
        '3. 제도 이름을 묻는 것은 사용자의 상태가 아닙니다. "주거급여 얼마 '
        '받나요?"는 질문이므로 income_bracket을 채우면 안 되고, "저는 주거급여 '
        '받고 있어요"처럼 본인 상태를 서술했을 때만 채웁니다.\n'
        '4. 금액만 말한 경우(예: "월 250만원")는 income_bracket을 추정하지 '
        "말고 null로 둡니다. 기초생활수급/차상위 여부나 중위소득 대비 %를 "
        "말했을 때만 채웁니다.\n"
        "5. 이메일·전화번호·주민등록번호는 어떤 필드에도 넣지 않습니다.\n"
        "6. JSON 객체 하나만 출력합니다. 설명·코드펜스 금지."
    )


def _enum_values_text_household() -> str:
    return ", ".join(f'"{member.value}"' for member in HouseholdType)


def _extract_slots_via_llm(
    user_input: str, asked_slots: Sequence[str] | None, llm_client: LLMClient
) -> dict:
    """LLM으로 슬롯을 뽑는다. 실패하면 빈 dict(= 아무것도 못 뽑음)."""

    redacted = redact_for_slot_extraction(user_input)
    if not redacted.strip():
        return {}

    prompt = build_slot_extraction_prompt(redacted, asked_slots)
    try:
        raw = llm_client.complete(prompt, system=SLOT_EXTRACTION_SYSTEM_PROMPT)
        data = loads_json_object(raw)
    except (LLMCallError, ValueError, TypeError, AttributeError):
        # 규칙 기반 결과로 폴백한다(N5 claim_extractor와 같은 관례).
        return {}
    return _validated_llm_slots(data)


def _validated_llm_slots(data: Mapping[str, object]) -> dict:
    """LLM이 준 값 중 계약을 통과한 것만 남긴다 (fail-closed).

    계약에 없는 값을 그대로 저장하면 N2 게이트가 "채워졌음"으로 읽고
    검증되지 않은 값으로 판정이 진행된다 - 규칙 기반 경로가 이미 지키고
    있는 원칙을 LLM 경로에도 똑같이 적용한다.
    """

    validated: dict = {}

    birth_date = data.get("birth_date")
    if isinstance(birth_date, str):
        # 실제 존재하는 날짜인지, 미래·비현실적 나이가 아닌지는 규칙 경로와
        # 같은 함수로 판정한다.
        parsed = parse_birth_date(birth_date.strip())
        if parsed is not None:
            validated["birth_date"] = parsed.isoformat()

    age = _coerce_int(data.get("age_self_reported"), 0, MAX_PLAUSIBLE_AGE)
    if age is not None:
        validated["age_self_reported"] = age

    region_raw = data.get("region_raw")
    if isinstance(region_raw, str) and region_raw.strip():
        # 정규화/검증은 N1의 _normalize_region이 한다(규칙 경로와 동일).
        validated["region_raw"] = region_raw.strip()

    for field in _LLM_ENUM_FIELDS:
        value = data.get(field)
        if is_valid_slot_value(field, value):
            validated[field] = value

    household_types = [
        value
        for value in _as_str_list(data.get("household_types"))
        if value in _HOUSEHOLD_TYPE_VALUES
    ]
    if household_types:
        validated["household_types"] = household_types

    interests = [
        value[:_LLM_MAX_INTEREST_LENGTH] for value in _as_str_list(data.get("interests"))
    ]
    if interests:
        validated["interests"] = interests

    household_size = _coerce_int(data.get("household_size"), 1, _LLM_MAX_HOUSEHOLD_SIZE)
    if household_size is not None:
        validated["household_size"] = household_size

    children_count = _coerce_int(data.get("children_count"), 0, _LLM_MAX_CHILDREN_COUNT)
    if children_count is not None:
        validated["children_count"] = children_count

    signals = data.get("age_subject_signals")
    if isinstance(signals, Mapping):
        validated["age_subject_signals"] = {
            key: bool(signals.get(key)) for key in _AGE_SUBJECT_SIGNAL_KEYS
        }

    return validated


def _coerce_int(value: object, minimum: int, maximum: int) -> int | None:
    """정수로 읽을 수 있고 상식적인 범위 안일 때만 값을 돌려준다."""

    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None


def _as_str_list(value: object) -> list[str]:
    """문자열 배열만 받아 정리한다. 중복은 순서를 지키면서 제거한다."""

    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= _LLM_MAX_LIST_ITEMS:
            break
    return cleaned


def _merge_rule_and_llm_slots(
    rule_based: ExtractedSlots, llm_values: Mapping[str, object]
) -> ExtractedSlots:
    """규칙 결과 위에 LLM 결과를 덮어쓴다.

    스칼라/열거형은 LLM 값이 이기고, 여러 개가 겹칠 수 있는 필드
    (``interests``/``household_types``)는 합집합으로 모은다.

    ``age_subject_signals``만 예외적으로 OR로 합친다. 이 신호는 "연령 조건이
    누구를 가리키는가"를 정하는데, 신호를 놓치면 41세 학부모의 "우리 아이
    지원" 질문에 본인 나이 41로 필터가 걸려 아동 제도가 전부 탈락한다
    (참고자료 §8). 어느 한쪽만 감지해도 살리는 편이 안전하다.
    """

    merged: dict = dict(rule_based)
    for field, value in llm_values.items():
        if field == "age_subject_signals":
            continue
        if field in ("interests", "household_types"):
            combined = list(merged.get(field) or [])
            for item in value:  # type: ignore[union-attr]
                if item not in combined:
                    combined.append(item)
            merged[field] = combined
            continue
        merged[field] = value

    llm_signals = llm_values.get("age_subject_signals")
    if isinstance(llm_signals, Mapping):
        rule_signals = merged.get("age_subject_signals") or {}
        merged["age_subject_signals"] = {
            key: bool(rule_signals.get(key)) or bool(llm_signals.get(key))
            for key in _AGE_SUBJECT_SIGNAL_KEYS
        }
    return merged  # type: ignore[return-value]


# 사용자가 "그건 모르겠다 / 말하기 싫다"라고 답했음을 알리는 표현.
_DONT_KNOW_MARKERS = (
    "모름", "모르", "몰라", "글쎄", "말하기 싫", "밝히고 싶지", "비공개",
    "안 알려", "말 안 할", "노코멘트",
)
# "모름" 답변을 센티넬로 확정해도 되는 슬롯. 열거형이 아닌 birth_date/region은
# 제외한다 - 센티넬 문자열이 날짜/지역 계약을 통과하지 못해서, N2가 어차피
# 되묻기 상한까지 간 뒤 자체적으로 확정한다.
_DONT_KNOW_APPLICABLE_SLOTS = (
    "gender",
    "income_bracket",
    "disability_status",
    "employment_status",
)


def _extract_slots_by_rules(
    user_input: str, asked_slots: Sequence[str] | None = None
) -> ExtractedSlots:
    """규칙(정규식/키워드)만으로 슬롯을 뽑는다.

    LLM이 없거나 실패해도 그래프가 끝까지 돌게 하는 기본 경로다. 자연어
    이해는 못 하지만 결정론적이고 네트워크가 필요 없다.

    ``asked_slots``는 직전 턴에 N3가 되물은 슬롯이다. 이 목록에 있는 슬롯은
    "사용자가 지금 그 질문에 답하는 중"이라는 뜻이라 두 가지가 달라진다:

    1. 자기서술 마커 없이 단답만 해도 인정한다(``relaxed``) - "여자",
       "무직", "차상위"처럼 답하는 것이 정상이기 때문.
    2. "모름"이라고 답했는데 그 슬롯 값을 못 뽑았으면 ``unknown`` 센티넬로
       확정한다. N3 질문이 "'모름'이라고 답하셔도 됩니다"라고 안내하는데
       정작 그 답을 못 알아들으면, 사용자는 답을 했는데도 되묻기 상한까지
       같은 질문을 계속 받게 된다.

    2번의 한계(숨기지 않음): "모름"이 **어느 항목**을 가리키는지는 규칙으로
    정확히 알 수 없어서, 되물은 슬롯 중 값을 못 뽑은 것 전부에 적용한다.
    "여자, 소득은 모름, 무직"처럼 다른 항목이 정상적으로 뽑히면 그 항목들은
    영향을 받지 않는다. 값을 잘못 채우는 게 아니라 "미확인"으로 두는
    것이라(센티넬은 필터로 쓰이지 않는다) fail-open 방향의 오차다.
    """

    redacted = redact_for_slot_extraction(user_input)
    answered = set(asked_slots or ())

    age_match = _AGE_PATTERN.search(redacted)
    household_match = _HOUSEHOLD_PATTERN.search(redacted)
    children_match = _CHILDREN_PATTERN.search(redacted)
    region_match = _REGION_PATTERN.search(redacted)
    interests = _extract_interests(redacted)

    extracted: ExtractedSlots = {
        "birth_date": _extract_birth_date(redacted),
        "age_subject_signals": _extract_age_subject_signals(redacted),
        "age_self_reported": int(age_match.group(1)) if age_match else None,
        "region_raw": region_match.group(0) if region_match else None,
        "gender": _match_rules(
            redacted, _GENDER_RULES, relaxed="gender" in answered
        ),
        "income_bracket": _extract_income_bracket(
            redacted, relaxed="income_bracket" in answered
        ),
        "disability_status": _match_rules(
            redacted, _DISABILITY_RULES, relaxed="disability_status" in answered
        ),
        "employment_status": _match_rules(
            redacted, _EMPLOYMENT_RULES, relaxed="employment_status" in answered
        ),
        "marital_status": _match_rules(redacted, _MARITAL_RULES),
        "household_types": _match_all_rules(redacted, _HOUSEHOLD_TYPE_RULES),
        "pregnancy_status": _match_rules(redacted, _PREGNANCY_RULES),
        "interests": interests,
        "household_size": int(household_match.group(1)) if household_match else None,
        "children_count": int(children_match.group(1)) if children_match else None,
    }

    if answered and any(marker in redacted for marker in _DONT_KNOW_MARKERS):
        for field in _DONT_KNOW_APPLICABLE_SLOTS:
            if field in answered and extracted.get(field) is None:
                extracted[field] = UNKNOWN  # type: ignore[literal-required]

    return extracted


# 관심사 키워드가 부정 표현 안에 들어 있으면 관심사로 보지 않는다.
#
# 실제로 사용자가 "비장애인"이라고 답했더니 "장애인"이 관심사로 잡히고,
# N4의 검색 질의(policy_search._build_query)가 관심사만으로 만들어지는 탓에
# 질의가 그대로 "장애인"이 되어 장애인 정책만 5건 추천된 일이 있었다
# (2026-08-31). 부정을 못 읽으면 사용자가 아니라고 말한 바로 그 조건으로
# 검색이 돌아간다.
_INTEREST_NEGATION_PREFIX = "비"
# "아닙니다"는 "아니"를 부분문자열로 갖지 않는다(아-닙-니-다) - 활용형을
# 따로 넣어줘야 한다.
_INTEREST_NEGATION_SUFFIXES = ("아니", "아닙", "아녜", "없")
# 부정 표현을 찾을 꼬리 길이. "장애인이 아니에요"처럼 조사가 끼는 경우까지만
# 보고 다음 문장으로 넘어가지 않을 만큼만 본다.
_INTEREST_NEGATION_WINDOW = 6


def _extract_interests(text: str) -> list[str]:
    """관심사 키워드를 뽑되, 부정 표현 안에 있는 것은 제외한다.

    ``"비장애인"``의 ``"장애인"``, ``"장애인이 아니에요"``처럼 사용자가
    **아니라고** 말한 조건이 검색 질의가 되면 안 된다.
    """

    interests: list[str] = []
    for keyword in _INTEREST_KEYWORDS:
        for match in re.finditer(re.escape(keyword), text):
            if text[max(0, match.start() - 1) : match.start()] == _INTEREST_NEGATION_PREFIX:
                continue
            tail = text[match.end() : match.end() + _INTEREST_NEGATION_WINDOW]
            if any(suffix in tail for suffix in _INTEREST_NEGATION_SUFFIXES):
                continue
            interests.append(keyword)
            break
    return interests


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


def _rule_matches(
    text: str,
    standalone: tuple[str, ...],
    needs_marker: tuple[str, ...],
    *,
    relaxed: bool = False,
) -> bool:
    """규칙 하나가 성립하는지 본다.

    ``relaxed``는 "직전 턴에 N3가 바로 이 슬롯을 물었다"는 뜻이다. 그때
    사용자의 답은 질문이 아니라 **대답**이므로, 평소에는 자기서술 마커를
    요구하던 트리거("여성", "무직", "차상위")도 단독으로 인정한다.

    왜 필요한가: 마커 요구는 "주거급여 얼마 받나요"를 수급 사실로 읽지 않기
    위한 장치인데, 되묻기에 대한 답은 보통 "여자, 무직" 같은 단답이라
    마커가 붙을 자리가 없다. 그래서 사용자가 분명히 답을 했는데도 슬롯이 안
    채워지고, N2가 되묻기 상한에 닿아 전부 ``unknown``으로 확정해버렸다.

    한계(숨기지 않음): 되물은 직후에 사용자가 답 대신 되질문을 하면
    ("소득이요? 주거급여 얼마 받는데요?") 오탐이 날 수 있다. 첫 자유 발화
    턴에는 ``relaxed``가 걸리지 않으므로 원래 보호는 그대로 유지된다.
    """

    if any(trigger in text for trigger in standalone):
        return True
    if relaxed:
        return any(trigger in text for trigger in needs_marker)
    return any(
        trigger in text and _states_about_self(text, trigger)
        for trigger in needs_marker
    )


def _match_rules(text: str, rules: _RuleTable, *, relaxed: bool = False) -> str | None:
    """먼저 걸리는 규칙의 열거형 값을 반환한다. 없으면 ``None``."""

    for value, standalone, needs_marker in rules:
        if _rule_matches(text, standalone, needs_marker, relaxed=relaxed):
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


def _extract_income_bracket(text: str, *, relaxed: bool = False) -> str | None:
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

    return _match_rules(text, _INCOME_KEYWORD_RULES, relaxed=relaxed)


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


# 되묻기 항목 문구. 한 줄짜리 짧은 항목으로 두고, 실제 질문은 아래
# generate_followup_question이 번호 목록으로 조립한다. 순서는 호출자가 준
# 순서가 아니라 계약(slot_schema.HARD_GATE_SLOTS) 순서를 따른다.
_SLOT_ASK_ITEMS: dict[str, str] = {
    REGION_SLOT: "거주 지역 (시/도부터, 예: 서울특별시 또는 경기도 성남시 분당구)",
    "birth_date": (
        "생년월일 (예: 1990-01-01) - 복지제도 연령 기준이 대부분 만 나이라 "
        "정확한 날짜가 필요합니다"
    ),
    "gender": "성별 (여성/남성)",
    "income_bracket": (
        "소득 수준 (기초생활수급/차상위 여부, 또는 기준중위소득 대비 비율 - "
        "예: 중위소득 60%)"
    ),
    "disability_status": "장애인 등록 여부 (등록/미등록)",
    "employment_status": "취업 상태 (재직/구직/자영업/학생/무직)",
}
_UNKNOWN_SLOT_ITEM = "추가 정보"
_ASK_INTRO = "맞춤 제도를 찾으려면 아래 정보가 필요해요. 한 번에 이어서 답해주셔도 됩니다."
_SKIP_NOTICE = "모르시거나 말씀하기 어려운 항목은 '모름'이라고 답하셔도 됩니다."
_REFERENCE_NOTICE = "지역과 무관하게 적용되는 관련 법령 참고 링크도 함께 안내해드릴게요."


def generate_followup_question(
    reference_count: int, missing_slots: Sequence[str] | None = None
) -> str:
    """부족한 슬롯을 사용자에게 되묻는 문구를 만든다.

    N3(``rag_chatbot.graph.nodes.request_missing_slots``)만 호출하며,
    "슬롯이 실제로 부족한 경우에만 호출한다"는 전제는 호출자가 검증한다 -
    이 함수는 전제를 다시 검사하지 않고 문구 조립만 담당한다(두 곳에서
    같은 불변식을 중복 검증하지 않기 위함).

    2026-08-31 변경: 예전에는 지역이 부족하면 **지역만** 먼저 묻고 나머지
    프로필 슬롯은 그다음 턴에 물었다. 지역이 검색 성립의 전제라는 이유였는데,
    실제로 돌려보니 되묻기 왕복이 두 배로 늘어나 대화가 길어지고
    ``MAX_SLOT_ASKS`` 상한에 먼저 닿아버리는 문제가 더 컸다. 이제는 부족한
    항목을 한 번에 번호 목록으로 묶어 묻는다.

    문구 생성은 여전히 규칙 기반 템플릿이다(참고자료 결정사항 시트 9번은
    "확인 필요"로 남아 있고, 모델 Fine-tuning은
    ``docs/PROJECT_COMPLIANCE.md``가 정한 Baseline 이전 비범위 항목).
    """

    slots = list(missing_slots) if missing_slots else [REGION_SLOT]
    # 계약 순서(지역 -> 프로필)로 정렬하고, 계약에 없는 슬롯은 뒤에 붙인다.
    ordered = [slot for slot in HARD_GATE_SLOTS if slot in slots]
    ordered += [slot for slot in slots if slot not in HARD_GATE_SLOTS]

    lines = [_ASK_INTRO]
    lines += [
        f"{number}. {_SLOT_ASK_ITEMS.get(slot, _UNKNOWN_SLOT_ITEM)}"
        for number, slot in enumerate(ordered, start=1)
    ]
    lines.append(_SKIP_NOTICE)
    if reference_count:
        lines.append(_REFERENCE_NOTICE)
    return "\n".join(lines)
