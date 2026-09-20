"""S07-06/S10-01(구비서류 칩 UI, 2026-09-16 옵션 ② 확정) - `required_documents`/
`required_documents_official`/`required_documents_self` 원문 문자열을 항목
배열로 구조화한다.

`src/rag_chatbot/service.py`의 `PolicyDetail`은 이 세 필드를 배열이 아니라
원문 문자열 1개(``str | None``)로만 제공한다(계약 유지, 수정하지 않음) -
이 모듈은 그 문자열을 **백엔드 응답에서만** 파생 필드(`*_items: list[str]`)로
추가로 구조화한다.

'지어내지 않는다' 원칙(PROJECT_COMPLIANCE.md, graph/nodes/benefit_calculator.py
의 "모호하면 추측하지 않고 원문/None을 그대로 둔다" 규율과 동일)을 지키기
위해, 원문이 **이미 시각적으로 표현하고 있는 구분**(줄바꿈+글머리표, 최상위
쉼표)만 인식해서 나눈다 - 새로운 의미나 경계를 만들어내지 않는다. 구분자를
찾지 못하면(줄바꿈도 없고 쉼표도 없는 산문 한 줄) 원문 전체를 항목 1개로
그대로 돌려준다.

실제 데이터 관찰(data/samples/subsidy_documents_sample.jsonl, 실사용 5건,
2026-09-16 확인):
- "해당없음"/"해당 없음"은 값 없음을 뜻하는 자리표시자(96.8%/93.3%의 실제
  레코드에서 이 값) - None으로 취급한다.
- 줄바꿈 + "-"/"○" 글머리표: 각 줄이 원문 자체가 이미 나눈 개별 항목이다
  (내용이 서류명이 아니라 조건문/법령 인용인 줄도 있지만, 그것도 원문이
  이미 별개 항목으로 나눈 것이므로 그대로 존중한다 - 의미를 판단해서
  걸러내지 않는다).
- 줄바꿈이 없는 한 줄인데 쉼표로 나열된 경우("주민등록등본, 신분증, ...")는
  쉼표로 분리한다. 단 괄호 안의 쉼표("어업허가 일건서류(어업허가증,
  어선검사증 등)")는 괄호 안 하위 항목이라 최상위 분리 대상이 아니다.
- 위 두 규칙 모두 해당 안 되면(구분자 없는 산문 한 줄) 쪼개지 않는다.
"""

from __future__ import annotations

import re

_NOT_APPLICABLE = frozenset({"해당없음", "해당 없음", "-"})
_BULLET_PREFIX = re.compile(r"^[-○•]\s*")  # -, ○(원문자), •


def split_document_items(raw: str | None) -> list[str] | None:
    """원문 문자열을 항목 배열로 구조화한다. 값이 없으면 ``None``."""

    if raw is None:
        return None
    text = raw.strip()
    if not text or text in _NOT_APPLICABLE:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        return [_BULLET_PREFIX.sub("", line).strip() for line in lines]

    # 줄이 하나뿐이어도 글머리표가 붙어 있으면(예: "- 주민등록등본"만 단독으로
    # 온 경우) 여러 줄일 때와 똑같이 제거한다 - 안 그러면 이 한 줄짜리
    # 경로만 "-"가 항목 텍스트에 그대로 남는 불일치가 생긴다(쉼표 분리
    # 결과의 첫 항목에도 동일하게 적용됨).
    single_line = _BULLET_PREFIX.sub("", lines[0]).strip() if lines else text

    segments = _split_top_level_commas(single_line)
    if len(segments) >= 2:
        return segments
    return [single_line]


def _split_top_level_commas(text: str) -> list[str]:
    """괄호 안 쉼표는 무시하고 최상위(괄호 밖) 쉼표에서만 나눈다."""

    segments: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            segments.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return [segment for segment in segments if segment]
