import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { Sidebar } from "@/components/layout/Sidebar";
import { ConfirmModal } from "@/components/common/ConfirmModal";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { ChatBubble } from "@/components/chat/ChatBubble";
import { ExamplePrompts } from "@/components/chat/ExamplePrompts";
import { SlotFollowupForm } from "@/components/chat/SlotFollowupForm";
import { SlotConflictForm } from "@/components/chat/SlotConflictForm";
import { CalcFollowupForm } from "@/components/chat/CalcFollowupForm";
import { ChatProgressBar } from "@/components/chat/ChatProgressBar";
import { PolicyQuestionDialog } from "@/components/dialogs/PolicyQuestionDialog";
import { useChatSession } from "@/features/chat/useChatSession";
import { ChatPolicyResult } from "@/components/chat/ChatPolicyResult";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import { useAuth } from "@/features/auth/useAuth";
import { getChatDefaults } from "@/api/userApi";
import { ApiError, toErrorMessage } from "@/api/client";
import {
  CHAT_INPUT_PLACEHOLDER,
  GUIDANCE_OFFICIAL,
  INTRO_GREETING_BODY,
  INTRO_GREETING_HINT,
  INTRO_GREETING_TITLE,
  INTRO_REQUIRED_HINT,
  INTRO_REQUIRED_SLOTS,
  INTRO_REQUIRED_TITLE,
  SLOT_LABELS_KO,
} from "@/constants/labels";
import { followupKindOf } from "@/utils/chatQuestion";
import type { CalculationAnswers, PolicyView } from "@/types/chat";
import { FALLBACK_DEFAULT_TOP_K } from "@/constants/labels";

export function ChatPage() {
  const { user } = useAuth();
  const chat = useChatSession();

  const [input, setInput] = useState("");
  const [supportConditions, setSupportConditions] = useState<string[]>([]);
  const [interestFields, setInterestFields] = useState<string[]>([]);
  // API-09의 default_top_k(서버 값)가 로딩되기 전까지만 프론트 상수로 보여준다.
  // 사용자가 슬라이더를 직접 움직이면 그 값이 항상 우선한다.
  const [topKOverride, setTopKOverride] = useState<number | null>(null);
  const { data: searchOptions } = useSearchOptions();
  const topK = topKOverride ?? searchOptions?.default_top_k ?? FALLBACK_DEFAULT_TOP_K;
  const [askingPolicy, setAskingPolicy] = useState<PolicyView | null>(null);
  const [confirmingNewChat, setConfirmingNewChat] = useState(false);
  const [newChatError, setNewChatError] = useState<string | null>(null);

  // S03-07: 로그인 사용자는 채팅 화면 진입 시 API-08을 1회 호출해 known_* 값을 보관한다.
  const { data: chatDefaults } = useQuery({
    queryKey: ["chat-defaults"],
    queryFn: getChatDefaults,
    enabled: Boolean(user),
    staleTime: Infinity,
  });

  const extraInterests = useMemo(
    () => Array.from(new Set([...supportConditions, ...interestFields, ...(chatDefaults?.extra_interests ?? [])])),
    [supportConditions, interestFields, chatDefaults],
  );

  const submitMessage = (text: string) => {
    if (!text.trim()) return;
    setInput("");
    void chat.send({
      message: text,
      top_k: topK,
      extra_interests: extraInterests,
      known_region: chatDefaults?.known_region ?? undefined,
      known_gender: chatDefaults?.known_gender ?? undefined,
      known_birth_date: chatDefaults?.known_birth_date ?? undefined,
      known_disability_status: chatDefaults?.known_disability_status ?? undefined,
      known_income_bracket: chatDefaults?.known_income_bracket ?? undefined,
      known_household_types: chatDefaults?.known_household_types,
      known_veteran_status: chatDefaults?.known_veteran_status ?? undefined,
    });
  };

  const submitCalcAnswers = (answers: CalculationAnswers) => {
    void chat.sendCalcAnswers(answers, "지원금 계산에 필요한 정보를 입력했어요.");
  };

  const handleNewChat = async () => {
    await chat.resetConversation();
    setAskingPolicy(null);
  };

  const confirmNewChat = async () => {
    setNewChatError(null);
    try {
      await handleNewChat();
      setConfirmingNewChat(false);
    } catch (err) {
      setNewChatError(err instanceof ApiError ? err.message : "새 상담을 시작하지 못했습니다. 다시 시도해주세요.");
    }
  };

  const isEmpty = chat.messages.length === 0;
  const response = chat.latestResponse;
  // S05-01/02/03: 마지막 응답이 needs_input이면 종류에 맞는 폼을, answered면
  // 정책 화면을 그린다. 되묻기 종류를 구분하지 않으면 계산 되묻기(N10a)가
  // 슬롯 폼으로 떨어져 필드 0개짜리 폼에 갇힌다(followupKindOf 주석 참고).
  const followupKind = followupKindOf(response);
  // 답을 보내는 중에는 폼을 내린다 - 방금 고른 값은 바로 위 사용자 말풍선에
  // 이미 보이고, 그 아래 같은 질문 폼이 그대로 떠 있으면 "보낸 건가?" 싶다.
  const showFollowupUi = followupKind !== "none" && !chat.isSending;
  const showPolicyUi = response?.status === "answered" && response.policies.length > 0;

  // 정책 결과는 턴마다 보존하고, 현재 입력 폼만 말풍선과 중복되지 않게 제외한다.
  const bubbleMessages = showFollowupUi
    ? chat.messages.filter((turn) => turn.response !== response)
    : chat.messages;

  // API-10/11 에러(예: GRAPH_EXECUTION_ERROR, SESSION_NOT_FOUND)를 서버 메시지 그대로 노출한다.
  const sendErrorMessage = chat.sendError ? toErrorMessage(chat.sendError) : null;

  return (
    <AppShell
      sidebar={
        <Sidebar
          onNewChat={() => setConfirmingNewChat(true)}
          isResettingChat={chat.isResetting}
          supportConditions={supportConditions}
          onSupportConditionsChange={setSupportConditions}
          interestFields={interestFields}
          onInterestFieldsChange={setInterestFields}
          topK={topK}
          onTopKChange={setTopKOverride}
        />
      }
    >
      <div className="app-content">
        {isEmpty && (
          <div className="card" style={{ marginBottom: 20 }}>
            <p style={{ fontSize: 15, fontWeight: 700, margin: "0 0 8px" }}>{INTRO_GREETING_TITLE}</p>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: "0 0 12px" }}>{INTRO_GREETING_BODY}</p>

            {/* 하드 게이트 슬롯(N2)은 다 차기 전에는 정책 검색으로 넘어가지
                않는다 — 무엇을 알려줘야 하는지 처음부터 밝혀 되묻기 왕복을 줄인다. */}
            <div className="intro-required">
              <p className="intro-required-title">{INTRO_REQUIRED_TITLE}</p>
              <ul className="intro-required-list">
                {INTRO_REQUIRED_SLOTS.map((slot) => (
                  <li key={slot}>{SLOT_LABELS_KO[slot]}</li>
                ))}
              </ul>
              <p className="intro-required-hint text-muted">{INTRO_REQUIRED_HINT}</p>
            </div>

            <p className="text-faint" style={{ fontSize: 12, margin: "12px 0 0" }}>{INTRO_GREETING_HINT}</p>
          </div>
        )}

        {isEmpty && (
          <div style={{ marginBottom: 20 }}>
            <ExamplePrompts onSelect={submitMessage} disabled={chat.isSending} />
          </div>
        )}

        {bubbleMessages.map((turn, i) => (
          turn.response?.status === "answered" && turn.response.policies.length > 0 ? (
            <ChatPolicyResult key={i} response={turn.response}
              onAskQuestion={turn.response === response && !chat.isSending ? setAskingPolicy : undefined} />
          ) : <ChatBubble key={i} role={turn.role} text={turn.text} />
        ))}

        {/* S03-06: 응답 대기 중 진행 막대 — 지금 어느 단계인지까지 보여준다
            (예전에는 점 세 개만 떠 있어 멈춘 건지 도는 건지 알 수 없었다). */}
        <ChatProgressBar token={chat.progressToken} active={chat.isSending} startedAt={chat.progressStartedAt} />

        {response && !showFollowupUi && !showPolicyUi && (
          <p className="text-faint" style={{ fontSize: 12, marginTop: -6, marginBottom: 12 }}>{GUIDANCE_OFFICIAL}</p>
        )}

        <div className="view-fade">
        {showFollowupUi && response && (
          followupKind === "calc" ? (
            <CalcFollowupForm
              response={response}
              onSubmit={submitCalcAnswers}
              onSkip={submitMessage}
              isSubmitting={chat.isSending}
            />
          ) : followupKind === "conflict" ? (
            <SlotConflictForm response={response} onSubmit={submitMessage} isSubmitting={chat.isSending} />
          ) : (
            <SlotFollowupForm
              response={response}
              onSubmit={submitMessage}
              isSubmitting={chat.isSending}
            />
          )
        )}

        </div>

        {sendErrorMessage && <ErrorBanner style={{ marginTop: 12, marginBottom: 0 }}>{sendErrorMessage}</ErrorBanner>}

        {(isEmpty || !showFollowupUi) && (
          <div style={{ marginTop: 20, display: "flex", alignItems: "center", gap: 8 }}>
            <div className="input-shell" style={{ flex: 1 }}>
              <input
                type="text"
                placeholder={CHAT_INPUT_PLACEHOLDER}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submitMessage(input)}
                disabled={chat.isSending}
              />
            </div>
            <button type="button" className="btn-primary" style={{ width: "auto", padding: "0 20px" }} onClick={() => submitMessage(input)} disabled={chat.isSending || !input.trim()}>
              전송
            </button>
          </div>
        )}
      </div>

      {askingPolicy && (
        <PolicyQuestionDialog sessionId={chat.sessionId} policy={askingPolicy} onClose={() => setAskingPolicy(null)} />
      )}

      <ConfirmModal
        open={confirmingNewChat}
        title="새 상담을 시작할까요?"
        description="현재 진행 중인 상담 내용은 저장되지 않고 모두 사라져요."
        confirmLabel="새 상담 시작"
        cancelLabel="취소"
        isSubmitting={chat.isResetting}
        onConfirm={confirmNewChat}
        onCancel={() => {
          setConfirmingNewChat(false);
          setNewChatError(null);
        }}
      >
        {newChatError && <p className="inline-err">{newChatError}</p>}
      </ConfirmModal>

    </AppShell>
  );
}
