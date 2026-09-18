import { EXAMPLE_PROMPTS } from "@/constants/labels";

interface ExamplePromptsProps {
  onSelect: (prompt: string) => void;
  disabled?: boolean;
}

/** S03-05: 클릭 시 문구가 그대로 사용자 메시지로 전송되며 파이프라인이 즉시 실행된다. */
export function ExamplePrompts({ onSelect, disabled }: ExamplePromptsProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {EXAMPLE_PROMPTS.map((prompt) => (
        <button
          key={prompt}
          type="button"
          className="btn-outline"
          style={{ justifyContent: "flex-start", height: "auto", padding: "10px 14px", textAlign: "left" }}
          onClick={() => onSelect(prompt)}
          disabled={disabled}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M13 2 3 14h7l-1 8 11-14h-7z" />
          </svg>
          <span style={{ fontWeight: 500, fontSize: 13 }}>{prompt}</span>
        </button>
      ))}
    </div>
  );
}
