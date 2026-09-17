import { useEffect, useRef, useState } from "react";

interface BirthDateSelectProps {
  value: string;
  onChange: (value: string) => void;
}

const CURRENT_YEAR = new Date().getFullYear();
const YEAR_OPTIONS = Array.from({ length: 100 }, (_, i) => CURRENT_YEAR - i);
const MONTH_OPTIONS = Array.from({ length: 12 }, (_, i) => i + 1);

function daysInMonth(year: number, month: number) {
  return new Date(year, month, 0).getDate();
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

const Chevron = () => (
  <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 9l6 6 6-6" />
  </svg>
);

/** 생년월일을 연→월→일 순으로만 고를 수 있게 하는 3단 드롭다운.
 * 네이티브 input[type=date]는 팝업 내부 순서를 브라우저가 정해서 연→월→일
 * 순서를 강제할 수 없어 대체했다(2026-09-17). */
export function BirthDateSelect({ value, onChange }: BirthDateSelectProps) {
  const [year, setYear] = useState("");
  const [month, setMonth] = useState("");
  const [day, setDay] = useState("");
  // 연/월 변경 시 우리가 직접 onChange("")를 호출해 value prop이 되돌아오는데,
  // 그 되돌아온 value로 이 이펙트가 다시 동기화하면 방금 고른 연/월까지
  // 같이 날아간다 - 그 한 번만 동기화를 건너뛴다.
  const skipNextSyncRef = useRef(false);

  useEffect(() => {
    if (skipNextSyncRef.current) {
      skipNextSyncRef.current = false;
      return;
    }
    if (!value) {
      setYear("");
      setMonth("");
      setDay("");
      return;
    }
    const [y, m, d] = value.split("-");
    setYear(y ?? "");
    setMonth(m ? String(Number(m)) : "");
    setDay(d ? String(Number(d)) : "");
  }, [value]);

  const dayCount = year && month ? daysInMonth(Number(year), Number(month)) : 31;
  const dayOptions = Array.from({ length: dayCount }, (_, i) => i + 1);

  const emit = (y: string, m: string, d: string) => {
    if (y && m && d) {
      const clampedDay = Math.min(Number(d), daysInMonth(Number(y), Number(m)));
      onChange(`${y}-${pad(Number(m))}-${pad(clampedDay)}`);
    }
  };

  return (
    <div className="date-select-row">
      <div className="select-shell">
        <select
          value={year}
          onChange={(e) => {
            const y = e.target.value;
            setYear(y);
            setMonth("");
            setDay("");
            // 일(day)이 리셋되면 더 이상 완전한 날짜가 아니므로, 부모가 예전 값을
            // 그대로 들고 있지 않도록 값을 비운다(2026-09-17 리뷰 반영).
            skipNextSyncRef.current = true;
            onChange("");
          }}
        >
          <option value="">연도</option>
          {YEAR_OPTIONS.map((y) => (
            <option key={y} value={y}>
              {y}년
            </option>
          ))}
        </select>
        <Chevron />
      </div>
      <div className="select-shell">
        <select
          value={month}
          disabled={!year}
          onChange={(e) => {
            const m = e.target.value;
            setMonth(m);
            setDay("");
            skipNextSyncRef.current = true;
            onChange("");
          }}
        >
          <option value="">월</option>
          {MONTH_OPTIONS.map((m) => (
            <option key={m} value={m}>
              {m}월
            </option>
          ))}
        </select>
        <Chevron />
      </div>
      <div className="select-shell">
        <select
          value={day}
          disabled={!year || !month}
          onChange={(e) => {
            const d = e.target.value;
            setDay(d);
            emit(year, month, d);
          }}
        >
          <option value="">일</option>
          {dayOptions.map((d) => (
            <option key={d} value={d}>
              {d}일
            </option>
          ))}
        </select>
        <Chevron />
      </div>
    </div>
  );
}
