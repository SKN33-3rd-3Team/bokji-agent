/**
 * 코드값 -> 한글 라벨 매핑 등 정적 텍스트.
 * API-09(/api/v1/config/search-options)가 단일 출처이며, 이 파일은
 * "API 응답이 오기 전까지" 쓰는 폴백 하드코딩이다(PROJECT_STRUCTURE.md
 * constants/labels.ts 역할 설명 그대로) — streamlit_ui/constants.py 값을
 * 그대로 옮겼다.
 */

import type { HardGateSlot } from "@/types/chat";

/** S05-01: 하드 게이트 슬롯 한글 라벨(SLOT_LABELS_KO) */
export const SLOT_LABELS_KO: Record<HardGateSlot, string> = {
  region: "거주 지역",
  birth_date: "생년월일",
  gender: "성별",
  income_bracket: "소득 수준",
  disability_status: "장애 등록 여부",
  employment_status: "취업 상태",
};

export const GENDER_NONE = "선택 안 함";
export const GENDER_LABELS_KO: Record<string, string> = {
  male: "남성",
  female: "여성",
};

export const DISABILITY_NONE = "선택 안 함";
export const DISABILITY_LABELS_KO: Record<string, string> = {
  registered: "등록 장애 있음",
  not_registered: "등록 장애 없음",
};

export const VETERAN_NONE = "선택 안 함";
export const VETERAN_LABELS_KO: Record<string, string> = {
  registered: "보훈대상자입니다",
  not_registered: "해당 없음",
};

export const INCOME_BRACKET_NONE = "선택 안 함";
export const INCOME_BRACKET_LABELS_KO: Record<string, string> = {
  under_30: "기초생활수급 수준(중위소득 30% 이하)",
  pct_30_50: "차상위 수준(중위소득 30~50%)",
  pct_50_75: "중위소득 50~75%",
  pct_75_100: "중위소득 75~100%",
  pct_100_150: "중위소득 100~150%",
  over_150: "중위소득 150% 초과",
};

export const HOUSEHOLD_TYPE_LABELS_KO: Record<string, string> = {
  single_parent: "한부모",
  multi_child: "다자녀",
  multicultural: "다문화",
  grandparent: "조손",
  single_person: "1인 가구",
  north_korean_defector: "북한이탈주민",
  care_leaver: "자립준비청년",
  facility_leaver: "시설퇴소",
  newlywed: "신혼부부",
};

/** 채팅 대화에서만 추출되는 취업 상태 — 마이페이지 프로필에는 없다(API-08 비고) */
export const EMPLOYMENT_STATUS_LABELS_KO: Record<string, string> = {
  employed: "재직",
  self_employed: "자영업",
  job_seeking: "구직",
  student: "학생",
  not_working: "무직",
};

/** API-09 로딩 전 폴백 — sido_options (17개 시/도) */
export const FALLBACK_SIDO_OPTIONS = [
  "서울특별시",
  "부산광역시",
  "대구광역시",
  "인천광역시",
  "광주광역시",
  "대전광역시",
  "울산광역시",
  "세종특별자치시",
  "경기도",
  "강원특별자치도",
  "충청북도",
  "충청남도",
  "전북특별자치도",
  "전라남도",
  "경상북도",
  "경상남도",
  "제주특별자치도",
];

/** API-09 로딩 전 폴백 — signup_interest_options (회원가입 전용 4종) */
export const FALLBACK_SIGNUP_INTEREST_OPTIONS = ["임신/출산", "노인/어르신", "농어업인", "청년"];

/** API-09 로딩 전 폴백 — sidebar_interest_options (사이드바 지원조건 전체 8종) */
export const FALLBACK_SIDEBAR_INTEREST_OPTIONS = [
  "기초생활수급/차상위",
  "장애인",
  "임신/출산",
  "국가유공자/보훈",
  "노인/어르신",
  "한부모/조손가정",
  "농어업인",
  "청년",
];

/** API-09 로딩 전 폴백 — interest_field_options (관심 분야 19종) */
export const FALLBACK_INTEREST_FIELD_OPTIONS = [
  "육아",
  "출산",
  "보육",
  "주거",
  "취업",
  "일자리",
  "창업",
  "교육",
  "장학",
  "의료",
  "건강",
  "돌봄",
  "노인",
  "장애인",
  "저소득",
  "청년",
  "다문화",
  "한부모",
  "지원금",
];

/** API-09 로딩 전 폴백 — income_bracket_options ({code,label}[]) */
export const FALLBACK_INCOME_BRACKET_OPTIONS = Object.entries(INCOME_BRACKET_LABELS_KO).map(
  ([code, label]) => ({ code, label }),
);

/** API-09 로딩 전 폴백 — household_type_options ({code,label}[]) */
export const FALLBACK_HOUSEHOLD_TYPE_OPTIONS = Object.entries(HOUSEHOLD_TYPE_LABELS_KO).map(
  ([code, label]) => ({ code, label }),
);

export const FALLBACK_DEFAULT_TOP_K = 5;

/** S03-05: 예시 질문 버튼 3종(EXAMPLE_PROMPTS) — 옵션이 아니라 정적 예시 문장이라 API-09에 없음 */
export const EXAMPLE_PROMPTS = [
  "서울특별시에 살고 2021년 3월 5일생 남자아이입니다. 기초생활수급자이고 장애는 없고 무직이에요. 유아학비 누리과정 지원 받을 수 있나요?",
  "부산에 사는 1997년생 여성입니다. 미혼이고 회사 다니고 중위소득 60%예요. 청년 주거 지원 뭐가 있나요?",
  "지원금 뭐 받을 수 있는지 알려주세요.",
];

/** S-03 채팅 입력창 placeholder(chat.py chat_input) */
export const CHAT_INPUT_PLACEHOLDER = "메시지를 입력하세요 (예: 서울 사는 2021년 3월생 아이 유아학비 지원 되나요?)";

/** S-03 인트로 카드 문구 */
export const INTRO_GREETING_TITLE = "👋 안녕하세요, 복지 에이전트입니다";
export const INTRO_GREETING_BODY =
  "거주 지역과 기본 정보를 알려주시면 받을 수 있는 지원 제도를 찾아 자격 · 지원금 · 중복수급을 근거와 함께 확인해 드려요.";
export const INTRO_GREETING_HINT = "아래 예시를 눌러 바로 시작할 수 있어요.";

/** S06-05: 공식 확인 안내 고정 문구(GUIDANCE_OFFICIAL) */
export const GUIDANCE_OFFICIAL =
  "정확한 내용은 복지로 또는 국가법령정보센터 공식 페이지에서 확인해 주세요.";

/**
 * 홈 화면(로그인 직후 진입) — API-14(자동추천_API_정의서_v1.0.xlsx)
 * POST /api/v1/chat/recommendations를 요청 바디 없이 1회 호출한 결과를
 * 보여주는 화면. 예전에는 이 화면이 진짜 추천 API가 없어 API-10에 고정
 * 질의 문자열을 실어 보내는 방식으로 흉내 냈지만(HOME_INITIAL_QUERY),
 * API-14가 생기면서 그 트릭은 더 이상 필요 없다.
 */
export const HOME_CAPTION = "마이페이지에 저장된 정보로 맞춤 지원 제도를 자동으로 찾아드려요.";
export const HOME_LOADING_MESSAGE = "마이페이지 정보로 맞춤 지원 제도를 찾고 있어요...";
