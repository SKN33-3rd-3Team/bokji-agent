import { useState } from "react";
import { ChatBubble } from "./ChatBubble";
import { LlmDebugPanel } from "./LlmDebugPanel";
import { PillMultiSelect } from "@/components/common/PillMultiSelect";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import {
  DISABILITY_LABELS_KO,
  FALLBACK_HOUSEHOLD_TYPE_OPTIONS,
  FALLBACK_SIDO_OPTIONS,
  GENDER_LABELS_KO,
  INCOME_BRACKET_LABELS_KO,
  SLOT_LABELS_KO,
} from "@/constants/labels";
import type { ChatResponse, HardGateSlot } from "@/types/chat";

// graph/slot_schema.py 기준 slot_conflicts는 region/gender/birth_date/
// income_bracket/disability_status/household_types 6개에서만 발생한다
// (veteran_status는 하드 게이트 슬롯이 아니라 대상 아님 — API-11 비고).
const EXTRA_SLOT_LABELS_KO: Record<string, string> = {
  household_types: "가구 유형",
};

const CODE_WIDGET_SLOTS = ["region", "gender", "disability_status", "birth_date", "income_bracket", "employment_status", "household_types"];

function slotLabel(slot: string): string {
  return SLOT_LABELS_KO[slot as HardGateSlot] ?? EXTRA_SLOT_LABELS_KO[slot] ?? slot;
}

/** conflicts 값은 이미 한글 라벨 문자열이라, select/radio 코드값으로 되돌리기 위한 역조회. */
function codeFromLabel(labelMap: Record<string, string>, label: string): string {
  return Object.entries(labelMap).find(([, koLabel]) => koLabel === label)?.[0] ?? "";
}

interface SlotConflictFormProps {
  response: ChatResponse;
  onSubmit: (message: string) => void;
  isSubmitting: boolean;
}

/**
 * S05-02: 회원 정보와 채팅에서 말한 값이 달라 조용히 덮어쓰지 않고 재확인한다.
 * 재확인 위젯은 슬롯 확인 폼(SlotFollowupForm)과 동일한 컨트롤을 쓰되, 방금
 * 채팅에서 말한 값을 기본 선택으로 미리 채워 둔다 — 회원 정보 값으로 되돌리고
 * 싶으면 직접 바꾸면 된다(디자인시안 S-05 기준).
 */
export function SlotConflictForm({ response, onSubmit, isSubmitting }: SlotConflictFormProps) {
  const { data: options } = useSearchOptions();
  const conflicts = response.slot_conflicts ?? {};
  const slots = Object.keys(conflicts);

  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const slot of slots) {
      const chatValue = conflicts[slot].chat;
      if (slot === "gender") init[slot] = codeFromLabel(GENDER_LABELS_KO, chatValue);
      else if (slot === "disability_status") init[slot] = codeFromLabel(DISABILITY_LABELS_KO, chatValue);
      else if (slot === "income_bracket") init[slot] = codeFromLabel(INCOME_BRACKET_LABELS_KO, chatValue);
      else init[slot] = chatValue; // region/birth_date/employment_status 등은 원문 그대로
    }
    return init;
  });

  const setValue = (slot: string, value: string) => setValues((prev) => ({ ...prev, [slot]: value }));

  const submit = () => {
    const parts = slots.map((slot) => {
      const raw = values[slot] ?? "";
      let label = raw;
      if (slot === "gender") label = GENDER_LABELS_KO[raw] ?? raw;
      else if (slot === "disability_status") label = DISABILITY_LABELS_KO[raw] ?? raw;
      else if (slot === "income_bracket") label = INCOME_BRACKET_LABELS_KO[raw] ?? raw;
      return `${slotLabel(slot)}: ${label}`;
    });
    onSubmit(parts.join(", "));
  };

  const allFilled = slots.every((slot) => values[slot]);
  const incomeBracketLabelMap =
    options?.income_bracket_options?.reduce<Record<string, string>>((acc, o) => ({ ...acc, [o.code]: o.label }), {}) ??
    INCOME_BRACKET_LABELS_KO;
  const householdTypeLabels = (options?.household_type_options ?? FALLBACK_HOUSEHOLD_TYPE_OPTIONS).map((o) => o.label);

  return (
    <div>
      <ChatBubble role="assistant" text={response.question ?? ""} />
      <LlmDebugPanel response={response} />
      <div className="card" style={{ padding: "18px 20px", marginTop: 10 }}>
        {slots.map((slot) => {
          const value = values[slot] ?? "";
          return (
            <div className="field" key={slot}>
              <label>{slotLabel(slot)}</label>

              {slot === "region" && (
                <div className="select-shell">
                  <select value={value} onChange={(e) => setValue(slot, e.target.value)}>
                    {(options?.sido_options ?? FALLBACK_SIDO_OPTIONS).map((sido) => (
                      <option key={sido} value={sido}>
                        {sido}
                      </option>
                    ))}
                  </select>
                  <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
                </div>
              )}

              {slot === "gender" && (
                <div className="radio-group">
                  {Object.entries(GENDER_LABELS_KO).map(([code, koLabel]) => (
                    <label key={code} className={`radio-opt${value === code ? " sel" : ""}`}>
                      <input type="radio" name={`conflict-${slot}`} checked={value === code} onChange={() => setValue(slot, code)} />
                      <span className="dot" />
                      {koLabel}
                    </label>
                  ))}
                </div>
              )}

              {slot === "disability_status" && (
                <div className="radio-group">
                  {Object.entries(DISABILITY_LABELS_KO).map(([code, koLabel]) => (
                    <label key={code} className={`radio-opt${value === code ? " sel" : ""}`}>
                      <input type="radio" name={`conflict-${slot}`} checked={value === code} onChange={() => setValue(slot, code)} />
                      <span className="dot" />
                      {koLabel}
                    </label>
                  ))}
                </div>
              )}

              {slot === "birth_date" && (
                <div className="input-shell">
                  <input type="date" value={value} onChange={(e) => setValue(slot, e.target.value)} />
                </div>
              )}

              {slot === "income_bracket" && (
                <div className="select-shell">
                  <select value={value} onChange={(e) => setValue(slot, e.target.value)}>
                    {Object.entries(incomeBracketLabelMap).map(([code, koLabel]) => (
                      <option key={code} value={code}>
                        {koLabel}
                      </option>
                    ))}
                  </select>
                  <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
                </div>
              )}

              {slot === "employment_status" && (
                <div className="input-shell">
                  <input type="text" value={value} onChange={(e) => setValue(slot, e.target.value)} />
                </div>
              )}

              {slot === "household_types" && (
                <PillMultiSelect
                  options={householdTypeLabels}
                  selected={value ? value.split(/,\s*/).filter(Boolean) : []}
                  onChange={(labels) => setValue(slot, labels.join(", "))}
                />
              )}

              {!CODE_WIDGET_SLOTS.includes(slot) && (
                <div className="input-shell">
                  <input type="text" value={value} onChange={(e) => setValue(slot, e.target.value)} />
                </div>
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
