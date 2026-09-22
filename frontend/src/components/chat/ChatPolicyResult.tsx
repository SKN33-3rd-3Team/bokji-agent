import { useState } from "react";
import { usePolicySelection } from "@/features/chat/usePolicySelection";
import type { ChatResponse, PolicyView } from "@/types/chat";
import { PolicyCard } from "./PolicyCard";
import { PolicySummaryStats } from "./PolicySummaryStats";
import { PolicyDetailView } from "./PolicyDetailView";
import { PolicyCompareTable } from "./PolicyCompareTable";
import { LlmDebugPanel } from "./LlmDebugPanel";

/** 상담 턴별 결과와 보기 상태를 보존한다. 새 응답이 와도 이전 결과를 텍스트로 바꾸지 않는다. */
export function ChatPolicyResult({ response, onAskQuestion }: {
  response: ChatResponse;
  onAskQuestion?: (policy: PolicyView) => void;
}) {
  const compare = usePolicySelection();
  const [view, setView] = useState<"list" | "detail" | "compare">("list");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const back = () => setView("list");
  const openDetail = (id: string) => { setSelectedId(id); setView("detail"); };
  const activePolicy = response.policies.find(policy => policy.policy_id === selectedId);

  return (
    <section className="chat-policy-result" aria-label="상담 정책 결과" style={{ marginBottom: 20 }}>
      {view === "detail" && activePolicy ? (
        <PolicyDetailView policy={activePolicy} onBack={back} onAskQuestion={onAskQuestion} />
      ) : view === "compare" ? (
        <PolicyCompareTable
          policies={response.policies.filter(policy => compare.selectedIds.includes(policy.policy_id))}
          onBackToList={back} onOpenDetail={openDetail}
        />
      ) : (
        <>
          <PolicySummaryStats policies={response.policies} />
          {response.policies.map(policy => (
            <PolicyCard key={policy.policy_id} policy={policy}
              selected={compare.selectedIds.includes(policy.policy_id)}
              onToggleSelect={compare.toggle} onOpenDetail={openDetail} />
          ))}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
            background: "var(--text)", borderRadius: 14, padding: "12px 18px" }}>
            <span style={{ color: "var(--bg)", fontWeight: 600, fontSize: 13.5 }}>
              {compare.count === 0 ? "정책을 선택하면 나란히 비교할 수 있어요"
                : compare.count === 1 ? "1개만 더 선택하면 비교할 수 있어요" : `${compare.count}개 선택됨`}
            </span>
            <button type="button" className="btn-primary" style={{ width: "auto", padding: "0 18px" }}
              disabled={compare.count < 2} onClick={() => setView("compare")}>비교하기</button>
          </div>
          <LlmDebugPanel response={response} />
        </>
      )}
    </section>
  );
}
