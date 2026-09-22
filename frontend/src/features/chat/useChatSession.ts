import { useCallback, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { resetSession as resetSessionApi, sendMessage as sendMessageApi, submitFollowup } from "@/api/chatApi";
import type { CalculationAnswers, ChatMessageRequest, ChatResponse, ChatTurn } from "@/types/chat";

export type PolicyViewMode = "list" | "detail" | "compare";

/** 요청 한 건을 가리키는 진행률 조회 토큰. crypto.randomUUID가 없는 환경(구형
 *  브라우저, http 원격 접속)에서도 막대가 사라지지 않게 폴백을 둔다. */
export function newProgressToken(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  return uuid ?? `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** sendMutation 입력 — 서버로 보낼 내용과, 화면 이력에 남길 사용자 발화. */
interface SendVariables {
  /** API-10/11 본문(되묻기 턴에서는 message만 뽑아 쓴다) */
  payload: ChatMessageRequest;
  /** 구조화 계산 답변(API-11 calc_answers). 있으면 message 대신 이걸 보낸다. */
  calcAnswers?: CalculationAnswers;
  /** 말풍선에 남길 사용자 쪽 문장 */
  userText: string;
}

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
  // 진행률 폴링(useChatProgress)이 읽는 "지금 도는 요청"의 토큰. 요청마다 새로
  // 만들어 헤더로 함께 보내고, 같은 값으로 진행 상황을 조회한다 — 첫 상담은
  // session_id를 서버가 만들기 때문에 응답 전에는 조회할 이름이 없다.
  const [progressToken, setProgressToken] = useState<string | null>(null);
  const [progressStartedAt, setProgressStartedAt] = useState<number | null>(null);
  // 최초 입력은 후속 요청의 기본값으로 보존한다. 새 질문에서는 payload의
  // 최신 사이드바 설정이 우선하며, 계산 답변은 기존 검색을 이어간다.
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
    mutationFn: async ({ payload, calcAnswers }: SendVariables): Promise<ChatResponse> => {
      // 되묻기(session_id 있음)는 새 토큰 대신 session_id 자체를 진행률
      // 조회 키로 쓴다. progress.py의 추적 키가 session_id이고
      // progress_token은 그 위에 거는 별칭일 뿐이라(_begin_progress) 동작은
      // 동일하되, 서버가 응답하기 전까지 새 토큰엔 별칭이 아직 안 걸려있어
      // useChatProgress가 매번 빈 상태(gcTime/staleTime 0)로 시작하는 문제를
      // 피한다 - 이미 진행 중이던 막대가 되묻기 제출 순간 0%로 리셋됐다가
      // 다시 점프하는 것처럼 보였다(계산 되묻기 폼에서 특히 눈에 띔).
      // 최초 메시지(API-10, 아직 session_id 없음)는 그대로 새 토큰을 쓴다.
      const token = sessionIdRef.current ?? newProgressToken();
      setProgressToken(token);
      setProgressStartedAt(Date.now());
      // session_id 보유 여부로 API-10(최초)/API-11(진행 중)을 분기 호출한다
      // (S03-06 요구사항).
      if (sessionIdRef.current) {
        // message와 calc_answers는 둘 중 하나만 보낸다(백엔드
        // FollowupRequest.require_one_answer) — 계산 답변일 때는 message를 뺀다.
        const body = calcAnswers
          ? { calc_answers: calcAnswers, ...initialContextRef.current }
          : { ...initialContextRef.current, ...payload };
        return submitFollowup(sessionIdRef.current, body, token);
      }
      const { message: _message, ...context } = payload;
      initialContextRef.current = context;
      return sendMessageApi(payload, token);
    },
    // 보낸 말은 서버 응답을 기다리지 않고 바로 화면에 올린다. 상담 한 번이
    // 수십 초~수 분 걸리는데 그동안 자기가 뭘 보냈는지 화면에 없으면
    // "전송이 안 된 건가" 싶어 같은 말을 다시 보내게 된다
    // (usePolicyQuestion이 이미 쓰는 것과 같은 낙관적 갱신).
    onMutate: ({ userText }: SendVariables) => {
      setMessages((prev) => [...prev, { role: "user", text: userText }]);
    },
    onSuccess: (response) => {
      setMessages((prev) => [
        ...prev,
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
    (payload: ChatMessageRequest) =>
      sendMutation.mutateAsync({ payload, userText: payload.message }),
    [sendMutation],
  );

  /**
   * S05-03: 지원금 계산 되묻기(N10a)에 구조화 답변으로 응답한다.
   * 자유 문장으로 보내면 백엔드가 다시 파싱해야 해서 값이 어긋날 수 있는데,
   * calc_answers는 interrupt_id와 선택지까지 서버가 직접 검증한다.
   */
  const sendCalcAnswers = useCallback(
    (answers: CalculationAnswers, userText: string) =>
      sendMutation.mutateAsync({ payload: { message: userText }, calcAnswers: answers, userText }),
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
      setProgressToken(null);
      setProgressStartedAt(null);
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
    progressToken,
    progressStartedAt,
    setProgressToken,
    send,
    sendCalcAnswers,
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
