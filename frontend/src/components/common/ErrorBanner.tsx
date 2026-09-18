import type { CSSProperties, ReactNode } from "react";

interface ErrorBannerProps {
  children: ReactNode;
  style?: CSSProperties;
}

/** 로그인/회원가입/마이페이지/채팅에서 반복되던 빨간 오류 배너를 하나로 통일. */
export function ErrorBanner({ children, style }: ErrorBannerProps) {
  return (
    <div
      style={{
        background: "var(--red-bg)",
        border: "1px solid var(--red-border)",
        color: "var(--red-text)",
        borderRadius: 10,
        padding: "12px 14px",
        marginBottom: 16,
        fontSize: 13,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
