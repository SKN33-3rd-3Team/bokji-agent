import { useState } from "react";
import { ChatBubble } from "./ChatBubble";
import { LlmDebugPanel } from "./LlmDebugPanel";
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

interface SlotFollowupFormProps {
  response: ChatResponse;
  /** missing_slots[0] — 배열 순서가 곧 질문 순서라 항상 첫 슬롯만 그린다. */
  slot: HardGateSlot;
  onSubmit: (message: string) => void;
  isSubmitting: boolean;
}

/** S05-01: 하드 게이트 슬롯이 부족할 때 되묻는 폼 — 슬롯 타입별 위젯 자동 구성. */
export function SlotFollowupForm({ response, slot, onSubmit, isSubmitting }: SlotFollowupFormProps) {
  const { data: options } = useSearchOptions();
  const [region, setRegion] = useState("");
  const [gender, setGender] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [incomeBracket, setIncomeBracket] = useState("");
  const [disabilityStatus, setDisabilityStatus] = useState("");
  const [employmentText, setEmploymentText] = useState("");

  const label = SLOT_LABELS_KO[slot];

  const submit = (valueLabel: string) => onSubmit(`${label}: ${valueLabel}`);

  return (
    <div>
      <ChatBubble role="assistant" text={response.question ?? ""} />
      <LlmDebugPanel response={response} />
      <div className="card" style={{ padding: "18px 20px", marginTop: 10 }}>
        {slot === "region" && (
          <div className="field">
            <label>{label}</label>
            <div className="select-shell">
              <select value={region} onChange={(e) => setRegion(e.target.value)}>
                <option value="">선택하세요</option>
                {(options?.sido_options ?? FALLBACK_SIDO_OPTIONS).map((sido) => (
                  <option key={sido} value={sido}>
                    {sido}
                  </option>
                ))}
              </select>
              <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </div>
            <button className="btn-primary" disabled={!region || isSubmitting} onClick={() => submit(region)}>
              이 정보로 계속
            </button>
          </div>
        )}

        {slot === "gender" && (
          <div className="field">
            <label>{label}</label>
            <div className="radio-group">
              {Object.entries(GENDER_LABELS_KO).map(([code, koLabel]) => (
                <label key={code} className={`radio-opt${gender === code ? " sel" : ""}`}>
                  <input type="radio" name="gender" value={code} checked={gender === code} onChange={() => setGender(code)} />
                  <span className="dot" />
                  {koLabel}
                </label>
              ))}
            </div>
            <button className="btn-primary" disabled={!gender || isSubmitting} onClick={() => submit(GENDER_LABELS_KO[gender])}>
              이 정보로 계속
            </button>
          </div>
        )}

        {slot === "birth_date" && (
          <div className="field">
            <label>{label}</label>
            <div className="input-shell">
              <input type="date" value={birthDate} onChange={(e) => setBirthDate(e.target.value)} />
            </div>
            <button className="btn-primary" disabled={!birthDate || isSubmitting} onClick={() => submit(birthDate)}>
              이 정보로 계속
            </button>
          </div>
        )}

        {slot === "income_bracket" && (
          <div className="field">
            <label>{label}</label>
            <div className="select-shell">
              <select value={incomeBracket} onChange={(e) => setIncomeBracket(e.target.value)}>
                <option value="">선택하세요</option>
                {Object.entries(options?.income_bracket_options?.reduce<Record<string, string>>((acc, o) => ({ ...acc, [o.code]: o.label }), {}) ?? INCOME_BRACKET_LABELS_KO).map(([code, koLabel]) => (
                  <option key={code} value={code}>
                    {koLabel}
                  </option>
                ))}
              </select>
              <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </div>
            <button
              className="btn-primary"
              disabled={!incomeBracket || isSubmitting}
              onClick={() => submit(INCOME_BRACKET_LABELS_KO[incomeBracket] ?? incomeBracket)}
            >
              이 정보로 계속
            </button>
          </div>
        )}

        {slot === "disability_status" && (
          <div className="field">
            <label>{label}</label>
            <div className="radio-group">
              {Object.entries(DISABILITY_LABELS_KO).map(([code, koLabel]) => (
                <label key={code} className={`radio-opt${disabilityStatus === code ? " sel" : ""}`}>
                  <input
                    type="radio"
                    name="disability_status"
                    value={code}
                    checked={disabilityStatus === code}
                    onChange={() => setDisabilityStatus(code)}
                  />
                  <span className="dot" />
                  {koLabel}
                </label>
              ))}
            </div>
            <button
              className="btn-primary"
              disabled={!disabilityStatus || isSubmitting}
              onClick={() => submit(DISABILITY_LABELS_KO[disabilityStatus])}
            >
              이 정보로 계속
            </button>
          </div>
        )}

        {slot === "employment_status" && (
          <div className="field">
            <label>{label}</label>
            <p className="helper">
              예: {Object.values(EMPLOYMENT_STATUS_LABELS_KO).join(" / ")} 중 자유롭게 입력해 주세요.
            </p>
            <div className="input-shell">
              <input
                type="text"
                placeholder="예: 회사 다니고 있어요"
                value={employmentText}
                onChange={(e) => setEmploymentText(e.target.value)}
              />
            </div>
            <button
              className="btn-primary"
              disabled={!employmentText || isSubmitting}
              onClick={() => onSubmit(employmentText)}
            >
              이 정보로 계속
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
