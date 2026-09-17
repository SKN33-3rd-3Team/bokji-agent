import { documentChipItems, isDocumentHeaderLine } from "@/utils/policy";

interface DocumentChipListProps {
  text: string | null | undefined;
}

/**
 * S07-06/S10-01: 구비서류 원문을 칩으로 쪼갤 수 있으면(줄바꿈으로 항목이
 * 나열된 형태) 칩으로, 아니면 원문 문단 그대로 보여준다. 파싱 규칙은
 * streamlit_ui/rendering.py의 기존 로직을 그대로 포팅한 것(utils/policy.ts
 * documentChipItems) — 콤마 등으로 새로 쪼개지 않고 원문의 줄바꿈만 쓴다.
 */
export function DocumentChipList({ text }: DocumentChipListProps) {
  const items = documentChipItems(text);

  if (!items) {
    return text ? (
      <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap", margin: 0 }}>{text}</p>
    ) : null;
  }

  // .chip이 이미 inline-flex + margin으로 자연스럽게 줄바꿈되므로(related_law
  // 칩과 동일 패턴), 여기서 별도 flex 컨테이너를 씌우지 않는다 — 씌우면 간격이 이중으로 붙는다.
  return (
    <div style={{ marginTop: 2 }}>
      {items.map((item, i) =>
        isDocumentHeaderLine(item) ? (
          <div key={i} style={{ fontSize: 12.5, fontWeight: 700, marginTop: i === 0 ? 0 : 6, marginBottom: 4 }}>
            {item}
          </div>
        ) : (
          <span key={i} className="chip">
            {item}
          </span>
        ),
      )}
    </div>
  );
}
