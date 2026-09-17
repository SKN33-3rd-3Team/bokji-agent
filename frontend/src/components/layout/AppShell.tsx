import type { ReactNode } from "react";

interface AppShellProps {
  /** 있으면 좌측 사이드바 포함 레이아웃(S-03~S-07/S-10), 없으면 헤더+본문만(S-09) */
  sidebar?: ReactNode;
  children: ReactNode;
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
export function AppShell({ sidebar, children }: AppShellProps) {
  return (
    <div className="app-shell">
      {sidebar}
      <div className="app-main">
        <header className="app-header">
          {BRAND_LOGO}
          <span className="brand-title">복지 에이전트</span>
        </header>
        <main className="app-body">{children}</main>
      </div>
    </div>
  );
}
