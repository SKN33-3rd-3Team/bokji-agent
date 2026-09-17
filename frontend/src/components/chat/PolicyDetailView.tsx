import type { PolicyView } from "@/types/chat";
import { amountNote, badgeColor, cardIntro, dupShortNote, regionLabel } from "@/utils/policy";

interface PolicyDetailViewProps {
  policy: PolicyView;
  onBack: () => void;
  onAskQuestion: (policy: PolicyView) => void;
}

/** S-07 정책 상세. */
export function PolicyDetailView({ policy, onBack, onAskQuestion }: PolicyDetailViewProps) {
  const color = badgeColor(policy.eligibility_status);
  const detail = policy.detail;

  return (
    <div>
      <button type="button" className="btn-outline" style={{ marginBottom: 16, border: "none", padding: 0, height: "auto" }} onClick={onBack}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.3} strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 18l-6-6 6-6" />
        </svg>
        목록으로
      </button>

      <div className="card">
        <span className={`badge ${color}`}>
          <span className="dot" />
          {policy.badge || policy.eligibility_status}
        </span>
        <span style={{ fontSize: 19, fontWeight: 800, margin: "0 10px" }}>{policy.title}</span>
        <span className="text-muted" style={{ fontSize: 12.5 }}>{regionLabel(policy)}</span>

        {/* S07-01: AI 요약 — 원문 대조 검증을 통과한 결과만 여기 오므로 배지는 고정 문구로 표시 */}
        <div style={{ background: "var(--primary-soft)", border: "1px solid var(--border)", borderRadius: 11, padding: "14px 16px", margin: "16px 0 4px" }}>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: "var(--primary)", marginBottom: 6 }}>
            ✨ AI 요약 (원문 대조 검증완료)
          </div>
          <p style={{ fontSize: 14.5, lineHeight: 1.65, margin: 0 }}>{cardIntro(policy)}</p>
        </div>

        {/* S07-02: 지원자격 확인 상태 — eligibility_status만 단독 노출하지 않는다 */}
        <div style={{ fontSize: 13, fontWeight: 700, margin: "22px 0 9px" }}>지원자격</div>
        <div>
          {policy.verification_checked.map((item) => (
            <span key={`checked-${item}`} className="cond on">확인함 · {item}</span>
          ))}
          {policy.verification_unchecked.map((item) => (
            <span key={`unchecked-${item}`} className="cond off">미확인 · {item}</span>
          ))}
        </div>

        {/* S07-03: 지원금액 · 중복수급 */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 10 }}>
          <div className="card" style={{ padding: "14px 16px", background: "var(--bg)" }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600, marginBottom: 5 }}>지원금액</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>{policy.amount_label || "지원금액 확인 필요"}</div>
            <div className="text-muted" style={{ fontSize: 11.5, marginTop: 5, lineHeight: 1.5 }}>{amountNote(policy)}</div>
          </div>
          <div className="card" style={{ padding: "14px 16px", background: "var(--bg)" }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600, marginBottom: 5 }}>중복수급</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>{policy.duplicate_status || "미확인"}</div>
            <div className="text-muted" style={{ fontSize: 11.5, marginTop: 5, lineHeight: 1.5 }}>
              {policy.duplicate_note || dupShortNote(policy)}
            </div>
          </div>
        </div>

        {/* S07-04: 추가 확인이 필요한 항목 — 원문 그대로, 요약/각색 금지 */}
        {policy.needs_confirmation.length > 0 && (
          <div style={{ background: "var(--amber-bg)", border: "1px solid var(--amber-border)", borderRadius: 11, padding: "15px 17px", marginTop: 22 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 13.5, fontWeight: 700, color: "var(--amber-text)", marginBottom: 9 }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.3} strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
                <path d="M12 9v4M12 17h.01" />
              </svg>
              추가 확인이 필요한 항목
            </div>
            <ul style={{ margin: 0, paddingLeft: 19 }}>
              {policy.needs_confirmation.map((item) => (
                <li key={item} style={{ fontSize: 13, lineHeight: 1.65, color: "var(--amber-text)", marginBottom: 5 }}>
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* S07-05: 근거 문서 확인 — 관련 법령 + 원문 링크, 외부 링크는 새 탭 */}
        {policy.related_law.length > 0 && (
          <>
            <div style={{ fontSize: 13, fontWeight: 700, margin: "22px 0 9px" }}>관련 법령</div>
            <div>
              {policy.related_law.map((law) =>
                law.source_url ? (
                  <a key={law.law_name} className="chip" href={law.source_url} target="_blank" rel="noopener noreferrer">
                    ⚖ {law.law_name}
                  </a>
                ) : (
                  <span key={law.law_name} className="chip">⚖ {law.law_name}</span>
                ),
              )}
            </div>
          </>
        )}

        {/* S07-06: 구비서류 — ⚠ 배열이 아니라 원문 문자열 1개(보류 항목). 칩으로 쪼개지 않고 그대로 표시 */}
        {(detail.required_documents || detail.required_documents_official || detail.required_documents_self) && (
          <>
            <div style={{ fontSize: 13, fontWeight: 700, margin: "22px 0 9px" }}>구비서류</div>
            {detail.required_documents && (
              <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{detail.required_documents}</p>
            )}
            {detail.required_documents_official && (
              <>
                <p style={{ fontSize: 12.5, fontWeight: 700, margin: "10px 0 4px" }}>공무원 확인 구비서류</p>
                <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{detail.required_documents_official}</p>
              </>
            )}
            {detail.required_documents_self && (
              <>
                <p style={{ fontSize: 12.5, fontWeight: 700, margin: "10px 0 4px" }}>본인확인 필요 구비서류</p>
                <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{detail.required_documents_self}</p>
              </>
            )}
          </>
        )}

        {detail.source_url && (
          <div className="card" style={{ padding: "13px 15px", marginTop: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 13.5, fontWeight: 700, marginBottom: 6 }}>
              📁 근거 문서 확인 (1건)
            </div>
            <a href={detail.source_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 13, fontWeight: 600 }}>
              {detail.source_name || "근거 문서"}
            </a>
          </div>
        )}

        {detail.organization && (
          <p className="text-muted" style={{ fontSize: 12.5, marginTop: 16 }}>문의처: {detail.organization}</p>
        )}

        {/* S07-07 */}
        <button type="button" className="btn-primary" style={{ marginTop: 24 }} onClick={() => onAskQuestion(policy)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          이 정책에 대해 추가 질문하기
        </button>
      </div>
    </div>
  );
}
