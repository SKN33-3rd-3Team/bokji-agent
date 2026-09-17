import { useState, type ReactNode } from "react";

interface PasswordInputProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoComplete?: string;
  required?: boolean;
  shellClassName?: string;
  leadingIcon?: ReactNode;
}

/**
 * 눈 아이콘으로 평문/마스킹을 토글하는 비밀번호 입력 — LoginPage(S-01)에
 * 있던 패턴을 회원가입·마이페이지의 모든 비밀번호 입력에도 동일하게 쓴다.
 */
export function PasswordInput({
  id,
  value,
  onChange,
  placeholder,
  autoComplete,
  required,
  shellClassName,
  leadingIcon,
}: PasswordInputProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div className={`input-shell${leadingIcon ? " has-icon" : ""}${shellClassName ? ` ${shellClassName}` : ""}`}>
      {leadingIcon}
      <input
        id={id}
        type={visible ? "text" : "password"}
        placeholder={placeholder}
        autoComplete={autoComplete}
        required={required}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ paddingRight: 40 }}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? "비밀번호 숨기기" : "비밀번호 표시"}
        style={{ position: "absolute", right: 13, background: "none", border: "none", color: "var(--text-faint)", display: "flex" }}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ width: 17, height: 17 }}>
          <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </button>
    </div>
  );
}
