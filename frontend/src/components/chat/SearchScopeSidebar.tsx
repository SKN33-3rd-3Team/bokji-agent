import { PillMultiSelect } from "@/components/common/PillMultiSelect";
import { useSearchOptions } from "@/features/config/useSearchOptions";
import {
  FALLBACK_DEFAULT_TOP_K,
  FALLBACK_INTEREST_FIELD_OPTIONS,
  FALLBACK_SIDEBAR_INTEREST_OPTIONS,
} from "@/constants/labels";

const TOP_K_MIN = 1;
const TOP_K_MAX = 20;

interface SearchScopeSidebarProps {
  supportConditions: string[];
  onSupportConditionsChange: (next: string[]) => void;
  interestFields: string[];
  onInterestFieldsChange: (next: string[]) => void;
  topK: number;
  onTopKChange: (next: number) => void;
}

/**
 * S-04 사이드바 · 검색 범위 조정 — S04-01(지원조건) + S04-02(관심 분야)는
 * 서로 다른 UI 그룹이지만 둘 다 같은 extra_interests 배열로 합쳐져
 * API-10에 전달된다(부모인 Sidebar가 두 선택값을 합산해서 보낸다).
 */
export function SearchScopeSidebar({
  supportConditions,
  onSupportConditionsChange,
  interestFields,
  onInterestFieldsChange,
  topK,
  onTopKChange,
}: SearchScopeSidebarProps) {
  const { data: options } = useSearchOptions();
  const sidebarInterestOptions = options?.sidebar_interest_options ?? FALLBACK_SIDEBAR_INTEREST_OPTIONS;
  const interestFieldOptions = options?.interest_field_options ?? FALLBACK_INTEREST_FIELD_OPTIONS;
  const defaultTopK = options?.default_top_k ?? FALLBACK_DEFAULT_TOP_K;

  return (
    <>
      <div className="sb-field">
        <label>지원조건</label>
        <PillMultiSelect
          options={sidebarInterestOptions}
          selected={supportConditions}
          onChange={onSupportConditionsChange}
        />
      </div>
      <div className="sb-field">
        <label>관심 분야</label>
        <PillMultiSelect
          options={interestFieldOptions}
          selected={interestFields}
          onChange={onInterestFieldsChange}
        />
      </div>
      <div className="sb-field">
        <label>정책 후보 수 — {topK}</label>
        <input
          className="sb-slider-input"
          type="range"
          min={TOP_K_MIN}
          max={TOP_K_MAX}
          value={topK || defaultTopK}
          onChange={(e) => onTopKChange(Number(e.target.value))}
        />
      </div>
    </>
  );
}
