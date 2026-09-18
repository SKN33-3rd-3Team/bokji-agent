import { useCallback, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { askPolicyQuestion } from "@/api/chatApi";
import type { PolicyQuestionTurn } from "@/types/chat";

/**
 * S-08 정책 문의 다이얼로그 — 히스토리는 다이얼로그가 열려 있는 동안만
 * 로컬로 쌓이고 닫으면 소실된다(서버 미저장, S08-04 비고 그대로).
 */
export function usePolicyQuestion(sessionId: string | null, policyId: string | null) {
  const [history, setHistory] = useState<PolicyQuestionTurn[]>([]);

  const mutation = useMutation({
    mutationFn: async (question: string) => {
      if (!sessionId || !policyId) {
        throw new Error("session_id/policy_id가 없어 정책 문의를 보낼 수 없습니다.");
      }
      return askPolicyQuestion(sessionId, policyId, { question });
    },
    onMutate: (question: string) => {
      setHistory((prev) => [...prev, { role: "user", text: question }]);
    },
    onSuccess: (response) => {
      setHistory((prev) => [
        ...prev,
        { role: "assistant", text: response.text, kind: response.kind, evidenceQuotes: response.evidence_quotes },
      ]);
    },
  });

  const ask = useCallback((question: string) => mutation.mutateAsync(question), [mutation]);
  const resetHistory = useCallback(() => setHistory([]), []);

  return {
    history,
    ask,
    isAsking: mutation.isPending,
    error: mutation.error,
    resetHistory,
  };
}
