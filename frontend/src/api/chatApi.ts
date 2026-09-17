import { apiClient } from "./client";
import type {
  ChatMessageRequest,
  ChatResponse,
  FollowupRequest,
  PolicyQuestionRequest,
  PolicyQuestionResponse,
  SessionResetResponse,
} from "@/types/chat";

/** API-10 POST /api/v1/chat/messages — 새 상담 세션 시작(N1 진입점) */
export async function sendMessage(payload: ChatMessageRequest): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>("/api/v1/chat/messages", payload);
  return data;
}

/** API-11 POST /api/v1/chat/sessions/{session_id}/followup — 되묻기 응답 제출 */
export async function submitFollowup(
  sessionId: string,
  payload: FollowupRequest,
): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>(
    `/api/v1/chat/sessions/${sessionId}/followup`,
    payload,
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
