import { useCallback, useState } from "react";
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

  const latestResponse = [...messages].reverse().find((m) => m.response)?.response ?? null;

  const sendMutation = useMutation({
    mutationFn: async (payload: ChatMessageRequest): Promise<ChatResponse> => {
      // session_id 보유 여부로 API-10(최초)/API-11(진행 중)을 분기 호출한다
      // (S03-06 요구사항).
      return sessionId
        ? submitFollowup(sessionId, { message: payload.message })
        : sendMessageApi(payload);
    },
    onSuccess: (response, variables) => {
      setMessages((prev) => [
        ...prev,
        { role: "user", text: variables.message },
        { role: "assistant", text: response.final_answer ?? response.question ?? "", response },
      ]);
      if (response.session_id) setSessionId(response.session_id);
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

  const resetMutation = useMutation({
    mutationFn: async () => {
      // S03-02: session_id가 없으면(아직 대화 시작 전) API 호출 없이 화면만 초기화한다.
      if (sessionId) await resetSessionApi(sessionId);
    },
    onSuccess: () => {
      setMessages([]);
      setSessionId(null);
      setPolicyView("list");
      setSelectedPolicyId(null);
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
