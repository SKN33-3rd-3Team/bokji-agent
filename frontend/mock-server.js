/**
 * 임시 QA용 mock 백엔드 — 실제 backend/ 구현이 아니라, 시안(디자인) 확인을
 * 위해 API_정의서.xlsx 계약대로 고정 응답만 돌려주는 스크립트다.
 * `backend/` 아래 진짜 FastAPI 구현이 생기면 이 파일은 지워도 된다.
 *
 * 실행: node mock-server.js  (포트 8000, VITE_API_BASE_URL 기본값과 동일)
 */
import http from "node:http";
import crypto from "node:crypto";

const PORT = 8000;
const COOKIE_NAME = "bokji_session";

const usersById = new Map();
const sessionsBySid = new Map(); // sid -> userId
const chatSessions = new Map(); // chatSessionId -> { userId, step }

let nextUserId = 1;

// mock 응답이 너무 빨라서(수 ms) 로딩 인디케이터를 눈으로 확인할 수 없다는
// QA 피드백 반영 — 채팅/정책 문의처럼 실제로 시간이 걸리는 API에만 일부러
// 지연을 준다. 실제 서비스와는 무관한 QA 전용 값이다.
const QA_DELAY_MS = 700;
// 그래프를 실제로 도는 API(상담/자동추천/되묻기)는 진짜 서버에서 수십 초가
// 걸린다. 진행 막대(ChatProgressBar)가 단계별로 차오르는지 QA에서 눈으로
// 확인하려면 mock도 그만큼은 끌어야 해서, 이 세 엔드포인트만 따로 둔다.
const QA_GRAPH_DELAY_MS = 6000;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// 진행률 mock — X-Progress-Token으로 요청이 들어오면 시작 시각만 기억해두고,
// GET /chat/progress/{token}에서 경과 시간으로 단계를 흉내 낸다. 실제 백엔드는
// 그래프 노드가 끝날 때마다 기록한다(src/rag_chatbot/progress.py).
const progressByToken = new Map();
const MOCK_PROGRESS_STEPS = [
  "말씀하신 내용에서 필요한 정보를 정리하고 있어요",
  "받을 수 있는 지원 제도를 찾고 있어요",
  "조건이 원문에 실제로 있는지 확인하고 있어요",
  "자격 충족 여부를 판정하고 있어요",
  "받을 수 있는 지원금을 계산하고 있어요",
  "찾은 제도를 정리하고 있어요",
  "안내 문장을 작성하고 있어요",
];

function beginProgress(req) {
  const token = req.headers["x-progress-token"];
  if (typeof token === "string" && token) progressByToken.set(token, Date.now());
  return token;
}

function progressSnapshot(token) {
  const startedAt = progressByToken.get(token);
  if (!startedAt) return { status: "unknown", fraction: 0, message: null, completed_steps: 0, total_steps: 0, elapsed_seconds: 0 };
  const elapsedMs = Date.now() - startedAt;
  const total = 14;
  const stepIndex = Math.min(
    MOCK_PROGRESS_STEPS.length - 1,
    Math.floor((elapsedMs / QA_GRAPH_DELAY_MS) * MOCK_PROGRESS_STEPS.length),
  );
  const completed = Math.min(total, Math.floor((elapsedMs / QA_GRAPH_DELAY_MS) * total));
  const done = elapsedMs >= QA_GRAPH_DELAY_MS;
  return {
    status: done ? "done" : "running",
    fraction: done ? 1 : Math.min(completed / total, 0.95),
    message: done ? "완료" : MOCK_PROGRESS_STEPS[stepIndex],
    completed_steps: completed,
    total_steps: total,
    elapsed_seconds: Math.round(elapsedMs / 100) / 10,
  };
}

/** UI 힌트("8자 이상, 영문·숫자·특수문자를 섞어 주세요")와 동일한 정책. */
function passwordViolations(password) {
  const violations = [];
  if (!password || password.length < 8) violations.push("비밀번호는 8자 이상이어야 합니다.");
  if (!/[a-zA-Z]/.test(password ?? "")) violations.push("영문을 포함해야 합니다.");
  if (!/[0-9]/.test(password ?? "")) violations.push("숫자를 포함해야 합니다.");
  if (!/[^a-zA-Z0-9]/.test(password ?? "")) violations.push("특수문자를 포함해야 합니다.");
  return violations;
}

function newUser(payload) {
  const id = nextUserId++;
  const user = {
    id,
    email: payload.email ?? `demo${id}@example.com`,
    password: payload.password || "test1234!",
    display_name: payload.name ?? payload.display_name ?? `테스트유저${id}`,
    created_at: new Date().toISOString(),
    // 기본 정보(선택) 항목은 실제로 입력 안 하면 빈 값 그대로 저장해야 한다.
    // 예전엔 fallback 기본값(서울/여성/2003년생 등)을 채워 넣었는데, 그러면
    // API-14(자동 추천)의 "가입 선택 8개가 모두 없어도 초기/계산 질문 없이
    // 0건을 반환한다"는 정상 0건 시나리오를 mock으로 재현할 방법이 없었다.
    region: payload.region ?? "",
    gender: payload.gender ?? "",
    birth_date: payload.birth_date ?? "",
    disability_status: payload.disability_status ?? "",
    veteran_status: payload.veteran_status ?? "",
    income_bracket: payload.income_bracket ?? "",
    household_types: payload.household_types ?? [],
    interests: payload.interests ?? [],
    marketing_opt_in: payload.marketing_opt_in ?? false,
    failedAttempts: 0,
    lockedUntil: 0,
  };
  usersById.set(id, user);
  return user;
}

// src/rag_chatbot/auth/lockout.py 기본값과 동일(AUTH_MAX_LOGIN_ATTEMPTS=5, AUTH_LOCKOUT_MINUTES=15).
const MAX_LOGIN_ATTEMPTS = 5;
const LOCKOUT_MS = 15 * 60 * 1000;

/** 저장된 password/잠금 카운터 필드는 절대 응답 바디로 내보내지 않는다(API-01/02/04/05 계약). */
function publicUser(user) {
  const { password, failedAttempts, lockedUntil, ...rest } = user;
  return rest;
}

function findUserByEmail(email) {
  for (const u of usersById.values()) if (u.email === email) return u;
  return null;
}

function parseCookies(req) {
  const header = req.headers.cookie;
  const out = {};
  if (!header) return out;
  for (const part of header.split(";")) {
    const idx = part.indexOf("=");
    if (idx === -1) continue;
    out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  }
  return out;
}

function getCurrentUser(req) {
  const sid = parseCookies(req)[COOKIE_NAME];
  if (!sid) return null;
  const userId = sessionsBySid.get(sid);
  if (!userId) return null;
  return usersById.get(userId) ?? null;
}

function setSessionCookie(res, sid) {
  res.setHeader("Set-Cookie", `${COOKIE_NAME}=${sid}; Path=/; HttpOnly; SameSite=Lax`);
}

function clearSessionCookie(res) {
  res.setHeader("Set-Cookie", `${COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res, status, body) {
  const text = JSON.stringify(body);
  // API-14 시트 "Response Headers": 성공·오류 응답은 Content-Type +
  // Cache-Control: no-store. 브라우저 저장 방지일 뿐 서버 세션 수명과는 별개.
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(text);
}

function sendError(res, status, errorCode, message, extra = {}) {
  sendJson(res, status, { error_code: errorCode, message, ...extra });
}

const SEARCH_OPTIONS = {
  sido_options: [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시",
    "울산광역시", "세종특별자치시", "경기도", "강원특별자치도", "충청북도", "충청남도",
    "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주특별자치도",
  ],
  gender_options: [
    { code: "male", label: "남성" },
    { code: "female", label: "여성" },
  ],
  disability_status_options: [
    { code: "registered", label: "등록 장애 있음" },
    { code: "not_registered", label: "등록 장애 없음" },
  ],
  veteran_status_options: [
    { code: "registered", label: "보훈대상자입니다" },
    { code: "not_registered", label: "해당 없음" },
  ],
  income_bracket_options: [
    { code: "under_30", label: "기초생활수급 수준(중위소득 30% 이하)" },
    { code: "pct_30_50", label: "차상위 수준(중위소득 30~50%)" },
    { code: "pct_50_75", label: "중위소득 50~75%" },
    { code: "pct_75_100", label: "중위소득 75~100%" },
    { code: "pct_100_150", label: "중위소득 100~150%" },
    { code: "over_150", label: "중위소득 150% 초과" },
  ],
  household_type_options: [
    { code: "single_parent", label: "한부모" },
    { code: "multi_child", label: "다자녀" },
    { code: "multicultural", label: "다문화" },
    { code: "grandparent", label: "조손" },
    { code: "single_person", label: "1인 가구" },
    { code: "north_korean_defector", label: "북한이탈주민" },
    { code: "care_leaver", label: "자립준비청년" },
    { code: "facility_leaver", label: "시설퇴소" },
    { code: "newlywed", label: "신혼부부" },
  ],
  signup_interest_options: ["임신/출산", "노인/어르신", "농어업인", "청년"],
  sidebar_interest_options: [
    "기초생활수급/차상위", "장애인", "임신/출산", "국가유공자/보훈",
    "노인/어르신", "한부모/조손가정", "농어업인", "청년",
  ],
  interest_field_options: [
    "육아", "출산", "보육", "주거", "취업", "일자리", "창업", "교육", "장학",
    "의료", "건강", "돌봄", "노인", "장애인", "저소득", "청년", "다문화", "한부모", "지원금",
  ],
  // 일부러 프론트의 하드코딩 폴백(5)과 다른 값으로 둬서 "서버 값과 실제로
  // 동기화되는지"를 눈으로 바로 확인할 수 있게 한다.
  default_top_k: 7,
};

function emptyLlmStatus() {
  return { enabled: false, model: null, calls: 0, successes: 0, failures: 0, messages: [] };
}

function mockLlmStatus() {
  return {
    enabled: true,
    model: "mock-llm-v1",
    calls: 3,
    successes: 3,
    failures: 0,
    messages: ["mock 응답입니다 — 실제 LLM 호출 아님"],
  };
}

function emptyTiming() {
  return { phases: [], node_path: [] };
}

function mockTiming() {
  return {
    phases: [
      { name: "정책 검색", count: 1, total_s: 0.8, avg_s: 0.8, share: 0.4 },
      { name: "자격 판정", count: 3, total_s: 0.6, avg_s: 0.2, share: 0.3 },
      { name: "근거 검증", count: 3, total_s: 0.6, avg_s: 0.2, share: 0.3 },
    ],
    node_path: [
      { node: "N1", title: "슬롯 확인", seconds: 0.1 },
      { node: "N5", title: "정책 검색", seconds: 0.8 },
      { node: "N9", title: "근거 검증", seconds: 0.6 },
    ],
  };
}

function buildPolicy({
  rank, id, title, badge, eligibility_status, eligibility_reasons,
  amount, amount_label, amount_period, duplicate_status, duplicate_note,
}) {
  return {
    rank,
    policy_id: id,
    title,
    badge,
    eligibility_status,
    eligibility_reasons,
    verification_checked: ["소득 기준", "거주 지역"],
    verification_unchecked: ["부양가족 소득"],
    verification_note: "일부 항목은 서류 제출 후 최종 확인됩니다.",
    amount,
    amount_label,
    amount_period,
    amount_is_maximum: true,
    amount_per_unit: "가구당",
    amount_total: amount,
    amount_min: null,
    amount_max: amount,
    total_amount_min: null,
    total_amount_max: amount,
    duplicate_status,
    duplicate_note,
    duplicate_clause_kind: duplicate_status === "불가" ? "household" : "other",
    duplicate_conflicts: [],
    household_limit_clauses: duplicate_status === "불가" ? ["동일 가구 내 1인만 수급 가능"] : [],
    needs_confirmation: eligibility_status === "미확인" ? ["소득 구간 재확인 필요"] : [],
    related_law: [
      { law_name: "국민기초생활 보장법", source_url: "https://www.law.go.kr" },
    ],
    detail: {
      purpose: `${title} 제도의 목적은 대상 가구의 생활 안정과 자립을 지원하는 것입니다.`,
      support_target: "소득·재산 기준을 충족하는 가구",
      eligibility_criteria: "기준 중위소득 이하 가구, 신청일 기준 해당 지역 거주자",
      support_details: amount_label,
      application_method: "읍/면/동 주민센터 방문 신청 또는 복지로 온라인 신청",
      application_period: "상시 접수",
      legal_basis: "국민기초생활 보장법 제7조",
      // 줄바꿈으로 한 줄에 하나씩 — DocumentChipList가 이 형태만 칩으로 쪼갠다(콤마는 안 쪼갬).
      required_documents: "신분증\n주민등록등본\n가족관계증명서",
      required_documents_official: "행정정보 공동이용으로 자동 확인(소득/재산 조회)",
      required_documents_self: "임대차계약서(해당 시)\n기타 증빙서류",
      region_names: ["전국"],
      region_scope: "national",
      age_start: null,
      age_end: null,
      organization: "보건복지부",
      source_url: "https://www.bokjiro.go.kr",
      source_name: "복지로",
    },
  };
}

// 사이드바 "정책 후보 수"(top_k) 슬라이더 값에 맞춰 실제로 개수가 달라지는 걸
// 보여주기 위한 후보 풀 — 실제 검색은 아니고 이 풀에서 앞에서부터 top_k개를 자른다.
const POLICY_POOL = [
  {
    id: "policy-001", title: "청년 월세 특별지원", badge: "가장 적합",
    eligibility_status: "충족", eligibility_reasons: ["만 19~34세 무주택 청년", "소득 기준 충족"],
    amount: 200000, amount_label: "월 20만원 (최대 12개월)", amount_period: "month",
    duplicate_status: "가능", duplicate_note: "타 주거 지원과 중복 수급 가능",
  },
  {
    id: "policy-002", title: "영유아 보육료 지원(누리과정)", badge: "확인 필요",
    eligibility_status: "미확인", eligibility_reasons: ["소득 구간 확인 필요"],
    amount: 280000, amount_label: "월 28만원 상당(어린이집 보육료)", amount_period: "month",
    duplicate_status: "조건부", duplicate_note: "양육수당과는 중복 불가, 유아학비와는 가능",
  },
  {
    id: "policy-003", title: "기초생활수급자 생계급여", badge: "자격 미충족",
    eligibility_status: "미충족", eligibility_reasons: ["소득인정액이 선정 기준을 초과"],
    amount: null, amount_label: "가구원 수별 차등 지급", amount_period: null,
    duplicate_status: "불가", duplicate_note: "동일 가구 내 중복 수급 불가",
  },
  {
    id: "policy-004", title: "청년 구직활동지원금", badge: "자격 충족",
    eligibility_status: "충족", eligibility_reasons: ["만 18~34세 미취업 청년", "졸업 후 2년 이내"],
    amount: 500000, amount_label: "월 50만원 (최대 6개월)", amount_period: "month",
    duplicate_status: "가능", duplicate_note: "타 고용지원 사업과 중복 수급 가능",
  },
  {
    id: "policy-005", title: "산모·신생아 건강관리 지원", badge: "확인 필요",
    eligibility_status: "미확인", eligibility_reasons: ["출산 예정일/출산일 기준 확인 필요"],
    amount: null, amount_label: "본인부담금 소득 구간별 차등 지원", amount_period: null,
    duplicate_status: "조건부", duplicate_note: "지자체 자체 사업과 중복 여부는 지역마다 다름",
  },
  {
    id: "policy-006", title: "장애인연금", badge: "자격 미충족",
    eligibility_status: "미충족", eligibility_reasons: ["등록 장애 정보 없음"],
    amount: null, amount_label: "기초급여 + 부가급여(등급별 차등)", amount_period: "month",
    duplicate_status: "미확인", duplicate_note: null,
  },
  {
    id: "policy-007", title: "농어업인 국민연금보험료 지원", badge: "자격 충족",
    eligibility_status: "충족", eligibility_reasons: ["농어업인 확인서 발급 대상"],
    amount: 45000, amount_label: "월 최대 4만 5천원 보험료 지원", amount_period: "month",
    duplicate_status: "가능", duplicate_note: "타 농업 직불금과 중복 수급 가능",
  },
  {
    id: "policy-008", title: "다자녀가구 전기요금 할인", badge: "자격 충족",
    eligibility_status: "충족", eligibility_reasons: ["가구 내 자녀 3인 이상"],
    amount: 16000, amount_label: "월 전기요금 30% 할인(최대 1만 6천원)", amount_period: "month",
    duplicate_status: "가능", duplicate_note: "다른 요금 할인 제도와 중복 적용 가능",
  },
];

function mockPolicies(topK) {
  const count = Math.min(Math.max(Number(topK) || 5, 1), POLICY_POOL.length);
  return POLICY_POOL.slice(0, count).map((p, i) => buildPolicy({ ...p, rank: i + 1 }));
}

function chatResponseAnswered(sessionId, topK) {
  const policies = mockPolicies(topK);
  return {
    status: "answered",
    session_id: sessionId,
    question: null,
    missing_slots: [],
    interrupt_id: null,
    calc_missing_slots: [],
    calc_missing_choices: [],
    calc_slot_inputs: [],
    slot_conflicts: null,
    answer_status: "complete",
    final_answer: `말씀하신 조건에 맞는 지원 제도 ${policies.length}건을 찾았어요. 아래에서 자격·지원금·중복수급 여부를 확인해 보세요.`,
    final_citations: [],
    policies,
    output_json: {},
    output_text: "",
    output_markdown: `### 검색 결과\n${policies.map((p) => `- ${p.title}`).join("\n")}`,
    llm_status: mockLlmStatus(),
    timing: mockTiming(),
  };
}

// API-14 정상 0건 — 문구는 정의서 예시 그대로(마침표 없음).
function chatResponseEmptyRecommendation(sessionId) {
  const text = "현재 정보로 추천할 정책이 없습니다";
  return {
    status: "answered",
    session_id: sessionId,
    question: null,
    missing_slots: [],
    interrupt_id: null,
    calc_missing_slots: [],
    calc_missing_choices: [],
    calc_slot_inputs: [],
    slot_conflicts: null,
    answer_status: "complete",
    final_answer: text,
    final_citations: [],
    policies: [],
    output_json: {
      status: "answered",
      session_id: sessionId,
      answer_status: "complete",
      final_answer: text,
      summary: { checked: 0, eligible: 0, not_eligible_or_unknown: 0 },
      profile: [],
      evidence_count: 0,
      final_citations: [],
      policies: [],
    },
    output_text: text,
    output_markdown: text,
    llm_status: emptyLlmStatus(),
    timing: emptyTiming(),
  };
}

function chatResponseNeedsRegion(sessionId) {
  return {
    status: "needs_input",
    session_id: sessionId,
    // N3(request_missing_slots.py)는 부족한 하드 게이트 슬롯을 한 턴에 전부
    // 모아 번호 목록으로 묻는다 — 지역만 먼저 묻고 다음 턴에 나머지를 물으면
    // 되묻기 왕복이 늘어 MAX_SLOT_ASKS 상한에 먼저 닿는 문제가 있었기 때문
    // (llm_gateway.py generate_followup_question 문서 참고). mock도 이 다중
    // 슬롯 동시 요청 형태를 재현해야 프론트가 missing_slots 전체를 실제로
    // 처리하는지 검증할 수 있다.
    question:
      "아래 정보를 알려주시면 더 정확하게 확인해드릴게요.\n1. 거주 지역이 어디신가요?\n2. 성별이 어떻게 되시나요?\n3. 생년월일이 언제신가요?\n모르시거나 말씀하기 어려운 항목은 '모름'이라고 답하셔도 됩니다.",
    missing_slots: ["region", "gender", "birth_date"],
    interrupt_id: null,
    calc_missing_slots: [],
    calc_missing_choices: [],
    calc_slot_inputs: [],
    slot_conflicts: null,
    answer_status: null,
    final_answer: null,
    final_citations: [],
    policies: [],
    output_json: {},
    output_text: "",
    output_markdown: "",
    llm_status: emptyLlmStatus(),
    timing: emptyTiming(),
  };
}

/** N10a(request_calc_info) 계산 되묻기 — 슬롯 위젯 + 정책별 선택형 */
function chatResponseNeedsCalcInfo(sessionId) {
  return {
    status: "needs_input",
    session_id: sessionId,
    question:
      "정확한 지원금액을 계산하려면 아래 정보가 필요해요.\n1. 혼인 상태 (미혼/기혼/이혼/사별)\n2. 가구원 수\n3. [첫만남이용권] 어떤 방식에 해당하시나요? (자연분만 / 제왕절개 중 선택)\n모르시거나 말씀하기 어려우면 '모름'이라고 답하셔도 됩니다. 이 경우 정확한 금액 대신 안내만 드려요.",
    missing_slots: [],
    interrupt_id: "mock-interrupt-1",
    calc_missing_slots: ["marital_status", "household_size"],
    calc_missing_choices: [
      { policy_id: "policy-2", labels: ["자연분만", "제왕절개"], policy_title: "첫만남이용권" },
    ],
    calc_slot_inputs: [
      {
        slot: "marital_status",
        label: "혼인 상태 (미혼/기혼/이혼/사별)",
        input_type: "select",
        options: [
          { value: "single", label: "미혼" },
          { value: "married", label: "기혼" },
          { value: "divorced", label: "이혼" },
          { value: "bereaved", label: "사별" },
        ],
        minimum: null,
        maximum: null,
      },
      {
        slot: "household_size",
        label: "가구원 수",
        input_type: "number",
        options: [],
        minimum: 1,
        maximum: 10,
      },
    ],
    slot_conflicts: null,
    answer_status: null,
    final_answer: null,
    final_citations: [],
    policies: [],
    output_json: {},
    output_text: "",
    output_markdown: "",
    llm_status: mockLlmStatus(),
    timing: emptyTiming(),
  };
}

function chatResponseConflict(sessionId) {
  return {
    status: "needs_input",
    session_id: sessionId,
    question:
      "회원 정보에는 소득 수준이(가) '중위소득 75~100%'로 되어 있는데, 방금은 '중위소득 50~75%'라고 하셨어요.\n회원 정보에는 가구 유형이(가) '다자녀'로 되어 있는데, 방금은 '한부모, 1인 가구'라고 하셨어요. 어느 쪽이 맞는지 다시 알려주세요.",
    missing_slots: [],
    interrupt_id: null,
    calc_missing_slots: [],
    calc_missing_choices: [],
    calc_slot_inputs: [],
    slot_conflicts: {
      income_bracket: { profile: "중위소득 75~100%", chat: "중위소득 50~75%" },
      household_types: { profile: "다자녀", chat: "한부모, 1인 가구" },
    },
    answer_status: null,
    final_answer: null,
    final_citations: [],
    policies: [],
    output_json: {},
    output_text: "",
    output_markdown: "",
    llm_status: mockLlmStatus(),
    timing: emptyTiming(),
  };
}

const routes = [
  {
    method: "POST",
    pattern: /^\/api\/v1\/auth\/signup$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      if (findUserByEmail(body.email)) {
        return sendError(res, 409, "USERNAME_TAKEN", "이미 가입된 이메일입니다.");
      }
      const violations = passwordViolations(body.password);
      if (violations.length) {
        return sendError(res, 400, "PASSWORD_POLICY_VIOLATION", violations.join(" "), { violations });
      }
      const user = newUser(body);
      const sid = crypto.randomUUID();
      sessionsBySid.set(sid, user.id);
      setSessionCookie(res, sid);
      sendJson(res, 201, { user: publicUser(user) });
    },
  },
  {
    method: "POST",
    pattern: /^\/api\/v1\/auth\/login$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const user = findUserByEmail(body.email);
      // 실제 API-02: 계정이 없어도 INVALID_CREDENTIALS로 동일하게 응답한다(계정 존재 여부 추측 방지) —
      // 회원가입 안 한 이메일로는 로그인되지 않는다. 먼저 /signup으로 계정을 만들어야 한다.
      if (!user) {
        return sendError(res, 401, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.");
      }
      const now = Date.now();
      if (user.lockedUntil > now) {
        return sendError(res, 423, "ACCOUNT_LOCKED", "로그인 시도 초과로 계정이 잠겼습니다.", {
          remaining_seconds: Math.ceil((user.lockedUntil - now) / 1000),
        });
      }
      if (user.password !== body.password) {
        user.failedAttempts += 1;
        if (user.failedAttempts >= MAX_LOGIN_ATTEMPTS) {
          user.lockedUntil = now + LOCKOUT_MS;
          return sendError(res, 423, "ACCOUNT_LOCKED", "로그인 시도 초과로 계정이 잠겼습니다.", {
            remaining_seconds: Math.ceil(LOCKOUT_MS / 1000),
          });
        }
        return sendError(res, 401, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.");
      }
      user.failedAttempts = 0;
      user.lockedUntil = 0;
      const sid = crypto.randomUUID();
      sessionsBySid.set(sid, user.id);
      setSessionCookie(res, sid);
      sendJson(res, 200, { user: publicUser(user) });
    },
  },
  {
    method: "POST",
    pattern: /^\/api\/v1\/auth\/logout$/,
    handler: async (req, res) => {
      const sid = parseCookies(req)[COOKIE_NAME];
      if (sid) sessionsBySid.delete(sid);
      clearSessionCookie(res);
      sendJson(res, 200, {});
    },
  },
  {
    method: "GET",
    pattern: /^\/api\/v1\/users\/me$/,
    handler: async (req, res) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      sendJson(res, 200, publicUser(user));
    },
  },
  {
    method: "PATCH",
    pattern: /^\/api\/v1\/users\/me$/,
    handler: async (req, res) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      const body = await readBody(req);
      Object.assign(user, body);
      sendJson(res, 200, publicUser(user));
    },
  },
  {
    method: "POST",
    pattern: /^\/api\/v1\/users\/me\/password$/,
    handler: async (req, res) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      const body = await readBody(req);
      if (body.current_password !== user.password) {
        return sendError(res, 401, "INVALID_CURRENT_PASSWORD", "현재 비밀번호가 올바르지 않습니다.");
      }
      if (body.new_password === body.current_password) {
        return sendError(res, 400, "SAME_AS_CURRENT", "새 비밀번호는 현재 비밀번호와 달라야 합니다.");
      }
      const violations = passwordViolations(body.new_password);
      if (violations.length) {
        return sendError(res, 400, "PASSWORD_POLICY_VIOLATION", violations.join(" "), { violations });
      }
      user.password = body.new_password;
      sendJson(res, 200, { message: "비밀번호가 변경되었습니다." });
    },
  },
  {
    method: "DELETE",
    pattern: /^\/api\/v1\/users\/me$/,
    handler: async (req, res) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      const body = await readBody(req);
      if (body.password !== user.password) {
        return sendError(res, 401, "INVALID_CREDENTIALS", "비밀번호가 올바르지 않습니다.");
      }
      usersById.delete(user.id);
      const sid = parseCookies(req)[COOKIE_NAME];
      if (sid) sessionsBySid.delete(sid);
      clearSessionCookie(res);
      sendJson(res, 200, {});
    },
  },
  {
    method: "GET",
    pattern: /^\/api\/v1\/users\/me\/chat-defaults$/,
    handler: async (req, res) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      sendJson(res, 200, {
        known_region: user.region || null,
        known_gender: user.gender || null,
        known_birth_date: user.birth_date || null,
        known_disability_status: user.disability_status || null,
        known_income_bracket: user.income_bracket || null,
        known_household_types: user.household_types ?? [],
        known_veteran_status: user.veteran_status || null,
        extra_interests: user.interests ?? [],
        employment_status_available: false,
      });
    },
  },
  {
    method: "GET",
    pattern: /^\/api\/v1\/config\/search-options$/,
    handler: async (_req, res) => sendJson(res, 200, SEARCH_OPTIONS),
  },
  {
    // 실제 백엔드는 구동 직후 임베딩 모델을 백그라운드로 로드한다 - mock은
    // 로드할 모델이 없으니 항상 준비 완료.
    method: "GET",
    pattern: /^\/api\/v1\/config\/status$/,
    handler: async (_req, res) =>
      sendJson(res, 200, { status: "ready", message: "준비가 끝났습니다.", seconds: 0 }),
  },
  {
    method: "GET",
    pattern: /^\/api\/v1\/chat\/progress\/([^/]+)$/,
    handler: async (req, res, [token]) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      sendJson(res, 200, progressSnapshot(decodeURIComponent(token)));
    },
  },
  {
    method: "POST",
    pattern: /^\/api\/v1\/chat\/messages$/,
    handler: async (req, res) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      const body = await readBody(req);
      beginProgress(req);
      await delay(QA_GRAPH_DELAY_MS);
      const sessionId = crypto.randomUUID();
      // top_k(사이드바 "정책 후보 수" 슬라이더)는 이 첫 호출에만 실려 오고, 이후 followup(API-11)
      // 요청에는 없으므로 세션 상태에 기억해뒀다가 답변 단계에서 그대로 쓴다.
      chatSessions.set(sessionId, { userId: user.id, step: 1, topK: body.top_k });
      sendJson(res, 200, chatResponseNeedsRegion(sessionId));
    },
  },
  {
    // API-14(자동추천_API_정의서_v1.0.xlsx) — 로그인 직후 1회, 요청 바디 없이
    // 호출. 서버가 인증 쿠키로 식별한 계정의 DB 프로필만으로 판단한다(client
    // user_id/profile/known_*/message/top_k/session_id는 받지 않음).
    method: "POST",
    pattern: /^\/api\/v1\/chat\/recommendations$/,
    handler: async (req, res) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      beginProgress(req);
      await delay(QA_GRAPH_DELAY_MS);
      const sessionId = crypto.randomUUID();
      chatSessions.set(sessionId, { userId: user.id, step: 4 });
      const hasAnyProfile = Boolean(
        user.region ||
          user.gender ||
          user.birth_date ||
          user.disability_status ||
          user.income_bracket ||
          user.veteran_status ||
          (user.household_types && user.household_types.length > 0),
      );
      if (!hasAnyProfile) {
        return sendJson(res, 200, chatResponseEmptyRecommendation(sessionId));
      }
      // 서버의 현재 후보 수 기본값(5)을 그대로 쓴다 - 클라이언트가 top_k를 보내지 않는다.
      sendJson(res, 200, chatResponseAnswered(sessionId, undefined));
    },
  },
  {
    method: "POST",
    pattern: /^\/api\/v1\/chat\/sessions\/([^/]+)\/followup$/,
    handler: async (req, res, [sessionId]) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      const followupBody = await readBody(req);
      beginProgress(req);
      await delay(QA_GRAPH_DELAY_MS);
      const state = chatSessions.get(sessionId) ?? { userId: user.id, step: 1 };
      state.step += 1;
      chatSessions.set(sessionId, state);
      if (state.step === 2) return sendJson(res, 200, chatResponseConflict(sessionId));
      // step 3은 지원금 계산 되묻기(N10a) — CalcFollowupForm을 QA에서 볼 수
      // 있게 재현한다. 여기서 calc_answers를 보내면 다음 턴이 답변이 된다.
      if (state.step === 3) return sendJson(res, 200, chatResponseNeedsCalcInfo(sessionId));
      // 실제 백엔드는 같은 항목을 값과 "모름" 양쪽에 담은 답변을 400으로
      // 거절한다(merge_structured_calc_answer). 폼이 둘 중 하나만 채우는지
      // mock에서도 드러나게 같은 검사를 둔다.
      const calcAnswers = followupBody?.calc_answers;
      if (calcAnswers) {
        const overlapSlot = (calcAnswers.unknown_slots ?? []).find(
          (field) => field in (calcAnswers.slots ?? {}),
        );
        const overlapChoice = (calcAnswers.unknown_choices ?? []).find(
          (policyId) => policyId in (calcAnswers.choices ?? {}),
        );
        if (overlapSlot || overlapChoice) {
          return sendError(res, 400, "VALIDATION_ERROR", "같은 항목에 값과 '모름'을 함께 보낼 수 없습니다.");
        }
      }
      return sendJson(res, 200, chatResponseAnswered(sessionId, state.topK));
    },
  },
  {
    method: "POST",
    pattern: /^\/api\/v1\/chat\/sessions\/([^/]+)\/policies\/([^/]+)\/questions$/,
    handler: async (req, res, [, policyId]) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      await delay(QA_DELAY_MS);
      const body = await readBody(req);
      // "응답 불가"가 반복될 때 화면이 이유를 보여주는지 QA에서 확인할 수
      // 있도록, 질문에 "모름"/"왜"가 들어가면 안내(guidance) 경로를 재현한다.
      const asksWhy = /모름|왜|안 ?돼|안돼/.test(body.question ?? "");
      if (asksWhy) {
        return sendJson(res, 200, {
          kind: "guidance",
          text: `이 채팅은 '${policyId}' 정책에 대한 질문만 답할 수 있어요. 다른 정책이나 새로운 검색은 메인 화면에서 다시 물어봐 주세요.`,
          evidence_quotes: [],
          reason: "evidence_not_found",
          reason_message: "LLM이 제시한 근거 발췌가 정책 원문에 없어 답변을 버렸습니다.",
          llm_status: mockLlmStatus(),
        });
      }
      sendJson(res, 200, {
        kind: "answer",
        text: `"${body.question ?? ""}"에 대한 답변입니다 (mock). 정책 ${policyId}의 지원 조건은 상세 화면의 안내를 참고해 주세요.`,
        evidence_quotes: ["관련 법령 제3조에 따라 소득 기준을 충족하는 가구가 대상입니다."],
        reason: "ok",
        reason_message: "근거 검증까지 통과했습니다.",
        llm_status: mockLlmStatus(),
      });
    },
  },
  {
    method: "DELETE",
    pattern: /^\/api\/v1\/chat\/sessions\/([^/]+)$/,
    handler: async (req, res, [sessionId]) => {
      const user = getCurrentUser(req);
      if (!user) return sendError(res, 401, "UNAUTHENTICATED", "로그인이 필요합니다.");
      chatSessions.delete(sessionId);
      sendJson(res, 200, { message: "세션이 초기화되었습니다." });
    },
  },
];

const server = http.createServer(async (req, res) => {
  const origin = req.headers.origin ?? "http://localhost:5173";
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Access-Control-Allow-Credentials", "true");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, X-Progress-Token");

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    return res.end();
  }

  const url = new URL(req.url, `http://localhost:${PORT}`);
  const match = routes.find((r) => r.method === req.method && r.pattern.test(url.pathname));
  if (!match) return sendError(res, 404, "NOT_FOUND", "존재하지 않는 엔드포인트입니다.");

  const params = url.pathname.match(match.pattern)?.slice(1) ?? [];
  try {
    await match.handler(req, res, params);
  } catch (err) {
    console.error(err);
    sendError(res, 500, "INTERNAL_ERROR", "mock 서버 오류");
  }
});

server.listen(PORT, () => {
  console.log(`[mock-server] QA용 mock 백엔드가 http://localhost:${PORT} 에서 실행 중입니다.`);
});

// 데모 도중 예상 못 한 예외로 서버 프로세스 전체가 죽지 않도록 하는 안전망.
process.on("uncaughtException", (err) => console.error("[mock-server] uncaughtException:", err));
process.on("unhandledRejection", (err) => console.error("[mock-server] unhandledRejection:", err));
