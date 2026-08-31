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
from typing import Mapping, TypedDict

from rag_design.contracts import CANONICAL_SIDO_NAMES


class ExtractedSlots(TypedDict, total=False):
    age: int | None
    region_raw: str | None
    interests: list[str]
    household_size: int | None
    children_count: int | None


# 이메일/전화번호/주민등록번호처럼 보이는 패턴은 슬롯으로 추출하기 전에
# 미리 지운다(프롬프트 규칙: "이메일·전화번호·ID는 절대 추출하지 않는다").
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_PATTERN = re.compile(r"01[0-9]-?\d{3,4}-?\d{4}")
_RESIDENT_ID_PATTERN = re.compile(r"\d{6}-?[1-4]\d{6}")


def redact_sensitive_text(text: str) -> str:
    """이메일·전화번호·주민등록번호처럼 보이는 부분을 공백으로 지운다.

    슬롯 추출뿐 아니라 검색 쿼리 조립(N2a)에서도 같은 마스킹을 써야 하므로
    공용 함수로 노출한다. 원문을 그대로 embedding provider나 vector DB로
    보내면 검색 로그에 PII가 남는다(``CONTRIBUTING.md`` 보안 항목).
    """

    redacted = _EMAIL_PATTERN.sub(" ", text)
    redacted = _PHONE_PATTERN.sub(" ", redacted)
    return _RESIDENT_ID_PATTERN.sub(" ", redacted)


_AGE_PATTERN = re.compile(r"(\d{1,3})\s*(?:세|살)")
_HOUSEHOLD_PATTERN = re.compile(r"(\d{1,2})\s*인\s*가구")
_CHILDREN_PATTERN = re.compile(r"(?:자녀|아이)\s*(\d{1,2})\s*명")

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
    출력 스키마(``{age, region_raw, interests[], household_size,
    children_count}``) 형태만 맞춘다. ``existing_slots``는 재입력 시
    참고용으로 전달받지만, 이 함수는 새로 인식된 값만 반환하고 기존 값과의
    병합 판단은 호출자(N1 노드)가 수행한다.
    """

    del existing_slots  # 지금은 병합 판단에 쓰지 않음 (호출자 책임).

    redacted = redact_sensitive_text(user_input)

    age_match = _AGE_PATTERN.search(redacted)
    household_match = _HOUSEHOLD_PATTERN.search(redacted)
    children_match = _CHILDREN_PATTERN.search(redacted)
    region_match = _REGION_PATTERN.search(redacted)
    interests = [keyword for keyword in _INTEREST_KEYWORDS if keyword in redacted]

    return {
        "age": int(age_match.group(1)) if age_match else None,
        "region_raw": region_match.group(0) if region_match else None,
        "interests": interests,
        "household_size": int(household_match.group(1)) if household_match else None,
        "children_count": int(children_match.group(1)) if children_match else None,
    }


def generate_followup_question(reference_count: int) -> str:
    """지역 확인을 사용자에게 직접 되묻는 문구를 만든다.

    N3(``rag_chatbot.graph.nodes.request_missing_region``)만 호출하며,
    "지역이 실제로 부족한 경우에만 호출한다"는 전제는 호출자가 검증한다 -
    이 함수는 전제를 다시 검사하지 않고 문구 조립만 담당한다(두 곳에서
    같은 불변식을 중복 검증하지 않기 위함).

    실제 문구 생성 방식(규칙 기반 vs 모델)은 참고자료 결정사항 시트 9번에
    "확인 필요"로 남아 있다. 모델 Fine-tuning은
    ``docs/PROJECT_COMPLIANCE.md``가 정한 Baseline 이전 비범위 항목이므로,
    이 placeholder는 규칙 기반 템플릿만 사용한다.
    """

    question = (
        "거주하시는 지역을 알려주시면 그 지역에 맞는 지원 제도를 안내해드릴 수 있어요. "
        "시/도 이름(예: 서울특별시, 부산광역시)부터 말씀해주세요."
    )
    if reference_count:
        question += " 지역과 무관하게 적용되는 관련 법령 참고 링크도 함께 안내해드릴게요."
    return question
