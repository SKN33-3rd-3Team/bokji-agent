import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/features/auth/useAuth";
import { PasswordInput } from "@/components/common/PasswordInput";
import { ApiError } from "@/api/client";

/** S-01 로그인 — 복지에이전트_디자인시안.html Main 아트보드 레이아웃 이식. */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [lockBanner, setLockBanner] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFieldError(null);
    setErrorBanner(null);
    setLockBanner(null);

    // S01-06: 클라이언트 1차 검증 — 미입력 시 서버 호출 자체를 생략한다.
    if (!email || !password) {
      setFieldError("이메일과 비밀번호를 모두 입력해 주세요.");
      return;
    }

    setIsSubmitting(true);
    try {
      await login({ email, password });
      navigate("/chat");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 423) {
          const seconds = err.remainingSeconds;
          setLockBanner(
            seconds
              ? `로그인 시도 초과로 계정이 잠겼습니다. 약 ${Math.ceil(seconds / 60)}분 후 다시 시도해주세요.`
              : "로그인 시도 초과로 계정이 잠겼습니다. 잠시 후 다시 시도해주세요.",
          );
        } else {
          // 401 등 — 서버 메시지를 가공 없이 그대로 노출한다(계정 존재 여부 추측 금지).
          setErrorBanner(err.message);
        }
      } else {
        setErrorBanner("일시적인 오류가 발생했습니다. 다시 시도해주세요.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const hasFieldError = Boolean(fieldError || errorBanner || lockBanner);

  return (
    <div
      style={{
        width: "100%",
        minHeight: "100vh",
        position: "relative",
        overflow: "hidden",
        background:
          "radial-gradient(760px 620px at 8% 18%, rgba(255,255,255,0.55) 0%, transparent 60%), linear-gradient(115deg, var(--sky-1) 0%, var(--sky-2) 42%, var(--sky-3) 72%, var(--bg) 100%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 72,
        padding: "0 64px",
      }}
    >
      <div style={{ flex: "0 0 560px" }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            background: "rgba(61,99,216,0.10)",
            border: "1px solid rgba(61,99,216,0.18)",
            color: "var(--primary)",
            fontSize: 12,
            fontWeight: 700,
            padding: "6px 12px",
            borderRadius: 999,
            marginBottom: 14,
          }}
        >
          정부 지원제도 안내 AI
        </div>
        <div style={{ fontSize: 54, fontWeight: 800, color: "var(--text)", letterSpacing: "-0.02em", marginBottom: 20 }}>
          복지 에이전트
        </div>
        <p style={{ fontSize: 19, fontWeight: 700, color: "var(--text)", lineHeight: 1.5, margin: "0 0 12px", maxWidth: 520 }}>
          복잡했던 복지 정책, 내가 원하는 정보를 바로 확인하세요.
        </p>
        <p style={{ fontSize: 16.5, fontWeight: 600, color: "var(--text-muted)", lineHeight: 1.5, maxWidth: 460, margin: "0 0 26px" }}>
          &ldquo;추측하지 않고, 오직 검증된 근거로만 답합니다.&rdquo;
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {[
            "공공서비스 지원제도 10,968건 데이터 기반",
            "국가법령정보 19만 건 이상 연계 검증",
            "근거가 부족하면 추측 대신 “미확인”으로 안내",
          ].map((text) => (
            <div key={text} style={{ display: "flex", alignItems: "center", gap: 11 }}>
              <span style={{ width: 22, height: 22, borderRadius: 999, background: "rgba(61,99,216,0.12)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
                  <path d="M5 13l4 4L19 7" />
                </svg>
              </span>
              <span style={{ fontSize: 14.5, color: "var(--text-muted)", fontWeight: 500 }}>{text}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ flex: "0 0 400px" }}>
        <form className="card" onSubmit={handleSubmit} style={{ width: "100%" }}>
          <div style={{ marginBottom: 24 }}>
            <h1 style={{ fontSize: 22, fontWeight: 700, margin: "0 0 6px" }}>로그인</h1>
            <p className="text-muted" style={{ fontSize: 13.5, margin: 0 }}>
              계정과 마이페이지 정보를 관리하려면 로그인해 주세요.
            </p>
          </div>

          {errorBanner && (
            <div style={{ background: "var(--red-bg)", border: "1px solid var(--red-border)", color: "var(--red-text)", borderRadius: 10, padding: "12px 14px", marginBottom: 16, fontSize: 13 }}>
              {errorBanner}
            </div>
          )}
          {lockBanner && (
            <div style={{ background: "var(--amber-bg)", border: "1px solid var(--amber-border)", color: "var(--amber-text)", borderRadius: 10, padding: "12px 14px", marginBottom: 16, fontSize: 13 }}>
              {lockBanner}
            </div>
          )}

          <div className="field">
            <label className={hasFieldError ? "err-label" : undefined} htmlFor="login-email">이메일</label>
            <div className={`input-shell has-icon${hasFieldError ? " err" : ""}`}>
              <svg className="leading" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <path d="M3 7l9 6 9-6" />
              </svg>
              <input id="login-email" type="email" placeholder="you@example.com" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label className={hasFieldError ? "err-label" : undefined} htmlFor="login-pw">비밀번호</label>
            <PasswordInput
              id="login-pw"
              placeholder="비밀번호 입력"
              autoComplete="current-password"
              value={password}
              onChange={setPassword}
              shellClassName={hasFieldError ? "err" : undefined}
              leadingIcon={
                <svg className="leading" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                  <rect x="4" y="10" width="16" height="11" rx="2" />
                  <path d="M8 10V7a4 4 0 0 1 8 0v3" />
                </svg>
              }
            />
            {fieldError && <span className="inline-err">{fieldError}</span>}
          </div>

          <button className="btn-primary" type="submit" disabled={isSubmitting} style={{ marginTop: 6 }}>
            {isSubmitting ? (
              <>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3}>
                  <circle cx="12" cy="12" r="9" opacity={0.25} />
                  <path d="M21 12a9 9 0 0 0-9-9" />
                </svg>
                로그인 중…
              </>
            ) : (
              <>
                로그인
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 18l6-6-6-6" />
                </svg>
              </>
            )}
          </button>

          <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "22px 0 16px" }}>
            <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
            <span className="text-faint" style={{ fontSize: 12, fontWeight: 500 }}>또는</span>
            <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
          </div>

          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, fontSize: 13.5 }}>
            <span className="text-muted">계정이 없으신가요?</span>
            <Link to="/signup" style={{ fontWeight: 700 }}>회원가입</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
