"""build_index.py가 받아둔 전체 목록(법령/행정규칙/자치법규)을 로컬에서
키워드로 필터링해서, 목록조회(lawSearch.do) API가 준 값을 필드 하나도
빼지 않고 그대로 JSONL로 저장한다.

본문조회(lawService.do) API는 쓰지 않는다 — 목록조회 API 응답 자체에
이미 있는 값(법령명, 소관부처, 시행일자, 공포일자, 상세링크 등)만 쓴다.

API로 키워드 검색을 하면(generate_law_targets.py 방식) 결과가 잘리거나
("기본법" 205건 중 50건만) API 검색 로직에 안 걸리는 이름을 놓칠 수 있다
("에너지이용 합리화법"이 그 예). 이 방식은 전체 목록을 이미 다 갖고
있는 상태에서 로컬 문자열 매칭만 하는 거라 그런 누락이 없다 — 그리고
API 호출이 하나도 안 들어서 키워드를 얼마든지 추가해서 다시 돌려봐도
비용이 안 든다.

자치법규(ordin)는 전국 지자체마다 비슷한 조례가 반복돼서 건수가 폭증하니
(예: "복지" 한 단어로 5,789건), ORDIN_CAP으로 상한을 둔다.

사용법:
    PYTHONPATH=src python -m rag_chatbot.collectors.law.filter_index \
        data/raw/law_index data/processed/law_filtered.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 복지 도메인 키워드. 로컬 필터링이라 비용 없이 계속 추가/조정 가능하다.
# generate_law_targets.py의 SUFFIX_KEYWORDS/NAMED_LAWS보다 훨씬 넓게 잡았다
# (에너지이용 합리화법처럼 "복지법/보장법/기본법" 접미어로는 안 걸리던 것들
#  포함).
KEYWORDS = [
    # "지원"은 뺐다 — 복지 무관 문서(재난 특별법, 올림픽 지원법 등)에도
    # 워낙 흔하게 들어가는 범용 단어라 혼자서 1,236/2,606건(47%)을 잘못
    # 걸러냈다. 대신 복지 맥락에서 실제로 쓰이는 "OO 지원" 복합어로 좁힌다.
    "복지", "보장", "급여", "돌봄", "장려금", "수당",
    "기초생활", "장애인", "노인", "아동", "청소년", "청년", "한부모",
    "다문화", "임신", "출산", "보육", "취업", "구직", "고용보험",
    "국민연금", "기초연금", "산재", "건강보험", "의료급여",
    "에너지이용", "에너지바우처", "주거급여", "긴급복지",
    "국가유공자", "보훈", "농어민", "농업인", "재해구호",
    "저소득층 지원", "취약계층 지원", "복지 지원", "생계 지원",
]

# "수당"/"급여"는 공무원·군인·국회의원 등 인건비성 수당 규정에도 흔히
# 쓰여서(예: "군인 명예전역수당 지급 규정"), 이런 기관·직역 단어가 제목에
# 같이 있으면 매칭에서 제외한다.
EXCLUDE_TERMS = [
    "공무원", "군인", "군무원", "국회의원", "법원", "검사", "판사",
    "경찰공무원", "지방의회", "교원", "교육공무원", "외무공무원",
]

ORDIN_CAP = 300  # 자치법규는 전국 반복 구조라 상한을 둔다


def load_index(index_dir: Path, target: str) -> list[dict]:
    path = index_dir / f"{target}_index.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def name_of(item: dict) -> str | None:
    return item.get("법령명한글") or item.get("행정규칙명") or item.get("자치법규명")


def filter_target(items: list[dict], target: str, cap: int | None) -> list[dict]:
    """매칭되는 항목을 원본 필드 그대로(하나도 안 버리고) 리스트로 돌려준다."""

    matched: list[dict] = []
    seen_names: set[str] = set()
    for item in items:
        name = name_of(item)
        if not name or name in seen_names:
            continue
        hit_keywords = [kw for kw in KEYWORDS if kw in name]
        if not hit_keywords:
            continue
        if any(term in name for term in EXCLUDE_TERMS):
            continue

        # 목록 API가 준 필드는 하나도 빼지 않고 그대로 담고, 우리가 계산한
        # 값만 별도 키로 덧붙인다(원본 필드와 이름 충돌 안 나게 접두어 사용).
        record = dict(item)
        record["_target"] = target
        record["_matched_keywords"] = hit_keywords
        matched.append(record)
        seen_names.add(name)
        if cap is not None and len(matched) >= cap:
            break
    return matched


def main() -> None:
    if len(sys.argv) != 3:
        print(
            "usage: python -m rag_chatbot.collectors.law.filter_index "
            "<index_dir> <output.jsonl>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    index_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    all_matched: list[dict] = []
    for target, cap in (("law", None), ("admrul", None), ("ordin", ORDIN_CAP)):
        items = load_index(index_dir, target)
        matched = filter_target(items, target, cap)
        print(
            f"[filter_index] {target}: 전체 {len(items)}건 중 {len(matched)}건 매칭",
            file=sys.stderr,
        )
        all_matched.extend(matched)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in all_matched:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[filter_index] 합계 {len(all_matched)}건 -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
