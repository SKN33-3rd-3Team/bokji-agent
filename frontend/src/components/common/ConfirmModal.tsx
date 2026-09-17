import type { ReactNode } from "react";

interface ConfirmModalProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  isSubmitting?: boolean;
  onConfirm: () => void;
  onCancel?: () => void;
  /** 안내/완료 팝업처럼 취소 없이 확인 버튼 하나만 필요할 때. */
  hideCancel?: boolean;
  children?: ReactNode;
}

/** 회원 탈퇴 확인, 완료 안내 등에서 재사용하는 팝업(API-07 등). */
export function ConfirmModal({
  open,
  title,
  description,
  confirmLabel = "확인",
  cancelLabel = "취소",
  danger,
  isSubmitting,
  onConfirm,
  onCancel,
  hideCancel,
  children,
}: ConfirmModalProps) {
  if (!open) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20,22,38,0.42)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
      }}
    >
      <div className="card" style={{ width: 400, padding: "24px 26px" }}>
        <h2 style={{ fontSize: 16, fontWeight: 800, margin: "0 0 8px" }}>{title}</h2>
        {description && (
          <p className="text-muted" style={{ fontSize: 13, lineHeight: 1.6, margin: "0 0 16px" }}>
            {description}
          </p>
        )}
        {children}
        <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
          {!hideCancel && (
            <button type="button" className="btn-outline" style={{ flex: 1 }} onClick={onCancel}>
              {cancelLabel}
            </button>
          )}
          <button
            type="button"
            className={danger ? "btn-danger" : "btn-primary"}
            style={{ flex: 1 }}
            onClick={onConfirm}
            disabled={isSubmitting}
          >
            {isSubmitting ? "처리 중…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
