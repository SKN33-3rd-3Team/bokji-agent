interface PillMultiSelectProps {
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** 옵션 목록을 로딩하는 동안 살짝 흐리게 표시(선택) */
  disabled?: boolean;
}

/** 지원조건/관심분야/가구유형 등에서 재사용하는 다중선택 pill 그룹. */
export function PillMultiSelect({ options, selected, onChange, disabled }: PillMultiSelectProps) {
  const toggle = (value: string) => {
    if (disabled) return;
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  };

  return (
    <div className="pill-group" role="group" aria-disabled={disabled}>
      {options.map((option) => {
        const isOn = selected.includes(option);
        return (
          <button
            key={option}
            type="button"
            className={`pill${isOn ? " on" : ""}`}
            aria-pressed={isOn}
            onClick={() => toggle(option)}
            disabled={disabled}
          >
            {option}
          </button>
        );
      })}
    </div>
  );
}
