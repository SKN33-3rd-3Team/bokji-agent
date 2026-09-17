/** S03-06/S08-02: 응답 대기 중 로딩 인디케이터 — 답변 봇 말풍선 자리에 점 3개가 통통 뛰는 형태로 보여준다. */
export function TypingIndicator() {
  return (
    <div style={{ display: "flex", justifyContent: "flex-start", marginBottom: 12 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "12px 16px",
          borderRadius: 14,
          background: "var(--panel)",
          border: "1px solid var(--border)",
        }}
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--text-faint)",
              animation: "bkw-typing-bounce 1.1s infinite ease-in-out",
              animationDelay: `${i * 0.15}s`,
            }}
          />
        ))}
        <style>{`
          @keyframes bkw-typing-bounce {
            0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
            30% { transform: translateY(-4px); opacity: 1; }
          }
        `}</style>
      </div>
    </div>
  );
}
