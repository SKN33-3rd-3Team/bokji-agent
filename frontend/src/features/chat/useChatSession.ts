import { useCallback, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { resetSession as resetSessionApi, sendMessage as sendMessageApi, submitFollowup } from "@/api/chatApi";
import type { ChatMessageRequest, ChatResponse, ChatTurn } from "@/types/chat";

export type PolicyViewMode = "list" | "detail" | "compare";

/**
 * PROJECT_STRUCTURE.md 3.4 — ChatPage 로컬 상태 하나로 S-03~S-07/S-10을
 * 전부 구현한다. list/detail/compare 전환은 API 재호출 없이 같은
 * ChatResponse.policies 배열을 재사용한다.
 */
export function useChatSession() {
  const [messages, setMessages] = useState<ChatTurn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [policyView, setPolicyView] = useState<PolicyViewMode>("list");
  const [selectedPolicyId, setSelectedPolicyId] = useState<string | null>(null);
  // 최초 턴(API-10)에 보낸 top_k/extra_interests/known_*를 기억해뒀다가
  // 되묻기(API-11) 요청에도 그대로 실어 보낸다. 백엔드가 지금 당장은 이
  // 값들을 안 쓰더라도(top_k는 체크포인터가 보존, 나머지는 아직 파라미터가
  // 없어 무시함) 최초 입력값이 요청 바디에서 조용히 빠지지 않도록 한다
  // (코드리뷰 반영, 2026-09-18).
  const initialContextRef = useRef<Omit<ChatMessageRequest, "message"> | null>(null);
  // sessionId state는 setSessionId 호출 후 다음 렌더가 커밋돼야 읽는 쪽에
  // 반영된다. resetConversation() 직후 곧바로 send()를 호출하는 화면(HomePage의
  // "새 상담 시작" 등)에서는 그 사이 리렌더가 아직 안 끝나 mutationFn 클로저가
  // 방금 지운 stale sessionId를 그대로 봐서, 이미 삭제된 세션으로 되묻기(API-11)
  // 요청을 잘못 보내는 레이스가 실제로 있었다(회귀 테스트로 확인). ref는
  // setSessionId와 같은 자리에서 동기적으로 같이 갱신해 항상 최신값을 보장한다.
  const sessionIdRef = useRef<string | null>(null);

  const latestResponse = [...messages].reverse().find((m) => m.response)?.response ?? null;

  const sendMutation = useMutation({
    mutationFn: async (payload: ChatMessageRequest): Promise<ChatResponse> => {
      // session_id 보유 여부로 API-10(최초)/API-11(진행 중)을 분기 호출한다
      // (S03-06 요구사항).
      if (sessionIdRef.current) {
        return submitFollowup(sessionIdRef.current, { message: payload.message, ...initialContextRef.current });
      }
      const { message: _message, ...context } = payload;
      initialContextRef.current = context;
      return sendMessageApi(payload);
    },
    onSuccess: (response, variables) => {
      setMessages((prev) => [
        ...prev,
        { role: "user", text: variables.message },
        { role: "assistant", text: response.final_answer ?? response.question ?? "", response },
      ]);
      if (response.session_id) {
        sessionIdRef.current = response.session_id;
        setSessionId(response.session_id);
      }
      if (response.status === "answered") {
        setPolicyView("list");
        setSelectedPolicyId(null);
      }
    },
  });

  const send = useCallback(
    (payload: ChatMessageRequest) => sendMutation.mutateAsync(payload),
    [sendMutation],
  );

  /**
   * API-14(자동 추천)처럼 이미 서버가 준 ChatResponse를 세션의 "첫 턴"으로
   * 그대로 심는다 — API-10/11을 거치지 않으므로 send()가 아니라 별도 함수로
   * 둔다. 사용자가 타이핑한 문장이 없어 messages에는 assistant 턴만 쌓는다.
   */
  const hydrate = useCallback((response: ChatResponse) => {
    setMessages((prev) => [...prev, { role: "assistant", text: response.final_answer ?? response.question ?? "", response }]);
    if (response.session_id) {
      sessionIdRef.current = response.session_id;
      setSessionId(response.session_id);
    }
    if (response.status === "answered") {
      setPolicyView("list");
      setSelectedPolicyId(null);
    }
  }, []);

  const resetMutation = useMutation({
    mutationFn: async () => {
      // S03-02: session_id가 없으면(아직 대화 시작 전) API 호출 없이 화면만 초기화한다.
      if (sessionIdRef.current) await resetSessionApi(sessionIdRef.current);
    },
    onSuccess: () => {
      sessionIdRef.current = null;
      setMessages([]);
      setSessionId(null);
      setPolicyView("list");
      setSelectedPolicyId(null);
      initialContextRef.current = null;
    },
  });

  const openDetail = useCallback((policyId: string) => {
    setSelectedPolicyId(policyId);
    setPolicyView("detail");
  }, []);

  const backToList = useCallback(() => {
    setPolicyView("list");
    setSelectedPolicyId(null);
  }, []);

  const openCompare = useCallback(() => setPolicyView("compare"), []);

  return {
    messages,
    sessionId,
    policyView,
    selectedPolicyId,
    latestResponse,
    send,
    hydrate,
    isSending: sendMutation.isPending,
    sendError: sendMutation.error,
    resetConversation: resetMutation.mutateAsync,
    isResetting: resetMutation.isPending,
    resetError: resetMutation.error,
    openDetail,
    backToList,
    openCompare,
    setPolicyView,
  };
}
