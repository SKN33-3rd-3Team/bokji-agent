"""build_index.py가 받아둔 전체 목록(법령/행정규칙/자치법규)을 공식 ID로만
중복 제거해서, 목록조회(lawSearch.do) API가 준 값을 필드 하나도 빼지 않고
그대로 JSONL로 저장한다.

본문조회(lawService.do) API는 쓰지 않는다 — 목록조회 API 응답 자체에
이미 있는 값(법령명, 소관부처, 시행일자, 공포일자, 상세링크 등)만 쓴다.

법령명 키워드, 제외어, 유형별 건수 상한은 적용하지 않는다. 이름이 같아도
공식 ID가 다르면 모두 남고, 이름이 달라도 공식 ID가 같으면 최초 레코드
하나만 남는다.

사용법:
    PYTHONPATH=src python -m rag_chatbot.collectors.law.filter_index \
        data/raw/law_index data/processed/law_filtered.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ID_FIELD_BY_TARGET = {
    "law": "법령ID",
    "admrul": "행정규칙ID",
    "ordin": "자치법규ID",
}


def load_index(index_dir: Path, target: str) -> list[dict]:
    path = index_dir / f"{target}_index.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def filter_target(items: list[dict], target: str) -> list[dict]:
    """전체 항목을 원천 ID로만 중복 제거해 원본 필드 그대로 돌려준다."""

    try:
        id_field = ID_FIELD_BY_TARGET[target]
    except KeyError as exc:
        raise ValueError(f"unsupported law target: {target!r}") from exc

    matched: list[dict] = []
    seen_ids: set[str] = set()
    for item in items:
        # 이름이 같아도 공식 ID가 다르면 서로 다른 원천이므로 모두 남긴다.
        # 반대로 이름이나 일련번호가 달라도 공식 ID가 같으면 같은 원천의
        # 중복 목록으로 보고 최초 레코드만 유지한다. ID가 없는 잘못된 원본은
        # 여기서 임의 identity를 만들지 않고 변환 단계의 검증에 맡긴다.
        raw_id = item.get(id_field)
        source_id = str(raw_id).strip() if raw_id is not None else ""
        if source_id and source_id in seen_ids:
            continue

        # 목록 API가 준 필드는 하나도 빼지 않고 그대로 담고, 우리가 계산한
        # 값만 별도 키로 덧붙인다(원본 필드와 이름 충돌 안 나게 접두어 사용).
        record = dict(item)
        record["_target"] = target
        matched.append(record)
        if source_id:
            seen_ids.add(source_id)
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
    for target in ("law", "admrul", "ordin"):
        items = load_index(index_dir, target)
        matched = filter_target(items, target)
        print(
            f"[filter_index] {target}: 전체 {len(items)}건 중 "
            f"ID 중복 제거 후 {len(matched)}건",
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
