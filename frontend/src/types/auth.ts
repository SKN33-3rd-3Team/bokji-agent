/**
 * API_정의서.xlsx API-01/02/04~09 계약과 1:1 대응.
 * 여기 필드명은 HTTP 계약(API_정의서.xlsx, backend/app/schemas/auth.py) 기준이다.
 * src/rag_chatbot/auth/service.py의 내부 함수 파라미터명(username 등)과는
 * 다를 수 있다 - 예: 여기 email은 그쪽 username에 대응(값은 이메일, 이름만 다름).
 * backend/app/services/auth_adapter.py가 이 계약과 내부 함수 사이를 매핑한다.
 */

export type Gender = "male" | "female";
export type DisabilityStatus = "registered" | "not_registered";
export type VeteranStatus = "registered" | "not_registered";
export type IncomeBracket =
  | "under_30"
  | "pct_30_50"
  | "pct_50_75"
  | "pct_75_100"
  | "pct_100_150"
  | "over_150";
export type HouseholdType =
  | "single_parent"
  | "multi_child"
  | "multicultural"
  | "grandparent"
  | "single_person"
  | "north_korean_defector"
  | "care_leaver"
  | "facility_leaver"
  | "newlywed";

/** API-01 응답 user 객체 / API-02 응답 user 객체와 동일 스키마 */
export interface UserSummary {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
  region: string;
  gender: string;
  birth_date: string;
  disability_status: string;
  veteran_status: string;
  income_bracket: string;
  household_types: string[];
  interests: string[];
  marketing_opt_in: boolean;
}

/** API-04 응답(마이페이지 조회) — UserSummary와 필드가 같되 id 없이 옴 */
export interface UserProfile {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
  region: string;
  gender: string;
  birth_date: string;
  disability_status: string;
  veteran_status: string;
  income_bracket: string;
  household_types: string[];
  interests: string[];
  marketing_opt_in: boolean;
}

/** API-01 Request Body */
export interface SignupRequest {
  email: string;
  password: string;
  password_confirm: string;
  name: string;
  region?: string;
  gender?: Gender;
  birth_date?: string;
  interests?: string[];
  disability_status?: DisabilityStatus;
  veteran_status?: VeteranStatus;
  income_bracket?: IncomeBracket;
  household_types?: HouseholdType[];
  marketing_opt_in?: boolean;
  terms_agreed: boolean;
  privacy_agreed: boolean;
}

/** API-02 Request Body */
export interface LoginRequest {
  email: string;
  password: string;
}

/** API-05 Request Body (PATCH — 필드 생략=미수정, 빈 값 전달=지움) */
export interface UpdateProfileRequest {
  display_name?: string;
  region?: string;
  gender?: Gender;
  birth_date?: string;
  interests?: string[];
  disability_status?: DisabilityStatus;
  veteran_status?: VeteranStatus;
  income_bracket?: IncomeBracket;
  household_types?: HouseholdType[];
}

/** API-06 Request Body */
export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

/** API-07 Request Body */
export interface DeleteAccountRequest {
  password: string;
}

/** API-08 응답 — 채팅 첫 턴 프리필용 */
export interface ChatDefaults {
  known_region: string | null;
  known_gender: Gender | null;
  known_birth_date: string | null;
  known_disability_status: DisabilityStatus | null;
  known_income_bracket: IncomeBracket | null;
  known_household_types: HouseholdType[];
  known_veteran_status: VeteranStatus | null;
  extra_interests: string[];
  /** 마이페이지에 취업 상태 컬럼이 없어 항상 false 고정 */
  employment_status_available: boolean;
}

export interface CodeLabel {
  code: string;
  label: string;
}

/** API-09 응답 — 코드값-한글라벨 옵션(단일 출처) */
export interface SearchOptions {
  sido_options: string[];
  gender_options: CodeLabel[];
  disability_status_options: CodeLabel[];
  veteran_status_options: CodeLabel[];
  income_bracket_options: CodeLabel[];
  household_type_options: CodeLabel[];
  signup_interest_options: string[];
  sidebar_interest_options: string[];
  interest_field_options: string[];
  default_top_k: number;
}

/** API 에러 응답 공통 형태 (에러 코드 표 기준) */
export interface ApiErrorBody {
  error_code: string;
  message: string;
  /** 423 ACCOUNT_LOCKED 전용 */
  remaining_seconds?: number;
  /** PASSWORD_POLICY_VIOLATION 전용 */
  violations?: string[];
}
