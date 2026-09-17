import type { PolicyView } from "@/types/chat";
import { badgeColor, regionLabel } from "@/utils/policy";

interface PolicyCardProps {
  policy: PolicyView;
  selected: boolean;
  onToggleSelect: (policyId: string) => void;
  onOpenDetail: (policyId: string) => void;
}

/** S06-02/03/04: 정책 카드 — 배지/제목/금액/중복수급, 비교 선택, 자세히 보기. */
export function PolicyCard({ policy, selected, onToggleSelect, onOpenDetail }: PolicyCardProps) {
  const color = badgeColor(policy.eligibility_status);

  return (
    <div
      className="card"
      style={{
        padding: "16px 18px",
        display: "flex",
        alignItems: "center",
        gap: 14,
        marginBottom: 14,
        borderColor: selected ? "#c7d2fe" : undefined,
        boxShadow: selected ? "0 1px 2px rgba(20,20,25,0.02), 0 0 0 3px var(--primary-soft)" : undefined,
      }}
    >
      <input
        type="checkbox"
        checked={selected}
        onChange={() => onToggleSelect(policy.policy_id)}
        aria-label={`${policy.title} 비교 선택`}
        style={{ width: 20, height: 20, accentColor: "var(--primary)", flexShrink: 0 }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <p style={{ fontSize: 15.5, fontWeight: 700, margin: 0 }}>
          {policy.title} <span className="text-muted" style={{ fontSize: 11.5, fontWeight: 500 }}>· {regionLabel(policy)}</span>
        </p>
        {policy.verification_note && (
          <p className="text-muted" style={{ fontSize: 12.5, lineHeight: 1.5, margin: "4px 0 8px" }}>
            {policy.verification_note}
          </p>
        )}
        <span className={`badge ${color}`}>
          <span className="dot" />
          {policy.badge || policy.eligibility_status}
        </span>
        <span className="chip">{policy.amount_label || "지원금액 확인 필요"}</span>
        <span className="chip">중복수급 {policy.duplicate_status || "미확인"}</span>
      </div>
      <div style={{ width: 112, flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
        {selected && (
          <span className="text-muted" style={{ fontSize: 11 }}>
            비교 목록에 담김
          </span>
        )}
        <button type="button" className="btn-outline" style={{ width: "100%" }} onClick={() => onOpenDetail(policy.policy_id)}>
          자세히 보기
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 18l6-6-6-6" />
          </svg>
        </button>
      </div>
    </div>
  );
}
