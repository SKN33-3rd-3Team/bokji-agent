interface ToastProps {
  message: string;
}

/**
 * 화면 상단 중앙에 잠깐 떴다 사라지는 성공 알림 — 폼 안 작은 텍스트보다
 * 눈에 띄게. 뷰포트 기준 고정이라 페이지를 스크롤해서 내려가 있어도
 * 항상 보인다(마이페이지처럼 긴 폼 아래쪽에서 저장할 때를 고려).
 */
export function Toast({ message }: ToastProps) {
  return (
    <div
      style={{
        position: "fixed",
        left: "50%",
        top: 24,
        transform: "translateX(-50%)",
        zIndex: 100,
        display: "flex",
        alignItems: "center",
        gap: 8,
        background: "var(--text)",
        color: "var(--bg)",
        fontSize: 13.5,
        fontWeight: 700,
        padding: "13px 20px",
        borderRadius: 999,
        boxShadow: "0 12px 28px rgba(15,18,40,0.28)",
        whiteSpace: "nowrap",
      }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="var(--green)" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" style={{ width: 17, height: 17, flexShrink: 0 }}>
        <circle cx="12" cy="12" r="10" />
        <path d="M8 12l2.5 2.5L16 9" />
      </svg>
      {message}
    </div>
  );
}
