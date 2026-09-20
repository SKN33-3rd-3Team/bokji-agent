import { apiClient } from "./client";
import type {
  ChatMessageRequest,
  ChatProgress,
  ChatResponse,
  FollowupRequest,
  PolicyQuestionRequest,
  PolicyQuestionResponse,
  SessionResetResponse,
} from "@/types/chat";

/**
 * 진행률 조회 토큰을 실어 보내는 헤더. 클라이언트가 요청마다 새로 만들고,
 * 같은 값으로 getChatProgress(token)를 폴링해 진행 막대를 그린다 — 상담
 * 시작(API-10/14)은 session_id를 서버가 만들기 때문에, 응답이 오기 전까지
 * 진행 상황을 조회할 이름이 이것밖에 없다(backend/app/api/v1/chat.py).
 */
const PROGRESS_HEADER = "X-Progress-Token";

function progressHeaders(progressToken?: string) {
  return progressToken ? { headers: { [PROGRESS_HEADER]: progressToken } } : undefined;
}

/** API-10 POST /api/v1/chat/messages — 새 상담 세션 시작(N1 진입점) */
export async function sendMessage(
  payload: ChatMessageRequest,
  progressToken?: string,
): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>(
    "/api/v1/chat/messages",
    payload,
    progressHeaders(progressToken),
  );
  return data;
}

/**
 * API-14 POST /api/v1/chat/recommendations — 로그인 직후 1회, 저장된 DB
 * 프로필만으로 자동 정책 추천. 요청 바디를 보내지 않는다(message, known_ 계열,
 * top_k, session_id 전부 서버가 받지 않음 - 자동추천_API_정의서_v1.0.xlsx
 * API-14 시트, Request 섹션). 응답은 API-10/11과 같은 ChatResponse.
 * 진행률 토큰만 헤더로 함께 보낸다 — 바디/쿼리로 보내면 서버가 400으로 막는다.
 */
export async function getAutoRecommendations(progressToken?: string, signal?: AbortSignal): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>(
    "/api/v1/chat/recommendations",
    undefined,
    { ...progressHeaders(progressToken), signal, timeout: 5000 },
  );
  return data;
}

/** API-11 POST /api/v1/chat/sessions/{session_id}/followup — 되묻기 응답 제출 */
export async function submitFollowup(
  sessionId: string,
  payload: FollowupRequest,
  progressToken?: string,
): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>(
    `/api/v1/chat/sessions/${sessionId}/followup`,
    payload,
    progressHeaders(progressToken),
  );
  return data;
}

/**
 * GET /api/v1/chat/progress/{token} — 진행 중인 상담의 현재 단계.
 * 기록이 없어도 200 + status:"unknown"이라 폴링이 오류 배너를 띄우지 않는다.
 */
export async function getChatProgress(token: string, signal?: AbortSignal): Promise<ChatProgress> {
  const { data } = await apiClient.get<ChatProgress>(
    `/api/v1/chat/progress/${encodeURIComponent(token)}`,
    { signal, timeout: 5000 },
  );
  return data;
}

/** API-12 POST /api/v1/chat/sessions/{session_id}/policies/{policy_id}/questions */
export async function askPolicyQuestion(
  sessionId: string,
  policyId: string,
  payload: PolicyQuestionRequest,
): Promise<PolicyQuestionResponse> {
  const { data } = await apiClient.post<PolicyQuestionResponse>(
    `/api/v1/chat/sessions/${sessionId}/policies/${policyId}/questions`,
    payload,
  );
  return data;
}

/** API-13 DELETE /api/v1/chat/sessions/{session_id} — 새 상담 시작(세션 초기화) */
export async function resetSession(sessionId: string): Promise<SessionResetResponse> {
  const { data } = await apiClient.delete<SessionResetResponse>(
    `/api/v1/chat/sessions/${sessionId}`,
  );
  return data;
}
