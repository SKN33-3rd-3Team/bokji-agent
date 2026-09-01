"""한글 라벨, 선택지와 경로 등 UI가 공유하는 순수 상수."""

from __future__ import annotations

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
}

# 자격 판정별 배지 색/아이콘 (config.toml 의 greenColor/redColor/grayColor 와 짝)
VERDICT_STYLE: dict[str, dict[str, str]] = {
    "충족": {"color": "green", "icon": ":material/check_circle:"},
    "미충족": {"color": "red", "icon": ":material/cancel:"},
    "미확인": {"color": "gray", "icon": ":material/help:"},
}

# 정책 원문 섹션 코드 → 한글 라벨
SECTION_LABELS_KO: dict[str, str] = {
    "purpose": "목적",
    "support_target": "지원 대상",
    "eligibility_criteria": "선정 기준",
    "support_details": "지원 내용",
    "application_method": "신청 방법",
    "application_period": "신청 기간",
    "legal_basis": "근거 법령",
    "support_conditions": "지원 조건",
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
    "지역",                    # 1위 (전국 아님)
    "기초생활수급/차상위",     # 2위
    "장애인",                  # 3위
    "임신/출산",               # 4위
    "국가유공자/보훈",         # 5위
    "노인/어르신",             # 6위
    "소득기준",                # 7위 (중위소득 등)
    "한부모/조손가정",         # 8위
    "농어업인",                # 9위
    "청년",                    # 10위
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
