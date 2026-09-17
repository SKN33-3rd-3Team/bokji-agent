import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/features/auth/useAuth";
import { SearchScopeSidebar } from "@/components/chat/SearchScopeSidebar";

interface SidebarProps {
  onNewChat: () => void;
  isResettingChat: boolean;
  supportConditions: string[];
  onSupportConditionsChange: (next: string[]) => void;
  interestFields: string[];
  onInterestFieldsChange: (next: string[]) => void;
  topK: number;
  onTopKChange: (next: number) => void;
}

/** S-03 좌측 사이드바(마이페이지/로그아웃/새 상담) + S-04(검색 범위 조정). */
export function Sidebar({
  onNewChat,
  isResettingChat,
  supportConditions,
  onSupportConditionsChange,
  interestFields,
  onInterestFieldsChange,
  topK,
  onTopKChange,
}: SidebarProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [scopeExpanded, setScopeExpanded] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <aside className="sidebar">
      {user && (
        <div className="sb-account">
          <span className="sb-avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 21a8 8 0 1 0-16 0" />
              <circle cx="12" cy="7.5" r="4.5" />
            </svg>
          </span>
          <svg className="status-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M8 12l2.5 2.5L16 9" />
          </svg>
          {user.display_name} 님으로 로그인됨
        </div>
      )}

      <Link to="/mypage" className="sb-btn">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c0-4 4-6 8-6s8 2 8 6" />
        </svg>
        마이페이지
      </Link>
      <button type="button" className="sb-btn" onClick={handleLogout}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
          <path d="M16 17l5-5-5-5" />
          <path d="M21 12H9" />
        </svg>
        로그아웃
      </button>

      <div className="sb-divider" />

      <button type="button" className="sb-btn" onClick={onNewChat} disabled={isResettingChat}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z" />
          <path d="M12 8v5M9.5 10.5h5" />
        </svg>
        새 상담 시작
      </button>

      <div className="sb-divider" />

      <button
        type="button"
        className={`sb-section-label${scopeExpanded ? "" : " collapsed"}`}
        onClick={() => setScopeExpanded((v) => !v)}
        aria-expanded={scopeExpanded}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 9l6 6 6-6" />
        </svg>
        검색 범위 조정 <span className="sb-optional">(선택)</span>
      </button>

      {scopeExpanded && (
        <SearchScopeSidebar
          supportConditions={supportConditions}
          onSupportConditionsChange={onSupportConditionsChange}
          interestFields={interestFields}
          onInterestFieldsChange={onInterestFieldsChange}
          topK={topK}
          onTopKChange={onTopKChange}
        />
      )}
    </aside>
  );
}
