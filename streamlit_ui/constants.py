"""한글 라벨, 선택지와 경로 등 UI가 공유하는 순수 상수."""

from __future__ import annotations

from datetime import date

from rag_design.contracts import SUBSIDY_DETAIL_SECTIONS

from . import ROOT

# ── 경로 ────────────────────────────────────────────────────────────
VECTOR_DB_DIR = ROOT / "data" / "vector_db"

DEFAULT_TOP_K = 5

# ── 슬롯/판정 라벨 ─────────────────────────────────────────────────
SLOT_LABELS_KO: dict[str, str] = {
    "region": "거주 지역",
    "birth_date": "생년월일",
    "gender": "성별",
    "income_bracket": "소득 수준",
    "disability_status": "장애 등록 여부",
    "employment_status": "취업 상태",
    "household_types": "가구 유형",
}

# 성별 선택지 (회원가입/마이페이지). 코드값("male"/"female")은 하드게이트
# 슬롯 계약(graph.slot_schema.Gender)과 맞춘다 - 회원가입 때 고른 성별이
# service.ask(known_gender=...)로 그대로 슬롯에 들어간다.
GENDER_NONE = "선택 안 함"
GENDER_LABELS_KO: dict[str, str] = {"male": "남성", "female": "여성"}
GENDER_CODE_BY_LABEL_KO: dict[str, str] = {
    label: code for code, label in GENDER_LABELS_KO.items()
}

# 생년월일 위젯(회원가입/마이페이지)의 선택 가능 범위. 서버 쪽 검증
# (auth.service._clean_birth_date)의 "미래 불가 / 120년 초과 불가"와 같은
# 경계를 화면에서도 걸어 둔다 — 두 곳이 따로 있는 값이니, 서버 규칙이
# 바뀌면 여기도 같이 봐야 한다.
BIRTH_DATE_MIN = date(date.today().year - 120, 1, 1)

# 장애 등록 여부 선택지 (회원가입/마이페이지). 코드값은
# graph.slot_schema.DisabilityStatus와 맞춘다.
DISABILITY_NONE = "선택 안 함"
DISABILITY_LABELS_KO: dict[str, str] = {
    "registered": "등록 장애 있음", "not_registered": "등록 장애 없음",
}
DISABILITY_CODE_BY_LABEL_KO: dict[str, str] = {
    label: code for code, label in DISABILITY_LABELS_KO.items()
}

# 보훈대상자 여부 선택지. 코드값은 graph.slot_schema.VeteranStatus와 맞춘다.
# (검색 질의만 넓히는 용도로 쓰인다 - 하드 필터는 아니다. service.ask()
# docstring 참고.)
VETERAN_NONE = "선택 안 함"
VETERAN_LABELS_KO: dict[str, str] = {
    "registered": "보훈대상자입니다", "not_registered": "해당 없음",
}
VETERAN_CODE_BY_LABEL_KO: dict[str, str] = {
    label: code for code, label in VETERAN_LABELS_KO.items()
}

# 소득 수준(기준중위소득 대비 구간) 선택지. 코드값은
# graph.slot_schema.IncomeBracket과 맞춘다. under_30에 기초수급 수준을
# 포함한다(강사님 주제 컨펌 - 기초수급을 별도 필드로 분리하지 않는다).
INCOME_BRACKET_NONE = "선택 안 함"
INCOME_BRACKET_LABELS_KO: dict[str, str] = {
    "under_30": "기초생활수급 수준(중위소득 30% 이하)",
    "pct_30_50": "차상위 수준(중위소득 30~50%)",
    "pct_50_75": "중위소득 50~75%",
    "pct_75_100": "중위소득 75~100%",
    "pct_100_150": "중위소득 100~150%",
    "over_150": "중위소득 150% 초과",
}
INCOME_BRACKET_CODE_BY_LABEL_KO: dict[str, str] = {
    label: code for code, label in INCOME_BRACKET_LABELS_KO.items()
}

# 가구유형 선택지(중복 선택 가능). 코드값은 graph.slot_schema.HouseholdType과
# 맞춘다. "신혼부부"는 별도 슬롯이 아니라 이 목록의 값 하나다.
HOUSEHOLD_TYPE_LABELS_KO: dict[str, str] = {
    "single_parent": "한부모", "multi_child": "다자녀", "multicultural": "다문화",
    "grandparent": "조손", "single_person": "1인 가구",
    "north_korean_defector": "북한이탈주민", "care_leaver": "자립준비청년",
    "facility_leaver": "시설퇴소", "newlywed": "신혼부부",
}
HOUSEHOLD_TYPE_CODE_BY_LABEL_KO: dict[str, str] = {
    label: code for code, label in HOUSEHOLD_TYPE_LABELS_KO.items()
}

# 자격 판정별 배지 색/아이콘 (config.toml 의 greenColor/redColor/grayColor 와 짝)
VERDICT_STYLE: dict[str, dict[str, str]] = {
    "충족": {"color": "green", "icon": ":material/check_circle:"},
    "미충족": {"color": "red", "icon": ":material/cancel:"},
    "미확인": {"color": "gray", "icon": ":material/help:"},
}

# 정책 원문 섹션 코드 → 한글 라벨
# section_type 목록의 유일한 출처는 rag_design.contracts.SUBSIDY_DETAIL_SECTIONS다
# (service.py의 _DETAIL_SECTION_TYPES도 같은 곳에서 가져온다) - 예전엔 이 dict를
# 따로 손으로 관리해서, 구비서류 3종을 추가할 때 두 파일을 같이 고쳐야 했다(#48).
SECTION_LABELS_KO: dict[str, str] = {
    section_type: display_label for section_type, _, display_label in SUBSIDY_DETAIL_SECTIONS
}

# ── 안내 문구 / 예시 ───────────────────────────────────────────────
EXAMPLE_PROMPTS: list[str] = [
    "서울특별시에 살고 2021년 3월 5일생 남자아이입니다. "
    "기초생활수급자이고 장애는 없고 무직이에요. 유아학비 누리과정 지원 받을 수 있나요?",
    "부산에 사는 1997년생 여성입니다. 미혼이고 회사 다니고 중위소득 60%예요. "
    "청년 주거 지원 뭐가 있나요?",
    "지원금 뭐 받을 수 있는지 알려주세요.",
]

GUIDANCE_OFFICIAL = (
    "정확한 내용은 복지로(bokjiro.go.kr) 또는 국가법령정보센터(law.go.kr) "
    "공식 페이지에서 확인해 주세요."
)

# ── 검색 튜닝: 지원조건 후보 ──────────────────────────────────────
# 데이터 분석 "정책에 가장 많이 등장하는 조건" 상위 1~10위. 고르면 정책 검색
# 쿼리에 더해진다(내부 변수명은 interests 를 그대로 씀).
INTEREST_OPTIONS: list[str] = [
    # "지역",                    # 1위 (전국 아님) 지역은 옵션이 너무 많아서 llm 하드게이트로 input -> 제외
    "기초생활수급/차상위",     # 2위
    "장애인",                  # 3위
    "임신/출산",               # 4위
    "국가유공자/보훈",         # 5위
    "노인/어르신",             # 6위
    # "소득기준",                # 7위 (중위소득 등) 별도 옵션 존재해서 llm 하드게이트로 input -> 제외
    "한부모/조손가정",         # 8위
    "농어업인",                # 9위
    "청년",                    # 10위
]

# 회원가입/마이페이지 폼 전용: INTEREST_OPTIONS에서 이미 같은 내용을 별도
# 구조화 필드로 물어보는 항목을 뺀 목록(회원가입 화면 스크린샷 리뷰,
# 2026-09-14 - "해당하는 지원조건" pills와 아래 필드가 겹쳐서 같은 걸 두 번
# 묻고 있었다). 대응 관계:
#   "국가유공자/보훈"       -> "국가유공자/보훈대상자 여부"(VETERAN_LABELS_KO)
#   "장애인"                -> "장애 등록 여부"(DISABILITY_LABELS_KO)
#   "기초생활수급/차상위"    -> "소득 수준"의 최하위 구간들(INCOME_BRACKET_LABELS_KO)
#   "한부모/조손가정"        -> "가구 유형"의 "한부모"/"조손"(HOUSEHOLD_TYPE_LABELS_KO)
# 이 필드들을 채우면 service.ask()가 known_veteran_status/disability_status/
# income_bracket/household_types로 그대로 반영하므로(veteran은
# _VETERAN_INTEREST_KEYWORD를 통해 interests에마저 실린다) 기능은 그대로
# 유지된다. 채팅 사이드바(chat.py)의 "지원조건" 선택지는 대화별 검색 힌트라
# 성격이 달라 그대로 INTEREST_OPTIONS 전체를 쓴다 - 거기는 중복 질문이 아니다.
_SIGNUP_INTEREST_DUPLICATES = frozenset({
    "국가유공자/보훈", "장애인", "기초생활수급/차상위", "한부모/조손가정",
})
SIGNUP_INTEREST_OPTIONS: list[str] = [
    item for item in INTEREST_OPTIONS if item not in _SIGNUP_INTEREST_DUPLICATES
]

# ── 검색 튜닝: 관심 분야 ──────────────────────────────────────────
# 지원조건(자격 범주)과 달리 "무엇에 관한 지원인지"(주제/영역)를 좁힌다.
# 고르면 지원조건과 함께 같은 interests 슬롯으로 정책 검색 쿼리에 더해진다.
INTEREST_FIELD_OPTIONS: list[str] = [
    "육아", "출산", "보육", "주거", "취업", "일자리", "창업", "교육", "장학",
    "의료", "건강", "돌봄", "노인", "장애인", "저소득", "청년", "다문화", "한부모",
    "지원금",
]

# ── 시·도 목록 (회원가입 화면 등) ─────────────────────────────────
SIDO_OPTIONS: list[str] = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
    "경상남도", "제주특별자치도",
]

# ── 채팅 아바타 ────────────────────────────────────────────────────
USER_AVATAR = ":material/person:"
BOT_AVATAR = "💬"
