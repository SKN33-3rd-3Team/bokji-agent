import { useState } from "react";
import type { ChatResponse } from "@/types/chat";

interface LlmDebugPanelProps {
  response: ChatResponse;
}

/**
 * S05-04(선택 우선순위): LLM 개입 여부 배지 + 응답 원문 보기.
 * 요구사항 정의서 비고: 운영 환경에서 일반 사용자에게 노출할지는 PM 정책
 * 결정이 필요 — 우선 항상 노출해 두고, 노출 조건(예: 관리자/QA 전용)은
 * 추후 조정 지점으로 남긴다.
 */
export function LlmDebugPanel({ response }: LlmDebugPanelProps) {
  const [open, setOpen] = useState(false);
  const { llm_status: llmStatus } = response;

  if (!llmStatus) return null;

  return (
    <div style={{ marginTop: 8, marginBottom: 4 }}>
      {llmStatus.enabled ? (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            background: "var(--violet-soft)",
            color: "var(--violet)",
            fontSize: 11,
            fontWeight: 700,
            padding: "3px 10px",
            borderRadius: 999,
          }}
        >
          🧠 AI 분석 적용 · {llmStatus.model ?? "모델 미확인"} · {llmStatus.calls}회 호출
        </span>
      ) : (
        <span className="text-faint" style={{ fontSize: 11.5 }}>
          AI 모델을 사용하지 않고 규칙 기반·템플릿 경로로 처리했습니다.
        </span>
      )}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          background: "none",
          border: "none",
          color: "var(--primary)",
          fontSize: 11.5,
          fontWeight: 600,
          padding: "5px 0",
          cursor: "pointer",
        }}
      >
        <span style={{ display: "inline-block", transform: open ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>▸</span>
        응답 원문 보기
      </button>
      {open && (
        <pre
          style={{
            fontSize: 11,
            lineHeight: 1.6,
            background: "var(--gray-bg)",
            color: "var(--gray-text)",
            padding: "10px 12px",
            borderRadius: 8,
            overflowX: "auto",
            whiteSpace: "pre-wrap",
          }}
        >
          {response.output_markdown || JSON.stringify(response.output_json, null, 2)}
        </pre>
      )}
    </div>
  );
}
