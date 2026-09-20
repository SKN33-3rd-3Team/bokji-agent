interface ChatBubbleProps {
  role: "user" | "assistant";
  text: string;
}

/** 사용자 발화 / 봇 응답(질문 또는 최종 안내) 한 줄을 렌더링한다. */
export function ChatBubble({ role, text }: ChatBubbleProps) {
  const isUser = role === "user";
  return (
    <div
      className="chat-bubble-in"
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        marginBottom: 12,
      }}
    >
      <div
        style={{
          maxWidth: "80%",
          padding: "10px 14px",
          borderRadius: 14,
          fontSize: 13.5,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
          background: isUser ? "var(--primary)" : "var(--panel)",
          color: isUser ? "#fff" : "var(--text)",
          border: isUser ? "none" : "1px solid var(--border)",
        }}
      >
        {text}
      </div>
    </div>
  );
}
