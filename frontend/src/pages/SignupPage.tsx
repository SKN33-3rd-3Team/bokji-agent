import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/features/auth/useAuth";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import { PillMultiSelect } from "@/components/common/PillMultiSelect";
import { PasswordInput } from "@/components/common/PasswordInput";
import { BirthDateSelect } from "@/components/common/BirthDateSelect";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { ApiError } from "@/api/client";
import {
  DISABILITY_NONE,
  FALLBACK_HOUSEHOLD_TYPE_OPTIONS,
  FALLBACK_INCOME_BRACKET_OPTIONS,
  FALLBACK_SIDO_OPTIONS,
  FALLBACK_SIGNUP_INTEREST_OPTIONS,
  GENDER_NONE,
  INCOME_BRACKET_NONE,
  VETERAN_NONE,
} from "@/constants/labels";
import type {
  DisabilityStatus,
  Gender,
  HouseholdType,
  IncomeBracket,
  VeteranStatus,
} from "@/types/auth";

/** S-02 회원가입 — 디자인시안 Signup 아트보드 필드 구성 그대로. */
export function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const { data: options } = useSearchOptions();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const [gender, setGender] = useState<Gender | "">("");
  const [birthDate, setBirthDate] = useState("");
  const [interests, setInterests] = useState<string[]>([]);
  const [disabilityStatus, setDisabilityStatus] = useState<DisabilityStatus | "">("");
  const [veteranStatus, setVeteranStatus] = useState<VeteranStatus | "">("");
  const [householdTypes, setHouseholdTypes] = useState<string[]>([]);
  const [incomeBracket, setIncomeBracket] = useState<IncomeBracket | "">("");
  const [marketingOptIn, setMarketingOptIn] = useState(false);
  const [termsAgreed, setTermsAgreed] = useState(false);
  const [privacyAgreed, setPrivacyAgreed] = useState(false);

  const [passwordMismatch, setPasswordMismatch] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const sidoOptions = options?.sido_options ?? FALLBACK_SIDO_OPTIONS;
  const signupInterestOptions = options?.signup_interest_options ?? FALLBACK_SIGNUP_INTEREST_OPTIONS;
  const householdTypeOptions = options?.household_type_options ?? FALLBACK_HOUSEHOLD_TYPE_OPTIONS;
  const incomeOptions = options?.income_bracket_options ?? FALLBACK_INCOME_BRACKET_OPTIONS;

  const householdLabelToCode = Object.fromEntries(householdTypeOptions.map((o) => [o.label, o.code]));
  const selectedHouseholdLabels = householdTypes.map(
    (code) => householdTypeOptions.find((o) => o.code === code)?.label ?? code,
  );

  const canSubmit = termsAgreed && privacyAgreed;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setFieldErrors({});

    if (password !== passwordConfirm) {
      setPasswordMismatch(true);
      return;
    }
    setPasswordMismatch(false);

    setIsSubmitting(true);
    try {
      await signup({
        email,
        password,
        password_confirm: passwordConfirm,
        name,
        region: region || undefined,
        gender: (gender || undefined) as Gender | undefined,
        birth_date: birthDate || undefined,
        interests: interests.length ? interests : undefined,
        disability_status: (disabilityStatus || undefined) as DisabilityStatus | undefined,
        veteran_status: (veteranStatus || undefined) as VeteranStatus | undefined,
        income_bracket: (incomeBracket || undefined) as IncomeBracket | undefined,
        household_types: householdTypes.length ? (householdTypes as HouseholdType[]) : undefined,
        marketing_opt_in: marketingOptIn,
        terms_agreed: termsAgreed,
        privacy_agreed: privacyAgreed,
      });
      // 로그인과 동일하게 마이페이지 정보 기반 정책 자동 추천 홈 화면으로
      // 이동한다(PR #55 후속, LoginPage.tsx와 동일 정책 / API-14).
      navigate("/home", { state: { justSignedUp: true } });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.errorCode === "USERNAME_TAKEN") {
          setFieldErrors({ email: err.message });
        } else if (err.errorCode === "PASSWORD_POLICY_VIOLATION") {
          setFieldErrors({ password: (err.violations ?? [err.message]).join(" / ") });
        } else {
          setFormError(err.message);
        }
      } else {
        setFormError("일시적인 오류로 가입할 수 없습니다. 잠시 후 다시 시도해주세요.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      style={{
        width: "100%",
        minHeight: "100vh",
        background: "radial-gradient(900px 420px at 50% -60px, var(--sky-2) 0%, transparent 65%), var(--bg)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "52px 24px 64px",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", marginBottom: 26 }}>
        <div style={{ fontSize: 22, fontWeight: 800, marginBottom: 8 }}>복지 에이전트</div>
        <p className="text-muted" style={{ fontSize: 13.5, maxWidth: 460, textAlign: "center", margin: 0 }}>
          몇 가지 질문에 답하시면 맞춤 혜택 후보를 추천해 드립니다.
        </p>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ width: "100%", maxWidth: 480 }}>
        <h1 style={{ fontSize: 19, fontWeight: 800, margin: "0 0 4px" }}>회원가입</h1>
        <p className="text-faint" style={{ fontSize: 12.5, margin: "0 0 22px" }}>
          가입 후 마이페이지에서 정보를 관리할 수 있어요.
        </p>

        {formError && <ErrorBanner>{formError}</ErrorBanner>}

        <div className="field">
          <label>이메일 *</label>
          <div className="input-shell">
            <input type="email" placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          {fieldErrors.email && <span className="inline-err">{fieldErrors.email}</span>}
        </div>

        <div className="field">
          <label>비밀번호 *</label>
          <PasswordInput value={password} onChange={setPassword} required autoComplete="new-password" />
          <p className="helper">8자 이상, 영문·숫자·특수문자를 섞어 주세요.</p>
          {fieldErrors.password && <span className="inline-err">{fieldErrors.password}</span>}
        </div>

        <div className="field">
          <label>비밀번호 확인 *</label>
          <PasswordInput
            value={passwordConfirm}
            onChange={setPasswordConfirm}
            required
            autoComplete="new-password"
            shellClassName={passwordMismatch ? "err" : undefined}
          />
          {passwordMismatch && <span className="inline-err">비밀번호와 비밀번호 확인이 일치하지 않습니다.</span>}
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label>이름 *</label>
          <div className="input-shell">
            <input type="text" placeholder="실명 또는 닉네임" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "24px 0 4px" }}>
          <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
        </div>

        <div style={{ background: "#fafbff", border: "1px solid var(--border)", borderRadius: 14, padding: "18px 18px 6px", marginTop: 12 }}>
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 14, fontWeight: 800, margin: "0 0 3px" }}>기본 정보 (선택)</h2>
            <p className="text-faint" style={{ fontSize: 11.5, margin: 0 }}>입력한 정보는 마이페이지에 저장됩니다.</p>
          </div>

          <div className="field">
            <label>거주 지역</label>
            <div className="select-shell">
              <select value={region} onChange={(e) => setRegion(e.target.value)}>
                <option value="">선택 안 함</option>
                {sidoOptions.map((sido) => (
                  <option key={sido} value={sido}>{sido}</option>
                ))}
              </select>
              <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </div>
          </div>

          <div className="field">
            <label>성별</label>
            <div className="radio-group">
              <label className={`radio-opt${gender === "" ? " sel" : ""}`}>
                <input type="radio" name="gender" checked={gender === ""} onChange={() => setGender("")} />
                <span className="dot" />{GENDER_NONE}
              </label>
              <label className={`radio-opt${gender === "male" ? " sel" : ""}`}>
                <input type="radio" name="gender" checked={gender === "male"} onChange={() => setGender("male")} />
                <span className="dot" />남성
              </label>
              <label className={`radio-opt${gender === "female" ? " sel" : ""}`}>
                <input type="radio" name="gender" checked={gender === "female"} onChange={() => setGender("female")} />
                <span className="dot" />여성
              </label>
            </div>
          </div>

          <div className="field">
            <label>생년월일</label>
            <BirthDateSelect value={birthDate} onChange={setBirthDate} />
          </div>

          <div className="field">
            <label>해당하는 지원조건</label>
            <PillMultiSelect options={signupInterestOptions} selected={interests} onChange={setInterests} />
          </div>

          <div className="field">
            <label>장애 등록 여부</label>
            <div className="radio-group">
              <label className={`radio-opt${disabilityStatus === "" ? " sel" : ""}`}>
                <input type="radio" name="disability" checked={disabilityStatus === ""} onChange={() => setDisabilityStatus("")} />
                <span className="dot" />{DISABILITY_NONE}
              </label>
              <label className={`radio-opt${disabilityStatus === "registered" ? " sel" : ""}`}>
                <input type="radio" name="disability" checked={disabilityStatus === "registered"} onChange={() => setDisabilityStatus("registered")} />
                <span className="dot" />등록 장애 있음
              </label>
              <label className={`radio-opt${disabilityStatus === "not_registered" ? " sel" : ""}`}>
                <input type="radio" name="disability" checked={disabilityStatus === "not_registered"} onChange={() => setDisabilityStatus("not_registered")} />
                <span className="dot" />등록 장애 없음
              </label>
            </div>
          </div>

          <div className="field">
            <label>가구 유형 (해당하는 항목 모두 선택)</label>
            <PillMultiSelect
              options={householdTypeOptions.map((o) => o.label)}
              selected={selectedHouseholdLabels}
              onChange={(labels) => setHouseholdTypes(labels.map((l) => householdLabelToCode[l] ?? l))}
            />
          </div>

          <div className="field">
            <label>국가유공자/보훈대상자 여부</label>
            <div className="radio-group">
              <label className={`radio-opt${veteranStatus === "" ? " sel" : ""}`}>
                <input type="radio" name="veteran" checked={veteranStatus === ""} onChange={() => setVeteranStatus("")} />
                <span className="dot" />{VETERAN_NONE}
              </label>
              <label className={`radio-opt${veteranStatus === "registered" ? " sel" : ""}`}>
                <input type="radio" name="veteran" checked={veteranStatus === "registered"} onChange={() => setVeteranStatus("registered")} />
                <span className="dot" />보훈대상자입니다
              </label>
              <label className={`radio-opt${veteranStatus === "not_registered" ? " sel" : ""}`}>
                <input type="radio" name="veteran" checked={veteranStatus === "not_registered"} onChange={() => setVeteranStatus("not_registered")} />
                <span className="dot" />해당 없음
              </label>
            </div>
          </div>

          <div className="field" style={{ marginBottom: 16 }}>
            <label>소득 수준</label>
            <div className="select-shell">
              <select value={incomeBracket} onChange={(e) => setIncomeBracket(e.target.value as IncomeBracket | "")}>
                <option value="">{INCOME_BRACKET_NONE}</option>
                {incomeOptions.map((o) => (
                  <option key={o.code} value={o.code}>{o.label}</option>
                ))}
              </select>
              <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 22, paddingTop: 18, borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 11 }}>
          <div className="check-row">
            <input type="checkbox" checked={termsAgreed} onChange={(e) => setTermsAgreed(e.target.checked)} />
            <label><span className="tag-req">[필수]</span> 서비스 이용약관에 동의합니다.</label>
          </div>
          <div className="check-row">
            <input type="checkbox" checked={privacyAgreed} onChange={(e) => setPrivacyAgreed(e.target.checked)} />
            <label><span className="tag-req">[필수]</span> 개인정보 수집&middot;이용에 동의합니다.</label>
          </div>
          <div className="check-row">
            <input type="checkbox" checked={marketingOptIn} onChange={(e) => setMarketingOptIn(e.target.checked)} />
            <label><span className="tag-opt">[선택]</span> 혜택&middot;안내 정보 수신에 동의합니다.</label>
          </div>
        </div>

        <button type="submit" className="btn-primary" style={{ marginTop: 22 }} disabled={!canSubmit || isSubmitting}>
          {isSubmitting ? "가입 처리 중…" : "회원가입"}
        </button>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, fontSize: 13, marginTop: 16 }}>
          <span className="text-muted">이미 계정이 있으신가요?</span>
          <Link to="/login" style={{ fontWeight: 700 }}>로그인</Link>
        </div>
      </form>
    </div>
  );
}
