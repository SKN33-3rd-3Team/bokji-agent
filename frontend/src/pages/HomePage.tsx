import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Sidebar } from "@/components/layout/Sidebar";
import { ConfirmModal } from "@/components/common/ConfirmModal";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { Toast } from "@/components/common/Toast";
import { SlotFollowupForm } from "@/components/chat/SlotFollowupForm";
import { SlotConflictForm } from "@/components/chat/SlotConflictForm";
import { CalcFollowupForm } from "@/components/chat/CalcFollowupForm";
import { PolicySummaryStats } from "@/components/chat/PolicySummaryStats";
import { PolicyCard } from "@/components/chat/PolicyCard";
import { PolicyDetailView } from "@/components/chat/PolicyDetailView";
import { PolicyCompareTable } from "@/components/chat/PolicyCompareTable";
import { LlmDebugPanel } from "@/components/chat/LlmDebugPanel";
import { ChatProgressBar } from "@/components/chat/ChatProgressBar";
import { PolicyQuestionDialog } from "@/components/dialogs/PolicyQuestionDialog";
import { useChatSession } from "@/features/chat/useChatSession";
import {
  useAutoRecommendations,
  useResetAutoRecommendations,
} from "@/features/chat/useAutoRecommendations";
import { usePolicySelection } from "@/features/chat/usePolicySelection";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import { ApiError, toErrorMessage } from "@/api/client";
import { FALLBACK_DEFAULT_TOP_K, GUIDANCE_OFFICIAL, HOME_CAPTION } from "@/constants/labels";
import { followupKindOf } from "@/utils/chatQuestion";
import type { CalculationAnswers, ChatResponse, PolicyView } from "@/types/chat";

/**
 * 홈 화면 — API-14(자동추천_API_정의서_v1.0.xlsx) POST
 * /api/v1/chat/recommendations를 요청 바디 없이 호출해, 서버가 DB에 저장된
 * 프로필만으로 판단한 정책 추천을 그대로 보여준다. 채팅(ChatPage)과는
 * 완전히 별도의 useChatSession 인스턴스를 쓰므로, 진행 중이던 상담 세션을
 * 건드리지 않는다.
 *
 * 로그인/회원가입 직후는 물론, 로고 클릭이나 마이페이지 "홈으로 돌아가기"로
 * 재진입할 때도 매번 새로 호출해 최신 프로필 기준 추천을 보여준다. API-14
 * 계약의 "가입/페이지 재진입/프로필 수정에 자동 재호출을 추가하지 않는다"는
 * React가 스스로(예: effect 재실행, StrictMode 이중 마운트) 조용히 다시
 * 부르는 것을 막으라는 뜻이지, 사용자가 홈으로 이동을 직접 요청하는 것까지
 * 막는 게 아니다 - hasStartedRef는 그 "조용한 이중 호출"만 막는다.
 */
export function HomePage() {
  const chat = useChatSession();
  const compare = usePolicySelection();
  const [askingPolicy, setAskingPolicy] = useState<PolicyView | null>(null);
  const [confirmingNewChat, setConfirmingNewChat] = useState(false);
  const [newChatError, setNewChatError] = useState<string | null>(null);
  // 같은 응답을 두 번 심지 않기 위한 표시(캐시 재진입 포함).
  const hydratedRef = useRef<ChatResponse | null>(null);
  const location = useLocation();
  const navigate = useNavigate();

  // API-01 비고: 회원가입 직후 홈 화면으로 넘어올 때 딱 한 번 성공 토스트를
  // 보여준다. location.state는 아래 effect가 읽자마자 지운다(새로고침 시
  // 재노출 방지) - 지우기 전에 초기 렌더 시점 값을 한 번만 캡처해둔다.
  const [initialNavState] = useState(
    () => (location.state as { justSignedUp?: boolean } | null) ?? {},
  );
  const [signupToast, setSignupToast] = useState(Boolean(initialNavState.justSignedUp));

  useEffect(() => {
    if (!initialNavState.justSignedUp) return;
    navigate(location.pathname, { replace: true, state: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!signupToast) return;
    const timer = setTimeout(() => setSignupToast(false), 2500);
    return () => clearTimeout(timer);
  }, [signupToast]);

  // 채팅과 동일한 사이드바(마이페이지/로그아웃/새 상담 시작/검색 범위 조정)를 쓴다.
  // top_k/관심 조건은 API-14 요청에는 실리지 않는다(서버 기본값 재사용, 클라이언트
  // 입력 없음) - ChatPage와의 사이드바 UI 일관성을 위해 컨트롤만 유지한다.
  const [supportConditions, setSupportConditions] = useState<string[]>([]);
  const [interestFields, setInterestFields] = useState<string[]>([]);
  const [topKOverride, setTopKOverride] = useState<number | null>(null);
  const { data: searchOptions } = useSearchOptions();
  const topK = topKOverride ?? searchOptions?.default_top_k ?? FALLBACK_DEFAULT_TOP_K;

  // API-14 결과는 캐시한다 - 재진입 때 재검색하지 않고 캐시로 그린다
  // (useAutoRecommendations). 진행 막대도 그리는데, 프로필만으로 도는 자동
  // 추천이라 사용자가 아무것도 입력하지 않았을 뿐 그래프는 상담과 똑같이
  // N1~N14를 돌기 때문이다.
  const autoReco = useAutoRecommendations(true);
  const resetAutoReco = useResetAutoRecommendations();

  // 캐시에서 왔든 방금 받았든, 받은 응답을 세션의 첫 턴으로 한 번만 심는다.
  // 같은 응답 객체를 두 번 심으면 말풍선이 겹쳐 쌓인다.
  useEffect(() => {
    if (!autoReco.data || hydratedRef.current === autoReco.data) return;
    hydratedRef.current = autoReco.data;
    chat.hydrate(autoReco.data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoReco.data]);

  // 홈 화면은 로그인 직후 자동 추천 결과만 보여주는 화면이라, 사이드바의
  // "새 상담 시작"은 여기서 추천을 반복하는 게 아니라 자유롭게 대화할 수 있는
  // 채팅 화면(S-03)으로 보내는 게 맞다.
  const handleNewChat = async () => {
    await chat.resetConversation();
    compare.clear();
    setAskingPolicy(null);
    // resetConversation이 API-13으로 이 session_id를 서버에서 지운다.
    // 캐시된 추천 응답은 그 session_id를 물고 있으므로 같이 버려야 한다.
    resetAutoReco();
    navigate("/chat");
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

  const response = chat.latestResponse;
  const followupKind = followupKindOf(response);
  // ChatPage와 같은 이유로 전송 중에는 폼을 내린다(진행 막대가 대신한다).
  const isBusy = chat.isSending || autoReco.isFetching;
  const showFollowupUi = followupKind !== "none" && !isBusy;
  const showPolicyUi = response?.status === "answered" && response.policies.length > 0;
  // 정책이 0건이거나(정상 0건) 그 외 판정 불가 상태 — final_answer 카드와
  // 공식 확인 안내 문구를 함께 보여준다. 전송 중에는 직전 턴의 final_answer가
  // 비어 있을 수 있어(되묻기 응답) 빈 카드가 뜨므로 같이 막는다.
  const showEmptyState = !showFollowupUi && !showPolicyUi && !isBusy && Boolean(response?.final_answer);

  const handleCompareClick = () => {
    if (compare.count >= 2) chat.openCompare();
  };

  // 상세보기/비교하기는 URL 이동 없이 chat.policyView만 바뀌는 화면이라,
  // 이 상태에서 로고를 눌러도 AppShell 기본 동작(이미 "/home"이라 무시)으로는
  // 목록으로 못 돌아간다 - policyView가 list가 아닐 때는 먼저 목록으로
  // 되돌린다. 되묻기/슬롯충돌 응답(showFollowupUi)은 policyView와 무관하게
  // response.status로만 결정되므로, 자동 추천 뒤 이어서 보낸 메시지가
  // needs_input을 받아온 경우엔 목록으로 되돌리는 것만으론 빠져나올 수
  // 없다 - 이 경우 새 세션으로 자동 추천을 다시 부른다(handleNewChat과
  // 같은 초기화 + 재호출).
  const handleBrandClick = () => {
    if (showFollowupUi) {
      void (async () => {
        await chat.resetConversation();
        compare.clear();
        setAskingPolicy(null);
        // 세션을 지웠으니 그 session_id를 물고 있는 캐시도 같이 버린다 -
        // 안 버리면 다음 진입에서 이미 삭제된 세션으로 정책 문의가 나간다.
        resetAutoReco();
        hydratedRef.current = null;
        void autoReco.refetch();
      })();
      return;
    }
    if (chat.policyView !== "list") {
      chat.backToList();
    }
  };

  const selectedPolicies = response?.policies.filter((p) => compare.selectedIds.includes(p.policy_id)) ?? [];
  const activePolicy = response?.policies.find((p) => p.policy_id === chat.selectedPolicyId) ?? null;
  // 자동 추천 완료 뒤 같은 세션에 새 메시지를 보내면 API-14 자동 모드를
  // 끝내고 일반 상담 새 턴으로 넘어간다(API-14 시트 "후속 질문 D4/D5").
  const submitFollowup = (message: string) => void chat.send({ message });
  const submitCalcAnswers = (answers: CalculationAnswers) =>
    void chat.sendCalcAnswers(answers, "지원금 계산에 필요한 정보를 입력했어요.");

  const autoRecoErrorMessage = autoReco.error ? toErrorMessage(autoReco.error) : null;
  const sendErrorMessage = chat.sendError ? toErrorMessage(chat.sendError) : null;

  return (
    <AppShell
      onBrandClick={handleBrandClick}
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
        <p className="text-faint" style={{ fontSize: 12.5, margin: "0 0 16px" }}>{HOME_CAPTION}</p>

        {/* 자동 추천(API-14)도, 이어지는 상담(API-10/11)도 같은 진행 막대를 쓴다. */}
        <ChatProgressBar token={chat.progressToken} active={isBusy} />

        {autoRecoErrorMessage && (
          <ErrorBanner>
            {autoRecoErrorMessage}
            <div style={{ marginTop: 8 }}>
              <button
                type="button"
                className="btn-outline"
                style={{ width: "auto", padding: "6px 14px", fontSize: 13 }}
                disabled={autoReco.isFetching}
                onClick={() => void autoReco.refetch()}
              >
                다시 시도
              </button>
            </div>
          </ErrorBanner>
        )}
        {sendErrorMessage && <ErrorBanner>{sendErrorMessage}</ErrorBanner>}

        {response && showEmptyState && (
          <>
            <div className="card" style={{ marginBottom: 20 }}>
              <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: 0 }}>{response.final_answer}</p>
            </div>
            <p className="text-faint" style={{ fontSize: 12, marginBottom: 12 }}>{GUIDANCE_OFFICIAL}</p>
          </>
        )}

        <div className="view-fade" key={`${chat.messages.length}-${chat.policyView}`}>
          {showFollowupUi && response && (
            followupKind === "calc" ? (
              <CalcFollowupForm
                response={response}
                onSubmit={submitCalcAnswers}
                onSkip={submitFollowup}
                isSubmitting={chat.isSending}
              />
            ) : followupKind === "conflict" ? (
              <SlotConflictForm response={response} onSubmit={submitFollowup} isSubmitting={chat.isSending} />
            ) : (
              <SlotFollowupForm response={response} onSubmit={submitFollowup} isSubmitting={chat.isSending} />
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
      </div>

      {askingPolicy && (
        <PolicyQuestionDialog sessionId={chat.sessionId} policy={askingPolicy} onClose={() => setAskingPolicy(null)} />
      )}

      <ConfirmModal
        open={confirmingNewChat}
        title="새 상담을 시작할까요?"
        description="현재 진행 중인 검색 내용은 저장되지 않고 모두 사라져요."
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
