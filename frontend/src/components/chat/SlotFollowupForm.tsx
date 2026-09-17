import { useState } from "react";
import { ChatBubble } from "./ChatBubble";
import { LlmDebugPanel } from "./LlmDebugPanel";
import { BirthDateSelect } from "@/components/common/BirthDateSelect";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import {
  DISABILITY_LABELS_KO,
  EMPLOYMENT_STATUS_LABELS_KO,
  FALLBACK_SIDO_OPTIONS,
  GENDER_LABELS_KO,
  INCOME_BRACKET_LABELS_KO,
  SLOT_LABELS_KO,
} from "@/constants/labels";
import type { ChatResponse, HardGateSlot } from "@/types/chat";

const SKIP_VALUE = "모름";

interface SlotFollowupFormProps {
  response: ChatResponse;
  onSubmit: (message: string) => void;
  isSubmitting: boolean;
}

/**
 * S05-01: 하드 게이트 슬롯이 부족할 때 되묻는 폼.
 * N3(request_missing_slots.py)는 부족한 슬롯을 한 턴에 전부 모아서
 * 번호 목록으로 묻고, 슬롯마다 "물어본 횟수"(slot_ask_counts)를 올린다
 * (지역만 먼저 묻고 나머지는 다음 턴에 묻던 예전 방식은 되묻기 왕복이
 * 늘어나 MAX_SLOT_ASKS 상한에 먼저 닿는 문제로 폐기됨, 2026-08-31).
 * 그래서 프론트도 missing_slots 배열 전체에 대해 위젯을 한 번에 그리고
 * 답을 한 메시지로 합쳐 보낸다 — 첫 슬롯만 그리면 뒤 슬롯은 한 번도
 * 위젯을 보여주지 못한 채로 ask_count만 올라가 조용히 "모름" 처리된다.
 */
export function SlotFollowupForm({ response, onSubmit, isSubmitting }: SlotFollowupFormProps) {
  const { data: options } = useSearchOptions();
  const slots = response.missing_slots as HardGateSlot[];

  const [values, setValues] = useState<Record<string, string>>({});
  const [skipped, setSkipped] = useState<Record<string, boolean>>({});

  const setValue = (slot: string, value: string) => {
    setValues((prev) => ({ ...prev, [slot]: value }));
    setSkipped((prev) => (prev[slot] ? { ...prev, [slot]: false } : prev));
  };
  const toggleSkip = (slot: string) => setSkipped((prev) => ({ ...prev, [slot]: !prev[slot] }));

  const incomeBracketLabelMap =
    options?.income_bracket_options?.reduce<Record<string, string>>((acc, o) => ({ ...acc, [o.code]: o.label }), {}) ??
    INCOME_BRACKET_LABELS_KO;

  const isFilled = (slot: string) => skipped[slot] || Boolean(values[slot]);
  const allFilled = slots.length > 0 && slots.every(isFilled);

  const submit = () => {
    const parts = slots.map((slot) => {
      const raw = values[slot];
      let valueLabel = SKIP_VALUE;
      if (!skipped[slot]) {
        if (slot === "gender") valueLabel = GENDER_LABELS_KO[raw] ?? raw;
        else if (slot === "disability_status") valueLabel = DISABILITY_LABELS_KO[raw] ?? raw;
        else if (slot === "income_bracket") valueLabel = incomeBracketLabelMap[raw] ?? raw;
        else valueLabel = raw;
      }
      return `${SLOT_LABELS_KO[slot]}: ${valueLabel}`;
    });
    onSubmit(parts.join(", "));
  };

  return (
    <div>
      <ChatBubble role="assistant" text={response.question ?? ""} />
      <LlmDebugPanel response={response} />
      <div className="card" style={{ padding: "18px 20px", marginTop: 10 }}>
        {slots.map((slot) => {
          const value = values[slot] ?? "";
          const isSkipped = Boolean(skipped[slot]);
          return (
            <div className="field" key={slot}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <label>{SLOT_LABELS_KO[slot]}</label>
                <button
                  type="button"
                  onClick={() => toggleSkip(slot)}
                  style={{
                    background: isSkipped ? "var(--primary-soft)" : "none",
                    color: isSkipped ? "var(--primary)" : "var(--text-faint)",
                    border: "none",
                    borderRadius: 999,
                    fontSize: 11.5,
                    fontWeight: 700,
                    padding: "3px 10px",
                    cursor: "pointer",
                  }}
                >
                  {isSkipped ? "모름 ✓" : "모름"}
                </button>
              </div>

              {!isSkipped && slot === "region" && (
                <div className="select-shell">
                  <select value={value} onChange={(e) => setValue(slot, e.target.value)}>
                    <option value="">선택하세요</option>
                    {(options?.sido_options ?? FALLBACK_SIDO_OPTIONS).map((sido) => (
                      <option key={sido} value={sido}>
                        {sido}
                      </option>
                    ))}
                  </select>
                  <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
                </div>
              )}

              {!isSkipped && slot === "gender" && (
                <div className="radio-group">
                  {Object.entries(GENDER_LABELS_KO).map(([code, koLabel]) => (
                    <label key={code} className={`radio-opt${value === code ? " sel" : ""}`}>
                      <input type="radio" name={`slot-${slot}`} checked={value === code} onChange={() => setValue(slot, code)} />
                      <span className="dot" />
                      {koLabel}
                    </label>
                  ))}
                </div>
              )}

              {!isSkipped && slot === "birth_date" && (
                <BirthDateSelect value={value} onChange={(v) => setValue(slot, v)} />
              )}

              {!isSkipped && slot === "income_bracket" && (
                <div className="select-shell">
                  <select value={value} onChange={(e) => setValue(slot, e.target.value)}>
                    <option value="">선택하세요</option>
                    {Object.entries(incomeBracketLabelMap).map(([code, koLabel]) => (
                      <option key={code} value={code}>
                        {koLabel}
                      </option>
                    ))}
                  </select>
                  <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
                </div>
              )}

              {!isSkipped && slot === "disability_status" && (
                <div className="radio-group">
                  {Object.entries(DISABILITY_LABELS_KO).map(([code, koLabel]) => (
                    <label key={code} className={`radio-opt${value === code ? " sel" : ""}`}>
                      <input type="radio" name={`slot-${slot}`} checked={value === code} onChange={() => setValue(slot, code)} />
                      <span className="dot" />
                      {koLabel}
                    </label>
                  ))}
                </div>
              )}

              {!isSkipped && slot === "employment_status" && (
                <>
                  <p className="helper">
                    예: {Object.values(EMPLOYMENT_STATUS_LABELS_KO).join(" / ")} 중 자유롭게 입력해 주세요.
                  </p>
                  <div className="input-shell">
                    <input
                      type="text"
                      placeholder="예: 회사 다니고 있어요"
                      value={value}
                      onChange={(e) => setValue(slot, e.target.value)}
                    />
                  </div>
                </>
              )}
            </div>
          );
        })}
        <button className="btn-primary" onClick={submit} disabled={isSubmitting || !allFilled}>
          이 정보로 계속
        </button>
      </div>
    </div>
  );
}
