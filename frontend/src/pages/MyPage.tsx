import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { PillMultiSelect } from "@/components/common/PillMultiSelect";
import { ConfirmModal } from "@/components/common/ConfirmModal";
import { PasswordInput } from "@/components/common/PasswordInput";
import { BirthDateSelect } from "@/components/common/BirthDateSelect";
import { Toast } from "@/components/common/Toast";
import { useAuth } from "@/features/auth/useAuth";
import { useChangePassword, useDeleteAccount, useProfile, useUpdateProfile } from "@/features/auth/useMyPage";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import {
  DISABILITY_LABELS_KO,
  DISABILITY_NONE,
  FALLBACK_HOUSEHOLD_TYPE_OPTIONS,
  FALLBACK_INCOME_BRACKET_OPTIONS,
  GENDER_LABELS_KO,
  GENDER_NONE,
  HOUSEHOLD_TYPE_LABELS_KO,
  INCOME_BRACKET_LABELS_KO,
  INCOME_BRACKET_NONE,
  VETERAN_LABELS_KO,
  VETERAN_NONE,
} from "@/constants/labels";
import { ApiError } from "@/api/client";
import type { DisabilityStatus, Gender, IncomeBracket, VeteranStatus } from "@/types/auth";

function labelOrUnset(value: string, map?: Record<string, string>): string {
  if (!value) return "미설정";
  return map?.[value] ?? value;
}

export function MyPage() {
  const navigate = useNavigate();
  const { logout, setUser } = useAuth();
  const { data: profile, isLoading, error: profileError } = useProfile();
  const { data: options } = useSearchOptions();
  const updateProfile = useUpdateProfile();
  const changePassword = useChangePassword();
  const deleteAccount = useDeleteAccount();

  const [editMode, setEditMode] = useState(false);
  const [saveToast, setSaveToast] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [region, setRegion] = useState("");
  const [gender, setGender] = useState<Gender | "">("");
  const [birthDate, setBirthDate] = useState("");
  const [interests, setInterests] = useState<string[]>([]);
  const [disabilityStatus, setDisabilityStatus] = useState<DisabilityStatus | "">("");
  const [householdTypes, setHouseholdTypes] = useState<string[]>([]);
  const [veteranStatus, setVeteranStatus] = useState<VeteranStatus | "">("");
  const [incomeBracket, setIncomeBracket] = useState<IncomeBracket | "">("");

  useEffect(() => {
    if (!profile) return;
    setDisplayName(profile.display_name);
    setRegion(profile.region);
    setGender((profile.gender as Gender) || "");
    setBirthDate(profile.birth_date);
    setInterests(profile.interests);
    setDisabilityStatus((profile.disability_status as DisabilityStatus) || "");
    setHouseholdTypes(profile.household_types);
    setVeteranStatus((profile.veteran_status as VeteranStatus) || "");
    setIncomeBracket((profile.income_bracket as IncomeBracket) || "");
  }, [profile]);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [newPasswordMismatch, setNewPasswordMismatch] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [passwordToast, setPasswordToast] = useState(false);

  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteAgree, setDeleteAgree] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteSuccess, setDeleteSuccess] = useState(false);

  const householdTypeOptions = options?.household_type_options ?? FALLBACK_HOUSEHOLD_TYPE_OPTIONS;
  const householdOptions = householdTypeOptions.map((o) => o.label);
  const householdLabelToCode = Object.fromEntries(householdTypeOptions.map((o) => [o.label, o.code]));
  const selectedHouseholdLabels = householdTypes.map((code) => HOUSEHOLD_TYPE_LABELS_KO[code] ?? code);
  const interestFieldOptions = options?.interest_field_options ?? Object.values(HOUSEHOLD_TYPE_LABELS_KO);

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    setSaveError(null);
    try {
      // API-05 비고: 부분 diff 대신 화면에 있는 값 전부를 매번 전송한다(실수 방지 권장 방식).
      await updateProfile.mutateAsync({
        display_name: displayName,
        region,
        gender: gender || undefined,
        birth_date: birthDate,
        interests,
        disability_status: disabilityStatus || undefined,
        veteran_status: veteranStatus || undefined,
        income_bracket: incomeBracket || undefined,
        household_types: householdTypes as never,
      });
      setEditMode(false);
      setSaveToast(true);
      setTimeout(() => setSaveToast(false), 3000);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "저장에 실패했습니다. 다시 시도해주세요.");
    }
  };

  const handleChangePassword = async (e: FormEvent) => {
    e.preventDefault();
    setPasswordMessage(null);

    if (newPassword !== newPasswordConfirm) {
      setNewPasswordMismatch(true);
      return;
    }
    setNewPasswordMismatch(false);

    try {
      await changePassword.mutateAsync({ current_password: currentPassword, new_password: newPassword });
      setCurrentPassword("");
      setNewPassword("");
      setNewPasswordConfirm("");
      setPasswordToast(true);
      setTimeout(() => setPasswordToast(false), 2500);
    } catch (err) {
      if (err instanceof ApiError) {
        // API-06 비고: PASSWORD_POLICY_VIOLATION은 위반 항목 목록을 별도 배열로 내려준다.
        const text = err.violations?.length ? err.violations.join(" / ") : err.message;
        setPasswordMessage({ ok: false, text });
      } else {
        setPasswordMessage({ ok: false, text: "비밀번호 변경에 실패했습니다." });
      }
    }
  };

  const handleDelete = async () => {
    setDeleteError(null);
    if (!deleteAgree) {
      setDeleteError("탈퇴 동의에 체크해 주세요.");
      return;
    }
    if (!deletePassword) {
      setDeleteError("비밀번호를 입력해 주세요.");
      return;
    }
    try {
      await deleteAccount.mutateAsync({ password: deletePassword });
      setDeleteModalOpen(false);
      setDeleteSuccess(true);
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "회원 탈퇴에 실패했습니다.");
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <div className="app-content">불러오는 중…</div>
      </AppShell>
    );
  }

  // API-04 실패(UNAUTHORIZED/USER_NOT_FOUND 등) — 로딩 중과 구분해서 원인을 보여준다.
  if (profileError || !profile) {
    return (
      <AppShell>
        <div className="app-content">
          <div style={{ background: "var(--red-bg)", border: "1px solid var(--red-border)", color: "var(--red-text)", borderRadius: 10, padding: "14px 16px", fontSize: 13.5 }}>
            {profileError instanceof ApiError ? profileError.message : "내 정보를 불러오지 못했습니다."}
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="app-content">
        <p style={{ fontSize: 12.5, fontWeight: 700, color: "var(--text-faint)", letterSpacing: "0.02em", margin: "0 0 14px" }}>
          마이페이지
        </p>

        {/* S09-01: 프로필 헤더 */}
        <div className="card" style={{ display: "flex", alignItems: "center", gap: 16, padding: "22px 26px", marginBottom: 16 }}>
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: 16,
              background: "linear-gradient(155deg, var(--sky-1), var(--primary))",
              color: "#fff",
              fontSize: 20,
              fontWeight: 800,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            {profile.display_name.slice(0, 1)}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
              <p style={{ fontSize: 18, fontWeight: 800, margin: 0 }}>{profile.display_name} 님</p>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5, background: "var(--violet-soft)", color: "var(--violet)", fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 999 }}>
                일반 회원
              </span>
            </div>
            <p className="text-faint" style={{ fontSize: 12.5, margin: "3px 0 0" }}>{profile.email}</p>
          </div>
          <span className="text-faint" style={{ fontSize: 12, whiteSpace: "nowrap" }}>가입일 {profile.created_at?.slice(0, 10)}</span>
        </div>

        {!editMode ? (
          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 18 }}>
              <h2 style={{ fontSize: 15.5, fontWeight: 800, margin: 0 }}>내 가입 정보</h2>
              <button
                type="button"
                onClick={() => setEditMode(true)}
                style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 5, background: "var(--primary-soft)", color: "var(--primary-hover)", border: "none", borderRadius: 8, fontSize: 12.5, fontWeight: 700, padding: "7px 12px" }}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.3} strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                </svg>
                수정하기
              </button>
            </div>

            {[
              ["거주 지역", labelOrUnset(profile.region)],
              ["성별", labelOrUnset(profile.gender, GENDER_LABELS_KO)],
              ["생년월일", labelOrUnset(profile.birth_date)],
              ["장애 등록 여부", labelOrUnset(profile.disability_status, DISABILITY_LABELS_KO)],
              ["보훈대상자 여부", labelOrUnset(profile.veteran_status, VETERAN_LABELS_KO)],
              ["소득 수준", labelOrUnset(profile.income_bracket, INCOME_BRACKET_LABELS_KO)],
              ["마케팅 수신", profile.marketing_opt_in ? "동의" : "미동의"],
            ].map(([label, value]) => (
              <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "13px 2px", borderBottom: "1px solid var(--border)" }}>
                <span style={{ fontSize: 13, color: "var(--text-muted)", fontWeight: 600 }}>{label}</span>
                <span style={{ fontSize: 13.5, fontWeight: 700 }}>{value}</span>
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "space-between", padding: "13px 2px" }}>
              <span style={{ fontSize: 13, color: "var(--text-muted)", fontWeight: 600 }}>가구 유형</span>
              <span>
                {profile.household_types.length
                  ? profile.household_types.map((code) => (
                      <span key={code} style={{ background: "var(--violet-soft)", color: "var(--violet)", fontSize: 12, fontWeight: 700, padding: "5px 11px", borderRadius: 999, marginLeft: 6 }}>
                        {HOUSEHOLD_TYPE_LABELS_KO[code] ?? code}
                      </span>
                    ))
                  : <span className="text-faint">미설정</span>}
              </span>
            </div>
          </div>
        ) : (
          <form className="card" style={{ marginBottom: 16 }} onSubmit={handleSave}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 18 }}>
              <h2 style={{ fontSize: 15.5, fontWeight: 800, margin: 0 }}>내 가입 정보 수정</h2>
              <button
                type="button"
                onClick={() => setEditMode(false)}
                style={{ marginLeft: "auto", background: "var(--bg)", color: "var(--text-muted)", border: "1px solid var(--border-strong)", borderRadius: 8, fontSize: 12.5, fontWeight: 700, padding: "6px 12px" }}
              >
                취소
              </button>
            </div>

            {saveError && (
              <div style={{ background: "var(--red-bg)", border: "1px solid var(--red-border)", color: "var(--red-text)", borderRadius: 10, padding: "12px 14px", marginBottom: 16, fontSize: 13 }}>
                {saveError}
              </div>
            )}

            <div className="field">
              <label>이름</label>
              <div className="input-shell"><input value={displayName} onChange={(e) => setDisplayName(e.target.value)} /></div>
            </div>
            <div className="field">
              <label>거주 지역</label>
              <div className="select-shell">
                <select value={region} onChange={(e) => setRegion(e.target.value)}>
                  <option value="">선택 안 함</option>
                  {(options?.sido_options ?? []).map((sido) => <option key={sido} value={sido}>{sido}</option>)}
                </select>
                <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
              </div>
            </div>
            <div className="field">
              <label>성별</label>
              <div className="radio-group">
                {[["", GENDER_NONE], ["male", "남성"], ["female", "여성"]].map(([code, koLabel]) => (
                  <label key={code} className={`radio-opt${gender === code ? " sel" : ""}`}>
                    <input type="radio" checked={gender === code} onChange={() => setGender(code as Gender | "")} />
                    <span className="dot" />{koLabel}
                  </label>
                ))}
              </div>
            </div>
            <div className="field">
              <label>생년월일</label>
              <BirthDateSelect value={birthDate} onChange={setBirthDate} />
            </div>
            <div className="field">
              <label>관심 지원조건</label>
              <PillMultiSelect options={interestFieldOptions} selected={interests} onChange={setInterests} />
            </div>
            <div className="field">
              <label>장애 등록 여부</label>
              <div className="radio-group">
                {[["", DISABILITY_NONE], ["registered", "등록 장애 있음"], ["not_registered", "등록 장애 없음"]].map(([code, koLabel]) => (
                  <label key={code} className={`radio-opt${disabilityStatus === code ? " sel" : ""}`}>
                    <input type="radio" checked={disabilityStatus === code} onChange={() => setDisabilityStatus(code as DisabilityStatus | "")} />
                    <span className="dot" />{koLabel}
                  </label>
                ))}
              </div>
            </div>
            <div className="field">
              <label>가구 유형 (해당하는 항목 모두 선택)</label>
              <PillMultiSelect
                options={householdOptions}
                selected={selectedHouseholdLabels}
                onChange={(labels) => setHouseholdTypes(labels.map((l) => householdLabelToCode[l] ?? l))}
              />
            </div>
            <div className="field">
              <label>국가유공자/보훈대상자 여부</label>
              <div className="radio-group">
                {[["", VETERAN_NONE], ["registered", "보훈대상자입니다"], ["not_registered", "해당 없음"]].map(([code, koLabel]) => (
                  <label key={code} className={`radio-opt${veteranStatus === code ? " sel" : ""}`}>
                    <input type="radio" checked={veteranStatus === code} onChange={() => setVeteranStatus(code as VeteranStatus | "")} />
                    <span className="dot" />{koLabel}
                  </label>
                ))}
              </div>
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>소득 수준</label>
              <div className="select-shell">
                <select value={incomeBracket} onChange={(e) => setIncomeBracket(e.target.value as IncomeBracket | "")}>
                  <option value="">{INCOME_BRACKET_NONE}</option>
                  {(options?.income_bracket_options ?? FALLBACK_INCOME_BRACKET_OPTIONS).map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
                </select>
                <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
              </div>
            </div>

            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <button type="button" className="btn-outline" style={{ flex: "0 0 110px" }} onClick={() => setEditMode(false)}>취소</button>
              <button type="submit" className="btn-primary" style={{ flex: 1 }} disabled={updateProfile.isPending}>
                {updateProfile.isPending ? "저장 중…" : "저장"}
              </button>
            </div>
          </form>
        )}

        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleChangePassword}>
          <h2 style={{ fontSize: 15.5, fontWeight: 800, margin: "0 0 4px" }}>비밀번호 변경</h2>
          <p className="helper" style={{ margin: "0 0 14px" }}>8자 이상, 영문·숫자·특수문자를 섞어 주세요.</p>
          {passwordMessage && (
            <div style={{ fontSize: 12.5, color: passwordMessage.ok ? "var(--green-text)" : "var(--red-text)", marginBottom: 12 }}>
              {passwordMessage.text}
            </div>
          )}
          <div className="field">
            <label>현재 비밀번호</label>
            <PasswordInput value={currentPassword} onChange={setCurrentPassword} autoComplete="current-password" />
          </div>
          <div className="field">
            <label>새 비밀번호</label>
            <PasswordInput value={newPassword} onChange={setNewPassword} autoComplete="new-password" />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>새 비밀번호 확인</label>
            <PasswordInput
              value={newPasswordConfirm}
              onChange={(v) => {
                setNewPasswordConfirm(v);
                if (newPasswordMismatch) setNewPasswordMismatch(false);
              }}
              autoComplete="new-password"
              shellClassName={newPasswordMismatch ? "err" : undefined}
            />
            {newPasswordMismatch && <span className="inline-err">새 비밀번호와 새 비밀번호 확인이 일치하지 않습니다.</span>}
          </div>
          <button type="submit" className="btn-primary" style={{ marginTop: 20 }} disabled={changePassword.isPending}>
            {changePassword.isPending ? "변경 중…" : "비밀번호 변경"}
          </button>
        </form>

        <div className="card" style={{ background: "var(--danger-soft)", borderColor: "var(--danger-border)", marginBottom: 16 }}>
          <h2 style={{ fontSize: 15.5, fontWeight: 800, margin: "0 0 4px", color: "var(--danger)" }}>회원 탈퇴</h2>
          <p style={{ fontSize: 12.5, color: "#8A3A30", lineHeight: 1.6, margin: "0 0 4px" }}>
            탈퇴하면 계정과 저장된 정보(이름·지역·성별·생년월일·관심조건)가 즉시 삭제되며 되돌릴 수 없습니다.
          </p>
          <button type="button" className="btn-danger" style={{ marginTop: 16 }} onClick={() => setDeleteModalOpen(true)}>
            회원 탈퇴
          </button>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          <button type="button" className="btn-outline" style={{ flex: 1 }} onClick={async () => { await logout(); navigate("/login"); }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></svg>
            로그아웃
          </button>
          <button type="button" className="btn-outline" style={{ flex: 1 }} onClick={() => navigate("/chat")}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
            상담으로 돌아가기
          </button>
        </div>
      </div>

      <ConfirmModal
        open={deleteModalOpen}
        title="정말 탈퇴하시겠어요?"
        description="되돌릴 수 없습니다. 비밀번호를 입력해 본인 확인 후 진행해 주세요."
        confirmLabel="회원 탈퇴"
        danger
        isSubmitting={deleteAccount.isPending}
        onConfirm={handleDelete}
        onCancel={() => { setDeleteModalOpen(false); setDeleteError(null); setDeletePassword(""); setDeleteAgree(false); }}
      >
        <div className="field">
          <label>비밀번호 확인</label>
          <PasswordInput value={deletePassword} onChange={setDeletePassword} autoComplete="current-password" />
        </div>
        <div className="check-row" style={{ marginBottom: 10 }}>
          <input type="checkbox" checked={deleteAgree} onChange={(e) => setDeleteAgree(e.target.checked)} />
          <label>위 내용을 확인했으며 탈퇴에 동의합니다.</label>
        </div>
        {deleteError && <p className="inline-err">{deleteError}</p>}
      </ConfirmModal>

      <ConfirmModal
        open={deleteSuccess}
        title="계정이 삭제되었습니다"
        description="그동안 이용해 주셔서 감사합니다. 저장된 정보는 모두 삭제되었습니다."
        confirmLabel="확인"
        hideCancel
        onConfirm={() => {
          setUser(null);
          navigate("/login");
        }}
      />

      {saveToast && <Toast message="다음 채팅부터 바뀐 값이 반영됩니다." />}
      {passwordToast && <Toast message="비밀번호가 성공적으로 변경되었습니다." />}
    </AppShell>
  );
}
