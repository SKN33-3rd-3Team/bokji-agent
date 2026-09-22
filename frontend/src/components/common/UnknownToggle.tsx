interface UnknownToggleProps {
  active: boolean;
  onToggle: () => void;
  disabled?: boolean;
  /** 기본 "모름". 선택지 성격에 따라 "모름 / 해당 없음" 등으로 바꿀 수 있다. */
  label?: string;
  /** 스크린리더용 — 어느 항목의 "모름"인지 알 수 있게 한다. */
  fieldLabel?: string;
}

/**
 * 되묻기 폼 공통 "모름 / 해당 없음" 토글.
 *
 * 왜 select의 option이나 radio 한 칸이 아니라 별도 토글인가: 되묻는 항목이
 * select·radio·숫자 입력·자유 입력까지 섞여 있어서(숫자 입력에는 "모름"
 * option을 넣을 자리가 없다), 위젯 종류마다 다른 방식으로 넣으면 같은 폼
 * 안에서 "모름"을 찾는 위치가 매번 달라진다. 항목 라벨 옆 한 자리로 통일한다.
 *
 * 또 하나: 열거형 중에는 "해당 없음"이 **실제 답**인 것이 있다(임신/출산
 * 상태의 ``none``, 보훈대상자 여부의 ``not_registered``). 그 옵션 목록에
 * "모름"을 같이 끼워 넣으면 "해당 없음"과 "모름"이 나란히 놓여 서로 다른
 * 뜻인지 구분이 안 된다 - 토글로 빼면 "답을 고르거나 / 모른다고 넘기거나"로
 * 층이 나뉜다.
 */
export function UnknownToggle({
  active,
  onToggle,
  disabled = false,
  label = "모름",
  fieldLabel,
}: UnknownToggleProps) {
  return (
    <button
      type="button"
      className={`unknown-toggle${active ? " on" : ""}`}
      aria-pressed={active}
      aria-label={fieldLabel ? `${fieldLabel}: ${label}` : label}
      disabled={disabled}
      onClick={onToggle}
    >
      {active ? `${label} ✓` : label}
    </button>
  );
}
