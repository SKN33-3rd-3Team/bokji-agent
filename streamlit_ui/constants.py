"""한글 라벨 매핑, 선택지 목록, 경로 상수 — UI 전반이 공유하는 순수 상수.

여기에는 Streamlit 호출이나 무거운 import 를 두지 않는다. ``AbstentionReason``
만 예외적으로 가져오는데, enum 값을 한글 문구에 대응시키기 위해서다.
"""

from __future__ import annotations

from rag_design.policy import AbstentionReason

from . import ROOT

# ── 경로 ────────────────────────────────────────────────────────────
SUBSIDY_SAMPLE = ROOT / "data" / "samples" / "subsidy_documents_sample.jsonl"
LAW_SAMPLE = ROOT / "data" / "samples" / "law_documents_sample.jsonl"
RUNTIME_DIR = ROOT / ".runtime" / "vector_db"

# ── 파이프라인 튜닝 ─────────────────────────────────────────────────
# 증거 게이트(N7) ↔ 재검색 노드(N6/N8) 왕복 상한. 종류별 재시도는 1회뿐이라
# 정상 흐름은 3~4회 안에 끝난다. 상한은 방어용.
MAX_EVIDENCE_LOOPS = 8

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

ABSTENTION_REASON_KO: dict[str, str] = {
    AbstentionReason.NO_EVIDENCE.value: "검색된 근거가 부족합니다",
    AbstentionReason.SAFETY.value: "안전 정책상 답변할 수 없습니다",
    AbstentionReason.CONFLICT.value: "근거끼리 서로 어긋납니다",
    AbstentionReason.STALE.value: "근거의 시행일 정보를 확인할 수 없습니다",
}

# 파이프라인 결과 종류 → st.status 완료 라벨(진행 표시 마무리 문구)
STATUS_LABELS_KO: dict[str, str] = {
    "answer": "상담 완료",
    "needs_input": "추가 정보가 필요해요",
    "abstain": "답변을 보류했어요",
    "no_candidates": "해당하는 제도를 찾지 못했어요",
    "error": "처리 중 오류가 발생했어요",
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

# ── 검색 튜닝: 관심 분야 ──────────────────────────────────────────
# 지원조건(자격 범주)과 달리 "무엇에 관한 지원인지"(주제/영역)를 좁힌다.
# 고르면 지원조건과 함께 정책 검색 쿼리에 더해진다(같은 interests 슬롯).
INTEREST_FIELD_OPTIONS: list[str] = [
    "육아", "출산", "보육", "주거", "취업", "일자리", "창업", "교육", "장학",
    "의료", "건강", "돌봄", "노인", "장애인", "저소득", "청년", "다문화", "한부모",
    "지원금",
]

# ── "파악한 정보" 요약용 슬롯값 → 한글 매핑 ─────────────────────────
GENDER_KO = {"male": "남성", "female": "여성"}
INCOME_KO = {
    "under_30": "기초생활수급 수준(중위소득 30% 이하)",
    "pct_30_50": "차상위 수준(중위소득 30~50%)",
    "pct_50_75": "중위소득 50~75%",
    "pct_75_100": "중위소득 75~100%",
    "pct_100_150": "중위소득 100~150%",
    "over_150": "중위소득 150% 초과",
}
DISABILITY_KO = {"registered": "장애 등록", "not_registered": "장애 없음"}
EMPLOYMENT_KO = {
    "employed": "재직", "job_seeking": "구직", "self_employed": "자영업",
    "student": "학생", "not_working": "무직",
}
MARITAL_KO = {
    "single": "미혼", "married": "기혼", "divorced": "이혼", "bereaved": "사별",
}
PREGNANCY_KO = {"pregnant": "임신 중", "postpartum": "산후", "none": "해당 없음"}
HOUSEHOLD_KO = {
    "single_parent": "한부모", "multi_child": "다자녀", "multicultural": "다문화",
    "grandparent": "조손", "single_person": "1인 가구",
    "north_korean_defector": "북한이탈주민", "care_leaver": "자립준비청년",
    "facility_leaver": "시설퇴소",
}

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
