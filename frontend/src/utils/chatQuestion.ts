import type { ChatResponse } from "@/types/chat";

/** 슬롯 위젯이 라벨을 이미 보여주므로, 백엔드가 붙이는 "1. OO가 어디신가요?" 같은
 * 번호 목록 줄은 되묻기 말풍선에서는 걸러내고 안내 문장만 남긴다. */
export function stripNumberedSlotList(text: string): string {
  return text
    .split("\n")
    .filter((line) => !/^\d+\.\s/.test(line.trim()))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** 되묻기 응답을 어떤 폼으로 그릴지 — "none"이면 폼 없이 말풍선 + 자유 입력. */
export type FollowupKind = "slots" | "conflict" | "calc" | "none";

/**
 * needs_input 응답 하나가 어느 되묻기인지 구분한다.
 *
 * 그래프가 멈추는 지점은 셋이다:
 *  - N3(request_missing_slots): 자격 판정 전 하드 게이트 슬롯 -> missing_slots
 *  - N1의 프로필/대화 충돌 재확인 -> slot_conflicts
 *  - N10a(request_calc_info): 금액 계산용 소프트 슬롯/선택형 -> calc_slot_inputs,
 *    calc_missing_choices
 *
 * 예전에는 status === "needs_input"만 보고 무조건 슬롯 폼을 그렸다. 그래서 계산
 * 되묻기가 오면 missing_slots가 빈 배열이라 필드 0개짜리 폼이 뜨고, 제출 버튼은
 * 영영 비활성인 채 자유 입력창도 숨겨져(되묻기 중에는 감춘다) 답을 보낼 방법이
 * 아예 없었다. 종류를 구분해 각각의 폼으로 보낸다.
 *
 * 셋 다 아닌 needs_input(계약상 나오지 않아야 하지만 방어)에는 "none"을 돌려
 * 말풍선과 자유 입력창을 그대로 남긴다 — 막다른 화면을 만들지 않기 위함이다.
 */
export function followupKindOf(response: ChatResponse | null | undefined): FollowupKind {
  if (!response || response.status !== "needs_input") return "none";
  if (response.calc_slot_inputs.length > 0 || response.calc_missing_choices.length > 0) {
    return "calc";
  }
  if (response.slot_conflicts && Object.keys(response.slot_conflicts).length > 0) {
    return "conflict";
  }
  if (response.missing_slots.length > 0) return "slots";
  return "none";
}
