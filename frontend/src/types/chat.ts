/**
 * API_정의서.xlsx API-10~13 계약 + src/rag_chatbot/service.py의
 * PolicyDetail/PolicyView/ChatResponse TypedDict(모듈 docstring 기준,
 * 실제 런타임 dict와 일치 — 클래스 선언보다 docstring이 최신/완전함)와
 * 1:1 대응. 여기 필드명은 HTTP 계약(app/schemas/chat.py) 기준이며 함부로
 * 바꾸지 않는다 - 단 service.ask()의 내부 파라미터명(user_input 등)과는
 * 다를 수 있다(여기 message가 거기 user_input에 대응). 매핑은
 * backend/app/services/chat_adapter.py 책임이다.
 */

import type {
  DisabilityStatus,
  Gender,
  HouseholdType,
  IncomeBracket,
  VeteranStatus,
} from "./auth";

/** API-10 Request Body */
export interface ChatMessageRequest {
  message: string;
  top_k?: number;
  extra_interests?: string[];
  known_region?: string;
  known_gender?: Gender;
  known_birth_date?: string;
  known_disability_status?: DisabilityStatus;
  known_income_bracket?: IncomeBracket;
  known_household_types?: HouseholdType[];
  known_veteran_status?: VeteranStatus;
}

/**
 * API-11 Request Body. message 외 필드는 실제 answer_followup()이 아직
 * 받지 않아 백엔드가 무시하지만(top_k는 체크포인터가 보존), 최초 턴에
 * 보낸 값을 요청 바디에서 조용히 빠뜨리지 않기 위해 옵셔널로 동봉한다
 * (코드리뷰 반영, 2026-09-18 - useChatSession.ts 참고).
 *
 * message와 calc_answers는 **둘 중 하나만** 보낸다(backend/app/schemas/chat.py
 * FollowupRequest.require_one_answer). calc_answers는 지원금 계산 되묻기
 * (N10a)에 폼으로 답할 때 쓴다.
 */
export interface FollowupRequest {
  message?: string;
  calc_answers?: CalculationAnswers;
  top_k?: number;
  extra_interests?: string[];
  known_region?: string;
  known_gender?: Gender;
  known_birth_date?: string;
  known_disability_status?: DisabilityStatus;
  known_income_bracket?: IncomeBracket;
  known_household_types?: HouseholdType[];
  known_veteran_status?: VeteranStatus;
}

/**
 * API-11 계산 되묻기 답변(backend/app/schemas/chat.py CalculationAnswers).
 * slots 값은 select면 코드 문자열, number면 정수다(백엔드가 StrictInt로
 * 검증하므로 문자열로 보내면 400이 된다). choices는 정책별로 고른 라벨
 * 원문이며, calc_missing_choices[].labels에 있는 값이어야 한다.
 *
 * unknown_slots/unknown_choices는 "이 항목은 모름/해당 없음"이다 — 항목
 * 이름만 담는다. 그래프 내부 센티넬("unknown") 문자열을 값 자리에 넣는 게
 * 아니라는 점에 주의(공개 API는 열거형 계약에 있는 값만 받는다). 같은
 * 항목을 값과 모름 양쪽에 넣으면 400이다.
 */
export interface CalculationAnswers {
  interrupt_id: string;
  slots?: Record<string, string | number>;
  choices?: Record<string, string>;
  unknown_slots?: string[];
  unknown_choices?: string[];
}

/** API-12 Request Body */
export interface PolicyQuestionRequest {
  question: string;
}

/** service.py PolicyDetail (모듈 docstring, line 84-102) */
export interface PolicyDetail {
  purpose: string | null;
  support_target: string | null;
  eligibility_criteria: string | null;
  support_details: string | null;
  application_method: string | null;
  application_period: string | null;
  legal_basis: string | null;
  /**
   * ⚠ 배열이 아니라 원문 문자열 1개 — PROJECT_STRUCTURE.md 미해결 이슈,
   * 요구사항 정의서 S07-06/S10-01 "보류" 항목. PM/백엔드가 파싱 규칙을
   * 확정하기 전까지 칩으로 쪼개지 말고 한 줄 문자열 그대로 렌더링한다.
   */
  required_documents: string | null;
  required_documents_official: string | null;
  required_documents_self: string | null;
  region_names: string[] | null;
  region_scope: "national" | "regional" | "unknown" | null;
  age_start: number | null;
  age_end: number | null;
  organization: string | null;
  source_url: string | null;
  source_name: string | null;
}

export interface RelatedLaw {
  law_name: string;
  source_url?: string;
}

/** service.py PolicyView */
export interface PolicyView {
  rank: number;
  policy_id: string;
  title: string;
  badge: string;
  eligibility_status: "충족" | "미충족" | "미확인" | string;
  eligibility_reasons: string[];
  verification_checked: string[];
  verification_unchecked: string[];
  verification_note: string | null;
  amount: number | null;
  amount_label: string;
  amount_period: "month" | "year" | "once" | null;
  amount_is_maximum: boolean;
  amount_per_unit: string | null;
  amount_total: number | null;
  amount_min: number | null;
  amount_max: number | null;
  total_amount_min: number | null;
  total_amount_max: number | null;
  duplicate_status: "가능" | "불가" | "조건부" | "미확인" | string;
  duplicate_note: string | null;
  duplicate_clause_kind: "other" | "household" | "header" | null;
  duplicate_conflicts: Record<string, unknown>[];
  household_limit_clauses: string[];
  needs_confirmation: string[];
  related_law: RelatedLaw[];
  detail: PolicyDetail;
}

export interface LlmStatus {
  enabled: boolean;
  model: string | null;
  calls: number;
  successes: number;
  failures: number;
  messages: string[];
}

export interface TimingPhase {
  name: string;
  count: number;
  total_s: number;
  avg_s: number;
  share: number;
}

export interface TimingNode {
  node: string;
  title: string;
  seconds: number;
}

export interface Timing {
  phases: TimingPhase[];
  node_path: TimingNode[];
}

/** slot_conflicts: {슬롯명: {profile, chat}} */
export type SlotConflicts = Record<string, { profile: string; chat: string }>;

/**
 * D5 공용 필드(자동추천_API_정의서_v1.0.xlsx 계약참조 시트) — 금액 계산에
 * 필요한 슬롯을 select/number 위젯으로 묻는 계산 interrupt 응답에 쓰인다.
 * 화면은 CalcFollowupForm이 그린다(2026-09-20 구현 — 그 전까지는 스키마만
 * 있고 렌더링이 없어, 계산 되묻기가 오면 빈 슬롯 폼에 막혀 답을 보낼 수
 * 없었다). API-14(자동 추천)는 항상 null/[]/[]/[]만 받으므로 무관하다.
 */
export interface CalculationSlotInput {
  slot: string;
  label: string;
  input_type: "select" | "number";
  options: { value: string; label: string }[];
  minimum: number | null;
  maximum: number | null;
}

export interface CalculationChoice {
  policy_id: string;
  labels: string[];
  policy_title: string;
}

/** API-10/11 공용 응답(ChatResponse) */
export interface ChatResponse {
  status: "needs_input" | "answered";
  session_id: string;
  question: string | null;
  missing_slots: string[];
  interrupt_id: string | null;
  calc_missing_slots: string[];
  calc_missing_choices: CalculationChoice[];
  calc_slot_inputs: CalculationSlotInput[];
  slot_conflicts: SlotConflicts | null;
  answer_status: "complete" | "partial" | "abstained" | null;
  final_answer: string | null;
  final_citations: Record<string, unknown>[];
  policies: PolicyView[];
  output_json: Record<string, unknown>;
  output_text: string;
  output_markdown: string;
  llm_status: LlmStatus;
  timing: Timing;
}

/** API-12 응답 */
export interface PolicyQuestionResponse {
  kind: "answer" | "guidance";
  text: string;
  evidence_quotes: string[];
}

/** API-13 응답 */
export interface SessionResetResponse {
  message: string;
}

/**
 * GET /api/v1/chat/progress/{token} 응답 — 상담이 지금 어느 단계인지.
 * fraction은 어림값이라 끝나기 전에는 0.95를 넘지 않는다(백엔드
 * src/rag_chatbot/progress.py). status가 "unknown"이면 아직 서버에 기록이
 * 없다는 뜻이지 오류가 아니다.
 */
export interface ChatProgress {
  status: "running" | "done" | "failed" | "unknown";
  fraction: number;
  message: string | null;
  completed_steps: number;
  total_steps: number;
  elapsed_seconds: number;
}

/** GET /api/v1/config/status 응답 — 서버 구동 워밍업(임베딩 모델 로드) 상태 */
export interface ServerStatus {
  status: "pending" | "running" | "ready" | "failed";
  message: string;
  seconds: number | null;
}

/** S-05 되묻기용 하드 게이트 슬롯 코드 — request_missing_slots.py 순서 기준 */
export type HardGateSlot =
  | "region"
  | "gender"
  | "birth_date"
  | "income_bracket"
  | "disability_status"
  | "employment_status";

/** S-08 다이얼로그 안에서만 쌓이는 로컬 히스토리(서버 저장 없음) */
export interface PolicyQuestionTurn {
  role: "user" | "assistant";
  text: string;
  kind?: "answer" | "guidance";
  evidenceQuotes?: string[];
}

/** ChatPage 로컬 메시지 이력 아이템 */
export interface ChatTurn {
  role: "user" | "assistant";
  /** 사용자 발화 원문, 또는 봇 쪽은 question/final_answer 중 하나 */
  text: string;
  response?: ChatResponse;
}
