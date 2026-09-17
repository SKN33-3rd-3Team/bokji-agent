import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { Sidebar } from "@/components/layout/Sidebar";
import { ConfirmModal } from "@/components/common/ConfirmModal";
import { Toast } from "@/components/common/Toast";
import { ChatBubble } from "@/components/chat/ChatBubble";
import { ExamplePrompts } from "@/components/chat/ExamplePrompts";
import { SlotFollowupForm } from "@/components/chat/SlotFollowupForm";
import { SlotConflictForm } from "@/components/chat/SlotConflictForm";
import { PolicySummaryStats } from "@/components/chat/PolicySummaryStats";
import { PolicyCard } from "@/components/chat/PolicyCard";
import { PolicyDetailView } from "@/components/chat/PolicyDetailView";
import { PolicyCompareTable } from "@/components/chat/PolicyCompareTable";
import { LlmDebugPanel } from "@/components/chat/LlmDebugPanel";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { PolicyQuestionDialog } from "@/components/dialogs/PolicyQuestionDialog";
import { useChatSession } from "@/features/chat/useChatSession";
import { usePolicySelection } from "@/features/chat/usePolicySelection";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import { useAuth } from "@/features/auth/useAuth";
import { getChatDefaults } from "@/api/userApi";
import { ApiError } from "@/api/client";
import { CHAT_INPUT_PLACEHOLDER, GUIDANCE_OFFICIAL, INTRO_GREETING_BODY, INTRO_GREETING_HINT, INTRO_GREETING_TITLE } from "@/constants/labels";
import type { PolicyView } from "@/types/chat";
import { FALLBACK_DEFAULT_TOP_K } from "@/constants/labels";

export function ChatPage() {
  const { user } = useAuth();
  const chat = useChatSession();
  const compare = usePolicySelection();
  const location = useLocation();
  const navigate = useNavigate();

  // API-01 비고: 회원가입 직후 S-03으로 넘어올 때 딱 한 번 성공 토스트를 보여준다.
  const [signupToast, setSignupToast] = useState(
    Boolean((location.state as { justSignedUp?: boolean } | null)?.justSignedUp),
  );
  useEffect(() => {
    if (!signupToast) return;
    navigate(location.pathname, { replace: true, state: {} }); // 새로고침 시 재노출 방지
    const timer = setTimeout(() => setSignupToast(false), 2500);
    return () => clearTimeout(timer);
    // 마운트 시 1회만 — location.state는 위에서 이미 초기값으로 읽었다.
  }, []);

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

  const handleNewChat = async () => {
    await chat.resetConversation();
    compare.clear();
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

  const handleCompareClick = () => {
    if (compare.count >= 2) chat.openCompare();
  };

  const isEmpty = chat.messages.length === 0;
  const response = chat.latestResponse;
  // S05-01/02: 마지막 응답이 needs_input이면 폼을, answered면 정책 화면을 그린다.
  const showFollowupUi = response?.status === "needs_input";
  const showPolicyUi = response?.status === "answered" && response.policies.length > 0;

  // 마지막 assistant 메시지는 폼/정책 화면이 대신 보여주므로 버블 목록에서는 뺀다.
  const bubbleMessages = showFollowupUi || showPolicyUi ? chat.messages.slice(0, -1) : chat.messages;

  const selectedPolicies = response?.policies.filter((p) => compare.selectedIds.includes(p.policy_id)) ?? [];
  const activePolicy = response?.policies.find((p) => p.policy_id === chat.selectedPolicyId) ?? null;

  // API-10/11 에러(예: GRAPH_EXECUTION_ERROR, SESSION_NOT_FOUND)를 서버 메시지 그대로 노출한다.
  const sendErrorMessage = chat.sendError
    ? chat.sendError instanceof ApiError
      ? chat.sendError.message
      : "일시적인 오류가 발생했습니다. 다시 시도해주세요."
    : null;

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
            <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: "0 0 10px" }}>{INTRO_GREETING_BODY}</p>
            <p className="text-faint" style={{ fontSize: 12, margin: 0 }}>{INTRO_GREETING_HINT}</p>
          </div>
        )}

        {isEmpty && (
          <div style={{ marginBottom: 20 }}>
            <ExamplePrompts onSelect={submitMessage} disabled={chat.isSending} />
          </div>
        )}

        {bubbleMessages.map((turn, i) => (
          <ChatBubble key={i} role={turn.role} text={turn.text} />
        ))}

        {/* S03-06: 응답 대기 중 로딩 인디케이터 */}
        {chat.isSending && <TypingIndicator />}

        {response && !showFollowupUi && !showPolicyUi && (
          <p className="text-faint" style={{ fontSize: 12, marginTop: -6, marginBottom: 12 }}>{GUIDANCE_OFFICIAL}</p>
        )}

        <div className="view-fade" key={`${chat.messages.length}-${chat.policyView}`}>
        {showFollowupUi && response && (
          response.slot_conflicts ? (
            <SlotConflictForm response={response} onSubmit={submitMessage} isSubmitting={chat.isSending} />
          ) : (
            <SlotFollowupForm
              response={response}
              onSubmit={submitMessage}
              isSubmitting={chat.isSending}
            />
          )
        )}

        {showPolicyUi && response && chat.policyView === "list" && (
          <div>
            <PolicySummaryStats policies={response.policies} />
            {response.policies.map((policy) => (
              <PolicyCard
                key={policy.policy_id}
                policy={policy}
                selected={compare.selectedIds.includes(policy.policy_id)}
                onToggleSelect={compare.toggle}
                onOpenDetail={chat.openDetail}
              />
            ))}
            <div
              style={{
                position: "sticky",
                bottom: 0,
                marginTop: 6,
                background: "var(--text)",
                borderRadius: 14,
                padding: "12px 18px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
              }}
            >
              <span style={{ color: "var(--bg)", fontWeight: 600, fontSize: 13.5 }}>
                {compare.count === 0 && "정책을 선택하면 나란히 비교할 수 있어요"}
                {compare.count === 1 && "1개만 더 선택하면 비교할 수 있어요"}
                {compare.count >= 2 && `${compare.count}개 선택됨`}
              </span>
              <button type="button" className="btn-primary" style={{ width: "auto", padding: "0 18px" }} disabled={compare.count < 2} onClick={handleCompareClick}>
                비교하기
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.3} strokeLinecap="round" strokeLinejoin="round">
                  <path d="M8 3L4 7l4 4M4 7h16M16 21l4-4-4-4M20 17H4" />
                </svg>
              </button>
            </div>
            <LlmDebugPanel response={response} />
          </div>
        )}

        {showPolicyUi && chat.policyView === "detail" && activePolicy && (
          <PolicyDetailView policy={activePolicy} onBack={chat.backToList} onAskQuestion={setAskingPolicy} />
        )}

        {showPolicyUi && chat.policyView === "compare" && (
          <PolicyCompareTable policies={selectedPolicies} onBackToList={chat.backToList} onOpenDetail={chat.openDetail} />
        )}
        </div>

        {sendErrorMessage && (
          <div style={{ background: "var(--red-bg)", border: "1px solid var(--red-border)", color: "var(--red-text)", borderRadius: 10, padding: "12px 14px", marginTop: 12, fontSize: 13 }}>
            {sendErrorMessage}
          </div>
        )}

        {(isEmpty || !showFollowupUi) && chat.policyView === "list" && (
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

      {signupToast && <Toast message="회원가입이 완료되었습니다!" />}
    </AppShell>
  );
}
