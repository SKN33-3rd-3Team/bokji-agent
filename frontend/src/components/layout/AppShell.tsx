import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

interface AppShellProps {
  /** 있으면 좌측 사이드바 포함 레이아웃(S-03~S-07/S-10), 없으면 헤더+본문만(S-09) */
  sidebar?: ReactNode;
  children: ReactNode;
  /**
   * 로고 클릭 시 기본 동작(다른 경로면 "/home"으로 이동, 같은 경로면 무시)
   * 대신 쓸 핸들러. HomePage처럼 상세보기/비교하기 화면이 URL 이동 없이
   * 같은 페이지 안에서 전환되는 경우, "이미 /home이라 아무 것도 안 함"
   * 분기 때문에 로고를 눌러도 목록으로 못 돌아가는 문제를 페이지 쪽에서
   * 직접 처리할 수 있게 한다.
   */
  onBrandClick?: () => void;
}

const BRAND_LOGO = (
  <svg width="26" height="26" viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <path
      d="M6 8.6C6 6.6 7.6 5 9.7 5h12.6C24.4 5 26 6.6 26 8.6V17c0 2-1.6 3.6-3.7 3.6H13l-4.6 4.1c-.7.6-1.7.1-1.7-.8v-3.3H9.7C7.6 20.6 6 19 6 17z"
      fill="#EEF2FF"
      stroke="#3D63D8"
      strokeWidth="2"
      strokeLinejoin="round"
    />
    <path
      d="M16 17.7s-4.1-2.7-4.1-5.7c0-1.6 1.2-2.6 2.5-2.6.9 0 1.5.5 1.6 1.3.1-.8.7-1.3 1.6-1.3 1.3 0 2.5 1 2.5 2.6 0 3-4.1 5.7-4.1 5.7Z"
      fill="#3D63D8"
    />
  </svg>
);

/** 로그인 이후 화면(S-03~S-10) 공통 헤더/레이아웃. */
export function AppShell({ sidebar, children, onBrandClick }: AppShellProps) {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  // 로고를 누르면 홈(마이페이지 기반 정책 추천)으로 이동한다. API-14 계약의
  // "가입/페이지 재진입/프로필 수정에 자동 재호출을 추가하지 않는다"는
  // React가 스스로 조용히 다시 부르는 것(예: effect 재실행)을 막으라는
  // 뜻이지, 사용자가 로고를 직접 눌러 홈으로 이동하는 것까지 막는 게
  // 아니다 - 이건 사용자가 명시적으로 요청한 새로고침이다. 이미 홈
  // 화면이면 아무 것도 하지 않는다 - 예전엔 이 경우 window.location.reload()를
  // 했는데, 그러면 입력 중이던 내용이 확인 없이 날아가버리는 문제가 있었다.
  const handleBrandClick = () => {
    if (onBrandClick) {
      onBrandClick();
      return;
    }
    if (pathname !== "/home") {
      navigate("/home");
    }
  };

  return (
    <div className="app-shell">
      {sidebar}
      <div className="app-main">
        <header className="app-header">
          <button type="button" className="app-brand-btn" onClick={handleBrandClick} aria-label="홈으로 이동">
            {BRAND_LOGO}
            <span className="brand-title">복지 에이전트</span>
          </button>
        </header>
        <main className="app-body">{children}</main>
      </div>
    </div>
  );
}
