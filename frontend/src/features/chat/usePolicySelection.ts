import { useCallback, useState } from "react";

/** S06-03: 비교 선택 체크박스 — 서버에 저장하지 않는 순수 로컬 상태. */
export function usePolicySelection() {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const toggle = useCallback((policyId: string) => {
    setSelectedIds((prev) =>
      prev.includes(policyId) ? prev.filter((id) => id !== policyId) : [...prev, policyId],
    );
  }, []);

  const clear = useCallback(() => setSelectedIds([]), []);

  return { selectedIds, toggle, clear, count: selectedIds.length };
}
