import { useState } from "react";
import { usePolicyQuestion } from "@/features/chat/usePolicyQuestion";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { ApiError } from "@/api/client";
import type { PolicyView } from "@/types/chat";

interface PolicyQuestionDialogProps {
  sessionId: string | null;
  policy: PolicyView;
  onClose: () => void;
}

/**
 * S-08 정책 문의 채팅 다이얼로그.
 * S08-01: dismissible=false — 바깥 영역 클릭으로 닫히지 않고 전용 ✕
 * 버튼으로만 닫힌다(기존 Streamlit에서 실수로 닫히던 문제 개선).
 */
export function PolicyQuestionDialog({ sessionId, policy, onClose }: PolicyQuestionDialogProps) {
  const { history, ask, isAsking, error } = usePolicyQuestion(sessionId, policy.policy_id);
  const [input, setInput] = useState("");

  const errorMessage = error ? (error instanceof ApiError ? error.message : "일시적인 오류가 발생했습니다. 다시 시도해주세요.") : null;

  const submit = () => {
    const question = input.trim();
    if (!question || isAsking) return;
    setInput("");
    void ask(question);
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20,22,38,0.42)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
      }}
      // 바깥 클릭으로 닫히지 않도록 스크림 클릭 핸들러를 두지 않는다(S08-01).
    >
      <div style={{ width: 600, maxHeight: 760, background: "var(--panel)", borderRadius: 18, boxShadow: "0 30px 70px rgba(15,18,40,0.32)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "16px 18px 12px 26px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-faint)" }}>정책 문의 채팅방</span>
          <button
            type="button"
            aria-label="닫기"
            onClick={onClose}
            style={{ width: 32, height: 32, borderRadius: 999, background: "var(--gray-bg)", border: "none", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)" }}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div style={{ padding: "20px 26px 22px", display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 6 }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round" style={{ width: 19, height: 19, color: "var(--primary)" }}>
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <h2 style={{ fontSize: 16.5, fontWeight: 800, margin: 0 }}>{policy.title}</h2>
          </div>
          <p className="text-muted" style={{ fontSize: 12.5, margin: "0 0 14px", lineHeight: 1.5 }}>
            이 정책 전용 채팅방이에요. 신청·자격·서류 등을 물어보세요.
          </p>

          <div style={{ height: 360, border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg)", padding: 16, overflowY: "auto" }}>
            {history.length === 0 ? (
              <p className="text-faint" style={{ fontSize: 12.5, textAlign: "center", lineHeight: 1.7, margin: "60px 0 0" }}>
                예: &ldquo;신청은 어디서 하나요?&rdquo;, &ldquo;제가 서울 사는데 대상인가요?&rdquo;
              </p>
            ) : (
              history.map((turn, i) => (
                <div key={i} style={{ display: "flex", justifyContent: turn.role === "user" ? "flex-end" : "flex-start", marginBottom: 10 }}>
                  <div
                    style={{
                      maxWidth: "85%",
                      padding: "9px 13px",
                      borderRadius: 12,
                      fontSize: 13,
                      lineHeight: 1.6,
                      whiteSpace: "pre-wrap",
                      background: turn.role === "user" ? "var(--primary)" : "var(--panel)",
                      color: turn.role === "user" ? "#fff" : "var(--text)",
                      border: turn.role === "user" ? "none" : "1px solid var(--border)",
                      opacity: turn.kind === "guidance" ? 0.85 : 1,
                    }}
                  >
                    {turn.text}
                  </div>
                </div>
              ))
            )}
            {/* S08-02: 응답 대기 중 로딩 표시 */}
            {isAsking && <TypingIndicator />}
          </div>

          {errorMessage && (
            <p style={{ color: "var(--red-text)", fontSize: 12.5, margin: "10px 2px 0" }}>{errorMessage}</p>
          )}

          <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 8, border: "1.5px solid var(--border-strong)", borderRadius: 999, padding: "6px 8px 6px 18px", background: "var(--panel)" }}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder={`${policy.title}에 대해 물어보세요`}
              style={{ flex: 1, border: "none", outline: "none", fontSize: 13.5, color: "var(--text)", background: "transparent" }}
              disabled={isAsking}
            />
            <button
              type="button"
              onClick={submit}
              disabled={isAsking || !input.trim()}
              style={{ width: 36, height: 36, borderRadius: 999, background: "var(--primary)", border: "none", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.3} strokeLinecap="round" strokeLinejoin="round" style={{ width: 16, height: 16, color: "#fff" }}>
                <path d="M22 2 11 13" />
                <path d="M22 2 15 22l-4-9-9-4 20-7Z" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
