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

/**
 * cardIntro/cardTagline 공용 원문 소스.
 *
 * 실제 정책 설명(support_details/purpose)을 최우선으로 쓴다 - 이게 없을
 * 때만 검증 문장(verification_note)으로 대신한다. verification_note를
 * 우선하면 거의 모든 정책에서 "이 정책이 뭔지"는 한 번도 안 보이고 검증
 * 상태 문장만 보이는데다(대부분의 정책에 확인/미확인 조건이 있으므로),
 * 그 내용은 바로 아래 "지원자격" 칩으로 이미 따로 보여주고 있어 중복이다.
 */
function _policySummarySource(policy: PolicyView): string {
  return (policy.detail?.support_details || policy.detail?.purpose || policy.verification_note || "").trim();
}

/** rendering.py _card_intro와 동일한 우선순위(90자 초과 시 말줄임). */
export function cardIntro(policy: PolicyView): string {
  const text = _policySummarySource(policy);
  if (!text) return "정책 설명이 아직 확인되지 않았습니다.";
  return text.length > 90 ? `${text.slice(0, 89).trimEnd()}…` : text;
}

// 정책 원문이 "ㅇ 인턴형 일경험: ... ㅇ 프로젝트형 일경험: ..."처럼 불릿
// 목록이거나 "지급 * 단, 고소득 가구는 제외" 처럼 "*"로 단서를 다는
// 경우가 있다 - 문장부호가 아니라 이 마커들로 항목이 나뉜다.
const _BULLET_RE = /(?:^|\s)[ㅇ○◦•▪●∙□▶※*]\s*/g;

/**
 * 목록 카드 전용 — cardIntro와 같은 소스를 쓰되 "~을 지원하는 제도입니다"
 * 처럼 짧게 보여준다. 상세 화면(cardIntro, 최대 90자)은 원문 전체를 그대로
 * 자르는 반면, 카드는 support_details가 여러 문장/여러 불릿짜리 문단일 때
 * 중간에서 잘려 난잡해 보인다는 피드백이 있어 첫 항목 경계에서 우선 끊는다.
 */
function _firstSentence(text: string): string {
  return (text.match(/^[^.!?]*[.!?]/)?.[0] ?? text).trim();
}

export function cardTagline(policy: PolicyView): string {
  const text = _policySummarySource(policy);
  if (!text) return "정책 설명이 아직 확인되지 않았습니다.";

  const bulletMatches = [...text.matchAll(_BULLET_RE)];
  let candidate: string;
  if (bulletMatches.length > 0) {
    const first = bulletMatches[0];
    const leading = text.slice(0, first.index).trim();
    if (leading) {
      // 불릿 앞에 이미 온전한 설명 문장이 있으면(예: "만 19~34세 청년을
      // 대상으로 한 지원사업입니다. ㅇ 인턴형...") 그 문장이 불릿 하위
      // 항목 하나보다 더 나은 한 줄 요약이므로 우선한다.
      candidate = _firstSentence(leading);
    } else {
      // 텍스트가 불릿으로 곧장 시작하면 첫 항목만 뗀다 - 마커 이후 ~
      // 다음 마커 전(또는 끝)까지.
      const start = first.index + first[0].length;
      const end = bulletMatches.length > 1 ? bulletMatches[1].index : text.length;
      candidate = text.slice(start, end).trim();
    }
  } else {
    candidate = _firstSentence(text);
  }
  if (!candidate) candidate = text;

  const CARD_MAX = 60;
  return candidate.length > CARD_MAX
    ? `${candidate.slice(0, CARD_MAX - 1).trimEnd()}…`
    : candidate;
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
