import type { PolicyView } from "@/types/chat";
import { badgeColor, documentChipItems, regionLabel } from "@/utils/policy";

interface PolicyCompareTableProps {
  policies: PolicyView[];
  onBackToList: () => void;
  onOpenDetail: (policyId: string) => void;
}

function eligibilitySummary(policy: PolicyView): { value: string; note?: string } {
  const checked = policy.verification_checked;
  const value = checked.length > 0 ? `확인함 · ${checked.join(", ")}` : "확인된 조건 없음";
  const unchecked = policy.verification_unchecked.length;
  return { value, note: unchecked ? `그 외 ${unchecked}개 항목 미확인` : undefined };
}

/** S-10 선택한 정책 비교 — policies 배열을 재사용, API 재호출 없음. */
export function PolicyCompareTable({ policies, onBackToList, onOpenDetail }: PolicyCompareTableProps) {
  if (policies.length === 0) {
    onBackToList();
    return null;
  }

  const rows: { label: string; render: (p: PolicyView) => { text: string; sub?: string; items?: string[] } }[] = [
    { label: "지역", render: (p) => ({ text: regionLabel(p) }) },
    { label: "지원자격", render: (p) => { const s = eligibilitySummary(p); return { text: s.value, sub: s.note }; } },
    { label: "지원금액", render: (p) => ({ text: p.amount_label || "지원금액 확인 필요" }) },
    { label: "중복수급", render: (p) => ({ text: p.duplicate_status || "미확인", sub: p.duplicate_note ?? undefined }) },
    {
      label: "관련 법령",
      render: (p) => ({ text: p.related_law.length ? p.related_law.map((l) => l.law_name).join(", ") : "확인된 법령 없음" }),
    },
    {
      label: "구비서류",
      // 원문에 줄바꿈으로 항목이 나열돼 있으면 칩으로(streamlit_ui/rendering.py 로직 포팅), 아니면 원문 한 줄.
      render: (p) => {
        const items = documentChipItems(p.detail.required_documents);
        return items
          ? { text: items.join(", "), items }
          : { text: p.detail.required_documents || "확인된 구비서류 없음" };
      },
    },
  ];

  return (
    <div>
      <button type="button" className="btn-outline" style={{ marginBottom: 16, border: "none", padding: 0, height: "auto" }} onClick={onBackToList}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.3} strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 18l-6-6 6-6" />
        </svg>
        목록으로
      </button>

      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 20, fontWeight: 800, margin: "0 0 5px" }}>선택한 정책 비교</h1>
        <p className="text-muted" style={{ fontSize: 13.5, margin: 0 }}>
          {policies.length}건을 나란히 비교합니다 · 값이 다른 항목은 노란색으로 표시돼요
        </p>
      </div>

      <table style={{ width: "100%", borderCollapse: "collapse", background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 14, overflow: "hidden" }}>
        <thead>
          <tr>
            <th style={{ borderBottom: "1px solid var(--border)" }} />
            {policies.map((p) => (
              <th key={p.policy_id} style={{ background: "var(--primary-soft)", padding: "13px 15px", borderBottom: "1px solid var(--border)", borderLeft: "1px solid var(--border)", textAlign: "left" }}>
                <span className={`badge ${badgeColor(p.eligibility_status)}`}>
                  <span className="dot" />
                  {p.badge || p.eligibility_status}
                </span>
                <div style={{ fontSize: 14, fontWeight: 800, marginTop: 6 }}>{p.title}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const cells = policies.map((p) => row.render(p));
            const isDiff = new Set(cells.map((c) => c.text)).size > 1;
            return (
              <tr key={row.label}>
                <th style={{ background: "var(--panel)", fontSize: 12.5, fontWeight: 700, color: "var(--text-muted)", whiteSpace: "nowrap", padding: "13px 15px", borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                  {row.label}
                </th>
                {cells.map((cell, i) => (
                  <td
                    key={policies[i].policy_id}
                    style={{
                      padding: "13px 15px",
                      borderBottom: "1px solid var(--border)",
                      borderLeft: "1px solid var(--border)",
                      background: isDiff ? "var(--diff-bg)" : undefined,
                      verticalAlign: "top",
                    }}
                  >
                    {isDiff && (
                      <span style={{ fontSize: 10, fontWeight: 700, color: "var(--amber-text)", background: "var(--amber-bg)", borderRadius: 5, padding: "1.5px 7px", display: "inline-block", marginBottom: 4 }}>
                        다름
                      </span>
                    )}
                    {cell.items ? (
                      <div>
                        {cell.items.map((item, itemIdx) => (
                          <span key={itemIdx} className="chip" style={{ fontSize: 11 }}>
                            {item}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--text)", display: "block" }}>{cell.text}</span>
                    )}
                    {cell.sub && (
                      <span className="text-muted" style={{ fontSize: 11.5, lineHeight: 1.5, display: "block", marginTop: 2 }}>
                        {cell.sub}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            );
          })}
          <tr>
            <td style={{ borderLeft: "none" }} />
            {policies.map((p) => (
              <td key={p.policy_id} style={{ padding: "16px 15px", borderLeft: "1px solid var(--border)" }}>
                <button type="button" className="btn-outline" style={{ width: "100%" }} onClick={() => onOpenDetail(p.policy_id)}>
                  상세보기
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 18l6-6-6-6" />
                  </svg>
                </button>
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
