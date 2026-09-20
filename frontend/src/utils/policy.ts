import type { PolicyView } from "@/types/chat";

/** rendering.py _BADGE_COLOR와 동일한 매핑. */
export function badgeColor(eligibilityStatus: string): "green" | "red" | "amber" {
  if (eligibilityStatus === "충족") return "green";
  if (eligibilityStatus === "미충족") return "red";
  return "amber";
}

/** rendering.py _region_label과 동일한 로직. */
export function regionLabel(policy: PolicyView): string {
  const names = policy.detail?.region_names;
  if (names && names.length > 0) return names.join(" · ");
  if (policy.detail?.region_scope === "national") return "전국";
  return "지역 미확인";
}

/** rendering.py _card_intro와 동일한 우선순위(90자 초과 시 말줄임). */
export function cardIntro(policy: PolicyView): string {
  const text = (policy.verification_note || policy.detail?.support_details || policy.detail?.purpose || "").trim();
  if (!text) return "정책 설명이 아직 확인되지 않았습니다.";
  return text.length > 90 ? `${text.slice(0, 89).trimEnd()}…` : text;
}

/** rendering.py _dup_short_note와 동일한 분기. */
export function dupShortNote(policy: PolicyView): string {
  if (policy.duplicate_conflicts && policy.duplicate_conflicts.length > 0) {
    return "다른 제도와 함께 받을 수 없습니다.";
  }
  if (policy.duplicate_clause_kind === "other") return "다른 제도와 조건부로 제한될 수 있습니다.";
  if (policy.duplicate_clause_kind === "header") return "제한 항목은 있으나 구체 조건은 문서에 없습니다.";
  if (policy.household_limit_clauses && policy.household_limit_clauses.length > 0) {
    return "가구·신청 횟수 제한이 있습니다.";
  }
  return "별도 중복수급 제한 조항이 확인되지 않았습니다.";
}

/** rendering.py 지원금액 카드 하단 안내문과 동일. */
export function amountNote(policy: PolicyView): string {
  return policy.amount_is_maximum
    ? "지급 상한 기준 금액이며, 실제 지급액은 가구 상황에 따라 다를 수 있습니다."
    : "정책 원문 기준 금액입니다.";
}

const DOC_HEADER_RE = /^(?:[□■▪◇◆]|\d{1,2}[).])\s*\S/;

/**
 * rendering.py `_document_chip_items`와 동일한 로직 — 원문에 이미 있는
 * 줄바꿈만 기준으로 나눈다(콤마 등 임의 구분자를 새로 만들어 쪼개지
 * 않는다 — "지어내지 않는다" 원칙, S07-06/S10-01 "보류" 항목 해결).
 * 한 줄에 항목이 하나씩 나열된 형태(2줄 이상)면 배열로, 줄바꿈 없는
 * 문단형이면 null을 돌려줘서 호출부가 원문 그대로 표시하게 한다.
 */
export function documentChipItems(text: string | null | undefined): string[] | null {
  if (!text) return null;
  const lines = text
    .split("\n")
    .map((line) => line.trim().replace(/^[ \-•·○\t]+/, "").replace(/[ \-•·○\t]+$/, ""))
    .filter(Boolean);
  if (lines.length < 2) return null;
  return lines.slice(0, 12);
}

/** rendering.py `_is_document_header_line`과 동일 — "1) 세대원 변경..." 같은
 * 상위 구분 줄은 칩이 아니라 굵은 구분 텍스트로 그린다. */
export function isDocumentHeaderLine(text: string): boolean {
  return DOC_HEADER_RE.test(text.trim());
}
