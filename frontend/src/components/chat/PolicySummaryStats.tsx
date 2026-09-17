import type { PolicyView } from "@/types/chat";

interface PolicySummaryStatsProps {
  policies: PolicyView[];
}

/** S06-01: policies 배열을 프론트에서 직접 집계해 타일 3개를 그린다(별도 통계 API 없음). */
export function PolicySummaryStats({ policies }: PolicySummaryStatsProps) {
  const total = policies.length;
  const fulfilled = policies.filter((p) => p.eligibility_status === "충족").length;
  const uncertainOrUnmet = total - fulfilled;

  const tiles = [
    { label: "확인한 제도", value: total, color: "var(--text)" },
    { label: "자격 충족", value: fulfilled, color: "var(--green-text)" },
    { label: "미충족 · 미확인", value: uncertainOrUnmet, color: "var(--amber-text)" },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 18 }}>
      {tiles.map((tile) => (
        <div key={tile.label} className="card" style={{ padding: "14px 16px" }}>
          <div style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600, marginBottom: 4 }}>
            {tile.label}
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: tile.color }}>{tile.value}건</div>
        </div>
      ))}
    </div>
  );
}
