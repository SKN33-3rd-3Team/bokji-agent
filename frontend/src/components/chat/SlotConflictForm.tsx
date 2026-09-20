import { useState } from "react";
import { ChatBubble } from "./ChatBubble";
import { LlmDebugPanel } from "./LlmDebugPanel";
import { PillMultiSelect } from "@/components/common/PillMultiSelect";
import { BirthDateSelect } from "@/components/common/BirthDateSelect";
import { UnknownToggle } from "@/components/common/UnknownToggle";
import { stripNumberedSlotList } from "@/utils/chatQuestion";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import {
  DISABILITY_LABELS_KO,
  FALLBACK_HOUSEHOLD_TYPE_OPTIONS,
  FALLBACK_SIDO_OPTIONS,
  GENDER_LABELS_KO,
  INCOME_BRACKET_LABELS_KO,
  SLOT_LABELS_KO,
  UNKNOWN_FIELD_NOTE,
} from "@/constants/labels";
import type { ChatResponse, HardGateSlot } from "@/types/chat";

// graph/slot_schema.py 기준 slot_conflicts는 region/gender/birth_date/
// income_bracket/disability_status/household_types 6개에서만 발생한다
// (veteran_status는 하드 게이트 슬롯이 아니라 대상 아님 — API-11 비고).
const EXTRA_SLOT_LABELS_KO: Record<string, string> = {
  household_types: "가구 유형",
};

// SlotFollowupForm과 같은 문구를 쓴다 - 백엔드(llm_gateway._DONT_KNOW_MARKERS)는
// 어느 폼에서 왔는지 모르고 문장만 본다.
const SKIP_VALUE = "모름";

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

  // 회원 정보 값도 채팅 값도 아니고 "지금은 모르겠다"는 답. 값을 비운 채
  // 넘기면 N2가 다시 물어보므로(그러다 상한에 닿으면 조용히 미확인 처리),
  // 사용자가 그 뜻을 명시적으로 고를 수 있어야 한다 — SlotFollowupForm의
  // "모름"과 같은 토글·같은 전송 문구("...: 모름")를 쓴다.
  const [skipped, setSkipped] = useState<Record<string, boolean>>({});

  const setValue = (slot: string, value: string) => {
    setValues((prev) => ({ ...prev, [slot]: value }));
    setSkipped((prev) => (prev[slot] ? { ...prev, [slot]: false } : prev));
  };
  const toggleSkip = (slot: string) => setSkipped((prev) => ({ ...prev, [slot]: !prev[slot] }));

  const submit = () => {
    const parts = slots.map((slot) => {
      const raw = values[slot] ?? "";
      let label = raw;
      if (skipped[slot]) label = SKIP_VALUE;
      else if (slot === "gender") label = GENDER_LABELS_KO[raw] ?? raw;
      else if (slot === "disability_status") label = DISABILITY_LABELS_KO[raw] ?? raw;
      else if (slot === "income_bracket") label = INCOME_BRACKET_LABELS_KO[raw] ?? raw;
      return `${slotLabel(slot)}: ${label}`;
    });
    onSubmit(parts.join(", "));
  };

  const allFilled = slots.every((slot) => skipped[slot] || values[slot]);
  const incomeBracketLabelMap =
    options?.income_bracket_options?.reduce<Record<string, string>>((acc, o) => ({ ...acc, [o.code]: o.label }), {}) ??
    INCOME_BRACKET_LABELS_KO;
  const householdTypeLabels = (options?.household_type_options ?? FALLBACK_HOUSEHOLD_TYPE_OPTIONS).map((o) => o.label);

  return (
    <div>
      <ChatBubble role="assistant" text={stripNumberedSlotList(response.question ?? "")} />
      <LlmDebugPanel response={response} />
      <div className="card" style={{ padding: "18px 20px", marginTop: 10 }}>
        {slots.map((slot) => {
          const value = values[slot] ?? "";
          const isSkipped = Boolean(skipped[slot]);
          return (
            <div className="field" key={slot}>
              <div className="field-head">
                <label>{slotLabel(slot)}</label>
                <UnknownToggle
                  active={isSkipped}
                  onToggle={() => toggleSkip(slot)}
                  fieldLabel={slotLabel(slot)}
                />
              </div>
              {isSkipped && <p className="field-unknown-note">{UNKNOWN_FIELD_NOTE}</p>}

              {!isSkipped && slot === "region" && (
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

              {!isSkipped && slot === "gender" && (
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

              {!isSkipped && slot === "disability_status" && (
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

              {!isSkipped && slot === "birth_date" && (
                <BirthDateSelect value={value} onChange={(v) => setValue(slot, v)} />
              )}

              {!isSkipped && slot === "income_bracket" && (
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

              {!isSkipped && slot === "employment_status" && (
                <div className="input-shell">
                  <input type="text" value={value} onChange={(e) => setValue(slot, e.target.value)} />
                </div>
              )}

              {!isSkipped && slot === "household_types" && (
                <PillMultiSelect
                  options={householdTypeLabels}
                  selected={value ? value.split(/,\s*/).filter(Boolean) : []}
                  onChange={(labels) => setValue(slot, labels.join(", "))}
                />
              )}

              {!isSkipped && !CODE_WIDGET_SLOTS.includes(slot) && (
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
