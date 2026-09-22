import { useState } from "react";
import { ChatBubble } from "./ChatBubble";
import { LlmDebugPanel } from "./LlmDebugPanel";
import { UnknownToggle } from "@/components/common/UnknownToggle";
import { stripNumberedSlotList } from "@/utils/chatQuestion";
import {
  CALC_FORM_SKIP_ALL_LABEL,
  CALC_FORM_SKIP_NOTICE,
  CALC_FORM_SUBMIT_LABEL,
  UNKNOWN_CHOICE_LABEL,
  UNKNOWN_FIELD_NOTE,
} from "@/constants/labels";
import type { CalculationAnswers, CalculationSlotInput, ChatResponse } from "@/types/chat";

/**
 * number 위젯 값을 정수로 변환·검증한다. `Number.parseInt`는 소수부를
 * 자르고("2.5"→2) 지수 표기를 잘못 끊어 읽는다("1e1"→1) — 값 전체를
 * `Number()`로 해석한 뒤 정수·범위를 확인해 둘 다 방지한다. 유효하지
 * 않으면 null을 반환해 미입력과 동일하게 취급한다(제출 버튼 비활성화).
 */
function parseIntegerSlotValue(raw: string, input: CalculationSlotInput): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || !Number.isInteger(value)) return null;
  if (input.minimum !== null && value < input.minimum) return null;
  if (input.maximum !== null && value > input.maximum) return null;
  return value;
}

interface CalcFollowupFormProps {
  response: ChatResponse;
  /** 구조화 답변(API-11 calc_answers)으로 제출 */
  onSubmit: (answers: CalculationAnswers) => void;
  /** 전부 건너뛰기 — 자유 문장(API-11 message)으로 제출 */
  onSkip: (message: string) => void;
  isSubmitting: boolean;
}

/**
 * S05-03: 지원금 계산 되묻기 폼(N10a request_calc_info).
 *
 * 왜 필요한가: 그래프가 되묻는 지점은 두 군데다. 하나는 자격 판정 전
 * 하드 게이트 슬롯(N3 → SlotFollowupForm), 다른 하나가 여기 — 이미 자격은
 * 충족했고 **금액 계산에만** 필요한 소프트 슬롯(혼인 상태·임신 상태·가구원
 * 수·자녀 수)과 정책별 선택형 조건(예: 자연분만/제왕절개)이다.
 *
 * 이 화면이 없던 동안에는 계산 되묻기가 오면 missing_slots가 비어 있어
 * SlotFollowupForm이 필드 0개짜리 폼을 그렸고, "이 정보로 계속" 버튼이 영영
 * 비활성이라 답을 보낼 수 없었다(자유 입력창도 되묻기 중에는 숨는다).
 *
 * 답변은 자유 문장이 아니라 구조화된 calc_answers로 보낸다 — 백엔드가
 * interrupt_id와 선택지를 직접 검증하므로(merge_structured_calc_answer),
 * 문장을 다시 파싱하면서 값이 조용히 어긋날 여지가 없다.
 *
 * 항목마다 "모름"을 고를 수 있다(2026-09-20). 그 전에는 "전부 답하거나 /
 * 전부 건너뛰거나" 둘뿐이라, 혼인 상태는 알지만 가구원 수는 모르는 흔한
 * 경우에 아는 값까지 같이 버려야 했다. 모름은 unknown_slots/unknown_choices에
 * 항목 이름으로 담아 보내고, 백엔드가 그 항목만 "미확인"으로 확정한 뒤
 * 다시 묻지 않는다.
 */
export function CalcFollowupForm({ response, onSubmit, onSkip, isSubmitting }: CalcFollowupFormProps) {
  const slotInputs = response.calc_slot_inputs;
  const choices = response.calc_missing_choices;

  const [slotValues, setSlotValues] = useState<Record<string, string>>({});
  const [choiceValues, setChoiceValues] = useState<Record<string, string>>({});
  const [skipped, setSkipped] = useState<Record<string, boolean>>({});

  const toggleSkip = (key: string) => setSkipped((prev) => ({ ...prev, [key]: !prev[key] }));
  const clearSkip = (key: string) =>
    setSkipped((prev) => (prev[key] ? { ...prev, [key]: false } : prev));

  const allAnswered =
    slotInputs.every(
      (input) =>
        skipped[input.slot] ||
        (input.input_type === "number"
          ? parseIntegerSlotValue(slotValues[input.slot] ?? "", input) !== null
          : Boolean(slotValues[input.slot])),
    ) &&
    choices.every((choice) => skipped[choice.policy_id] || Boolean(choiceValues[choice.policy_id]));

  const submit = () => {
    if (!response.interrupt_id) return;
    const slots: Record<string, string | number> = {};
    const unknownSlots: string[] = [];
    for (const input of slotInputs) {
      // "모름"은 값이 아니라 항목 이름으로 보낸다. 같은 항목을 값과 모름
      // 양쪽에 넣으면 백엔드가 전체를 거절하므로 둘 중 하나만 채운다.
      if (skipped[input.slot]) {
        unknownSlots.push(input.slot);
        continue;
      }
      const raw = slotValues[input.slot];
      if (!raw) continue;
      // number 위젯 값은 반드시 정수로 보낸다 — 백엔드가 StrictInt로 검증해
      // 문자열이면 400(VALIDATION_ERROR)이 된다.
      if (input.input_type === "number") {
        const parsed = parseIntegerSlotValue(raw, input);
        if (parsed === null) continue;
        slots[input.slot] = parsed;
      } else {
        slots[input.slot] = raw;
      }
    }
    const answerChoices: Record<string, string> = {};
    const unknownChoices: string[] = [];
    for (const choice of choices) {
      if (skipped[choice.policy_id]) {
        unknownChoices.push(choice.policy_id);
        continue;
      }
      const picked = choiceValues[choice.policy_id];
      if (picked) answerChoices[choice.policy_id] = picked;
    }
    const answers: CalculationAnswers = { interrupt_id: response.interrupt_id };
    if (Object.keys(slots).length > 0) answers.slots = slots;
    if (Object.keys(answerChoices).length > 0) answers.choices = answerChoices;
    if (unknownSlots.length > 0) answers.unknown_slots = unknownSlots;
    if (unknownChoices.length > 0) answers.unknown_choices = unknownChoices;
    onSubmit(answers);
  };

  return (
    <div>
      <ChatBubble role="assistant" text={stripNumberedSlotList(response.question ?? "")} />
      <LlmDebugPanel response={response} />

      <div className="card" style={{ padding: "18px 20px", marginTop: 10 }}>
        {slotInputs.map((input) => {
          const value = slotValues[input.slot] ?? "";
          const isSkipped = Boolean(skipped[input.slot]);
          const setValue = (next: string) => {
            setSlotValues((prev) => ({ ...prev, [input.slot]: next }));
            clearSkip(input.slot);
          };

          return (
            <div className="field" key={input.slot}>
              <div className="field-head">
                <label htmlFor={`calc-${input.slot}`}>{input.label}</label>
                <UnknownToggle
                  active={isSkipped}
                  onToggle={() => toggleSkip(input.slot)}
                  fieldLabel={input.label}
                />
              </div>
              {isSkipped && <p className="field-unknown-note">{UNKNOWN_FIELD_NOTE}</p>}

              {!isSkipped &&
                (input.input_type === "select" ? (
                  <div className="select-shell">
                    <select
                      id={`calc-${input.slot}`}
                      value={value}
                      onChange={(e) => setValue(e.target.value)}
                    >
                      <option value="">선택하세요</option>
                      {input.options.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
                  </div>
                ) : (
                  <div className="input-shell">
                    <input
                      id={`calc-${input.slot}`}
                      type="number"
                      inputMode="numeric"
                      min={input.minimum ?? undefined}
                      max={input.maximum ?? undefined}
                      step={1}
                      placeholder={
                        input.minimum !== null && input.maximum !== null
                          ? `${input.minimum} ~ ${input.maximum}`
                          : "숫자로 입력해 주세요"
                      }
                      value={value}
                      onChange={(e) => setValue(e.target.value)}
                    />
                  </div>
                ))}
            </div>
          );
        })}

        {choices.map((choice) => {
          const isSkipped = Boolean(skipped[choice.policy_id]);
          return (
            <div className="field" key={choice.policy_id}>
              <div className="field-head">
                <label>{choice.policy_title}</label>
                <UnknownToggle
                  active={isSkipped}
                  onToggle={() => toggleSkip(choice.policy_id)}
                  label={UNKNOWN_CHOICE_LABEL}
                  fieldLabel={choice.policy_title}
                />
              </div>
              {isSkipped ? (
                <p className="field-unknown-note">{UNKNOWN_FIELD_NOTE}</p>
              ) : (
                <>
                  <p className="helper">어떤 방식에 해당하시나요?</p>
                  <div className="radio-group">
                    {choice.labels.map((label) => (
                      <label
                        key={label}
                        className={`radio-opt${choiceValues[choice.policy_id] === label ? " sel" : ""}`}
                      >
                        <input
                          type="radio"
                          name={`calc-choice-${choice.policy_id}`}
                          checked={choiceValues[choice.policy_id] === label}
                          onChange={() => {
                            setChoiceValues((prev) => ({ ...prev, [choice.policy_id]: label }));
                            clearSkip(choice.policy_id);
                          }}
                        />
                        <span className="dot" />
                        {label}
                      </label>
                    ))}
                  </div>
                </>
              )}
            </div>
          );
        })}

        <button
          className="btn-primary"
          onClick={submit}
          disabled={isSubmitting || !allAnswered || !response.interrupt_id}
        >
          {CALC_FORM_SUBMIT_LABEL}
        </button>

        {/* 항목별 "모름"이 생긴 뒤에도 남겨 둔다 — 아는 게 하나도 없을 때
            토글을 항목 수만큼 누르는 것보다 한 번이 빠르다. */}
        <button
          type="button"
          className="btn-outline"
          style={{ width: "100%", marginTop: 10 }}
          onClick={() => onSkip(CALC_FORM_SKIP_ALL_LABEL)}
          disabled={isSubmitting}
        >
          {CALC_FORM_SKIP_ALL_LABEL}
        </button>
        <p className="helper" style={{ marginTop: 8 }}>{CALC_FORM_SKIP_NOTICE}</p>
      </div>
    </div>
  );
}
