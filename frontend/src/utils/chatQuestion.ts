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
