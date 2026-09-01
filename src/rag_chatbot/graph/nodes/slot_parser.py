"""N1: 슬롯 파싱 노드.

자유 텍스트 ``user_input``에서 나이·지역·관심사·가구 조건을 추출해
``slots``를 채운다. 재입력(N3 -> N1, Edge E6)일 때는 기존 ``slots``를
유지하고 새로 인식된 값만 덮어쓴다. 지역명은 원문(``region_raw``) 그대로
두지 않고 이 노드가 ``rag_design.contracts.validate_region_name``으로
검증 가능한 ``region_scope``/``region_names``로 정규화해서 저장한다
(참고자료 "노드_Agent" 시트 N1 "연결 rag_design 모듈" 참고).

``llm_client``가 주입되면 슬롯 추출에 LLM을 함께 쓴다(2026-08-31 추가).
규칙 기반 추출기만으로는 "1955년 3월생이에요"(일자 없음), "모름",
"형편이 어려워요" 같은 실제 표현을 못 알아들어 슬롯이 끝까지 안 채워졌고,
그러면 N2가 되묻기 상한에 닿아 전부 ``unknown`` 센티넬로 확정해버린다 -
사용자는 답을 다 했는데 아무것도 반영되지 않는 상태가 된다. 실제 병합
규칙과 개인정보 처리(생년월일은 마스킹하지 않고 LLM에 보낸다)는
``llm_gateway.extract_slots`` docstring 참고.

State 계약(참고자료 "State_연결부" 시트 E1/E2/E6):
- 입력: ``user_input``, ``slots``(재입력 시 기존값), ``missing_slots``
  (직전 턴에 N3가 되물은 항목 - "모름" 답변을 어느 슬롯에 붙일지 판단하는
  맥락으로만 쓴다)
- 출력: ``slots``
"""

from __future__ import annotations

import unicodedata
from datetime import date

from rag_design.contracts import NATIONAL_REGION_NAME, RegionScope, validate_region_name

from ...llm import LLMClient
from ..llm_gateway import extract_slots
from ..slot_schema import (
    SLOT_ENUMS,
    AgeSubject,
    calculate_ages,
    is_valid_slot_value,
    parse_birth_date,
)
from ..state import GraphState, SlotState

# 추출 결과를 그대로 덮어써도 되는 스칼라 슬롯.
_SCALAR_FIELDS = ("age_self_reported", "household_size", "children_count")
# 열거형 슬롯. 계약에 없는 값은 버린다(fail-closed).
# age_subject는 여기서 제외한다. 다른 열거형 슬롯은 "새 값이 있으면 덮어쓴다"
# 규칙이면 충분하지만, 연령 주체는 대화 전체에 걸쳐 결정되므로 별도 규칙을
# 쓴다(_apply_age_subject).
_ENUM_FIELDS = tuple(field for field in SLOT_ENUMS if field != "age_subject")
# 여러 값이 겹칠 수 있어 합집합으로 누적하는 슬롯.
_MULTI_VALUE_FIELDS = ("interests", "household_types")

# 사용자가 흔히 쓰는 시/도 축약형·구어체를 공식 명칭으로 매핑하는 자체 표.
# 시군구 단위의 모호한 단독 이름("중구" 등)은 여기 포함하지 않고 그대로
# unknown 처리한다 - 공통 validator에 전체 시군구 registry가 없어 임의로
# 확장하지 않기로 한 팀 결정을 따른다(docs/VECTOR_STORE.md 참고).
_SIDO_ALIASES: dict[str, str] = {
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "광주": "전남광주통합특별시",
    "전남": "전남광주통합특별시",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
    # 개편 전 명칭과 구어체 정식 명칭. 사용자는 "강원특별자치도"보다
    # "강원도"라고 쓰는 쪽이 많으므로 둘 다 같은 정규 명칭으로 접는다.
    "강원도": "강원특별자치도",
    "제주도": "제주특별자치도",
    "전라남도": "전남광주통합특별시",
    "전라북도": "전북특별자치도",
    "광주광역시": "전남광주통합특별시",
}
_NATIONAL_ALIASES = frozenset({NATIONAL_REGION_NAME, "전 지역", "전국단위"})
# "부산 광역시"처럼 시도 접미어가 띄어쓰기로 잘려 들어오면 시군구가 아니라
# 시도 명칭의 일부로 되붙인다. 이 처리가 없으면 "광역시"가 시군구로 오인돼
# "부산광역시 광역시" 같은 존재하지 않는 지역명이 만들어진다.
_SIDO_SUFFIX_WORDS = frozenset(
    {"특별시", "광역시", "특별자치시", "특별자치도", "자치시", "자치도", "도"}
)


def parse_slots(state: GraphState, llm_client: LLMClient | None = None) -> dict:
    """``user_input``에서 슬롯을 추출해 기존 ``slots``와 병합한다.

    ``llm_client``는 그래프 조립 시 ``functools.partial``로 주입한다. 없으면
    규칙 기반으로만 동작한다(그래프는 LLM 없이도 끝까지 돈다).
    """

    user_input = state.get("user_input", "")
    existing_slots: SlotState = state.get("slots", {})

    extracted = extract_slots(
        user_input,
        existing_slots,
        llm_client=llm_client,
        asked_slots=state.get("missing_slots"),
    )

    # dict()는 얕은 복사라 리스트 필드는 입력 state와 같은 객체를 가리킨다.
    # 그대로 반환하면 이후 노드의 in-place 변경이 checkpointer가 들고 있는
    # 과거 스냅샷까지 오염시키므로 리스트는 따로 복사한다.
    merged: SlotState = dict(existing_slots)
    for list_field in (*_MULTI_VALUE_FIELDS, "region_names"):
        if list_field in merged:
            merged[list_field] = list(merged[list_field])

    for field in _SCALAR_FIELDS:
        value = extracted.get(field)
        if value is not None:
            merged[field] = value

    for field in _ENUM_FIELDS:
        value = extracted.get(field)
        # 계약에 없는 값은 저장하지 않는다. 하드 게이트가 이 값을 "채워졌음"
        # 으로 읽으면 검증되지 않은 값으로 판정이 진행된다.
        if value is not None and is_valid_slot_value(field, value):
            merged[field] = value

    for field in _MULTI_VALUE_FIELDS:
        new_values = extracted.get(field) or []
        if new_values:
            combined = list(merged.get(field, []))
            for value in new_values:
                if value not in combined:
                    combined.append(value)
            merged[field] = combined
        elif field not in merged:
            merged[field] = []

    _apply_age_subject(merged, extracted.get("age_subject_signals") or {})
    _apply_birth_date(merged, extracted.get("birth_date"))

    region_raw = extracted.get("region_raw")
    if region_raw is not None:
        # 이번 턴에 지역처럼 보이는 텍스트가 있었다는 뜻이므로, 정규화에
        # 실패해도 예전 지역 값을 그대로 두지 않는다. "사용자가 지역을 새로
        # 언급했지만 이해하지 못함"을 "예전 지역이 여전히 맞음"으로 오인하면
        # 잘못된 지역으로 검색이 진행될 수 있어, 정규화 실패 시 unknown으로
        # 재설정해 N2가 다시 확인을 요청하도록 한다.
        region_scope, region_names = _normalize_region(region_raw)
        merged["region_scope"] = region_scope.value
        merged["region_names"] = region_names
    elif "region_scope" not in merged:
        # 이번 턴에 지역 언급이 전혀 없을 때만(재입력 포함) 예전 값을
        # 그대로 유지한다. 기존 값 자체가 없는 첫 턴에는 unknown으로 채운다.
        merged["region_scope"] = RegionScope.UNKNOWN.value
        merged["region_names"] = []

    result: dict = {"slots": merged}
    # 첫 턴 질문을 한 번만 보존한다. user_input은 되묻기에 답할 때마다
    # 덮어써지는데, N4 검색에는 "무엇을 알고 싶은지"가 담긴 원래 질문이
    # 필요하다(되묻기 답변 "서울, 2000-03-26, 여성..."을 검색어로 쓰면
    # 검색이 망가진다).
    if not state.get("initial_user_input") and user_input.strip():
        result["initial_user_input"] = user_input.strip()
    return result


def _apply_age_subject(merged: SlotState, signals: dict[str, bool]) -> None:
    """연령 조건이 누구를 가리키는지 누적해서 판정한다.

    참고자료 §8 세 번째 함정이 말하는 사고를 막는 장치다. 41세 학부모가
    "우리 아이 지원 뭐 있나요"라고 물었을 때 본인 나이 41로 연령 필터가
    걸리면 아동 제도가 전부 탈락한다.

    규칙:

    - 이번 턴에 자녀·부모 등 다른 사람이 언급되면 주체를 본인이 아닌 쪽으로
      내린다. 본인 언급까지 함께 있으면 둘 다 후보라는 뜻이므로 ``unknown``
      으로 둔다 - 어느 쪽인지 모를 때는 연령 필터를 생략하는 것이 맞다.
    - 한 번 내려간 주체는 이후 턴에서 자동으로 본인으로 돌아오지 않는다.
      "우리 아이 지원 뭐 있나요" 다음에 지역만 답했다고 주체가 바뀌면,
      사용자가 정정하지 않았는데 판정이 조용히 뒤집힌다. 잘못된 방향으로
      되돌아가는 것보다 검색이 넓은 채로 남는 쪽이 안전하다.
    - 다른 사람 언급이 한 번도 없었으면 본인으로 본다. 사용자가 자기
      생년월일을 말하는 것이 기본 상황이기 때문이다.
    """

    if signals.get("child") or signals.get("other"):
        if signals.get("self"):
            merged["age_subject"] = AgeSubject.UNKNOWN.value
        elif signals.get("child"):
            merged["age_subject"] = AgeSubject.CHILD.value
        else:
            merged["age_subject"] = AgeSubject.HOUSEHOLD_MEMBER.value
        return

    if merged.get("age_subject") in (
        AgeSubject.CHILD.value,
        AgeSubject.HOUSEHOLD_MEMBER.value,
        AgeSubject.UNKNOWN.value,
    ):
        return

    merged["age_subject"] = AgeSubject.SELF.value


def _apply_birth_date(merged: SlotState, extracted_birth_date: str | None) -> None:
    """생년월일을 저장하고 만 나이·연 나이를 파생 값으로 다시 계산한다.

    ``age``는 사용자가 말한 숫자가 아니라 항상 이 함수가 만든 파생 값이다.
    복지제도 연령 기준이 대부분 만 나이인데 사용자가 말하는 "30세"는 한국식
    세는 나이일 수 있어, 자기신고 숫자를 그대로 판정에 쓰면 경계에서 한 살
    차이로 오판정이 난다(참고자료 §8 - "만 34세 이하"는 34세 364일까지).

    ``age_year_based``(연 나이)를 함께 두는 이유는 일부 청년 정책이 출생연도
    기준이기 때문이다. 어느 쪽을 쓸지는 제도 문서의 ``age_basis``를 보고
    N9가 정하며, 이 노드는 두 값을 모두 준비만 한다.

    기준일은 매 턴 다시 계산한다. 대화가 연말을 넘기면 생일이 지나 만 나이가
    바뀌는데, 첫 턴에 계산한 값을 그대로 들고 있으면 틀린 나이로 판정한다.
    """

    if extracted_birth_date is not None:
        merged["birth_date"] = extracted_birth_date

    birth_date = parse_birth_date(merged.get("birth_date"))
    if birth_date is None:
        # 생년월일이 없으면 파생 값도 남기지 않는다. 예전 턴에 계산해 둔
        # 나이만 남아 있으면 근거 없는 나이로 판정이 진행된다.
        merged["age"] = None
        merged["age_year_based"] = None
        merged["age_ref_date"] = None
        return

    reference_date = date.today()
    age, age_year_based = calculate_ages(birth_date, reference_date)
    merged["age"] = age
    merged["age_year_based"] = age_year_based
    merged["age_ref_date"] = reference_date.isoformat()


def _normalize_region(region_raw: str | None) -> tuple[RegionScope, list[str]]:
    """원문 지역 텍스트를 계약이 요구하는 정규 지역명으로 변환한다.

    ``region_names``는 단일 이름이 아니라 상위 시도부터 누적한 계층
    리스트로 만든다 (``["서울특별시", "서울특별시 강남구"]``).
    ``rag_design.contracts.validate_region_metadata``가 완전 수식 이름 앞에
    해당 시도명이 먼저 오도록 요구하고, 수집기
    (``collectors.gov_24.region_utils.extract_region``)도 같은 형태로
    문서 메타데이터를 만들기 때문이다. 슬롯과 문서의 형태가 어긋나면 N4의
    지역 필터가 시도 단위 매칭을 통째로 놓친다.

    자체 별칭 표는 시/도 명칭까지만 보완하고, 시군구 단위 모호 명칭이나
    별칭 표 밖의 이름은 임의로 해석하지 않고 unknown으로 fail-closed한다.
    """

    if not region_raw:
        return RegionScope.UNKNOWN, []

    # 유니코드 정규화와 공백 정리를 먼저 한다. validate_region_name이 NFC와
    # 단일 공백을 요구하므로, macOS 복사(NFD)나 공백 오타("서울  강남구")가
    # 그대로 들어오면 정상 지역인데도 unknown으로 떨어진다.
    text = " ".join(unicodedata.normalize("NFC", region_raw).split())
    if not text:
        return RegionScope.UNKNOWN, []
    if text in _NATIONAL_ALIASES:
        return RegionScope.NATIONAL, [NATIONAL_REGION_NAME]

    tokens = text.split(" ")
    sido_raw, sigungu_parts = tokens[0], tokens[1:]
    if sigungu_parts and sigungu_parts[0] in _SIDO_SUFFIX_WORDS:
        sido_raw += sigungu_parts[0]
        sigungu_parts = sigungu_parts[1:]

    sido_name = _SIDO_ALIASES.get(sido_raw, sido_raw)
    try:
        validate_region_name(sido_name, allow_national=False)
    except ValueError:
        return RegionScope.UNKNOWN, []

    # 시군구 형태가 어긋나면 지역 전체를 버리지 않고 확실한 시도까지만
    # 남긴다. "서울"이라는 사실 자체는 여전히 맞기 때문이다.
    if not _is_sigungu_shaped(sigungu_parts):
        return RegionScope.REGIONAL, [sido_name]

    region_names = [sido_name]
    for depth in range(1, len(sigungu_parts) + 1):
        region_names.append(f"{sido_name} {' '.join(sigungu_parts[:depth])}")
    return RegionScope.REGIONAL, region_names


def _is_sigungu_shaped(parts: list[str]) -> bool:
    """시군구 토큰이 수집기와 같은 형태 규칙을 만족하는지 본다.

    정규 시군구 registry가 없으므로 존재 여부는 검증하지 못하고 형태만
    본다(수집기 ``region_utils._SIGUNGU_PATTERN``과 동일한 한계). 2단계는
    "시 + 구"(예: 성남시 분당구) 조합만 허용한다.
    """

    if not parts:
        return False
    if len(parts) > 2:
        return False
    if not all(part.endswith(("시", "군", "구")) for part in parts):
        return False
    if len(parts) == 2:
        return parts[0].endswith("시") and parts[1].endswith("구")
    return True
