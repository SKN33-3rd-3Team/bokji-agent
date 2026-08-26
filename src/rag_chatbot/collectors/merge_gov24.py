"""보조금24 3개 결과 파일(list/detail/conditions)을 서비스ID 기준으로 병합한다.

사용법:
    python -m rag_chatbot.collectors.merge_gov24
"""

import json
from pathlib import Path

from rag_chatbot.collectors.gov24 import CONDITIONS_OUT, DETAIL_OUT, LIST_OUT

MERGED_OUT = "data/raw/gov24_merged.json"


def load(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def merge() -> list[dict]:
    service_list = load(LIST_OUT)
    detail_items = load(DETAIL_OUT)
    condition_items = load(CONDITIONS_OUT)

    # detail/conditions는 {"data": [...]} 형태로 감싸져 있을 수 있어서 풀어준다.
    def unwrap(item: dict) -> dict:
        inner = item.get("data")
        if isinstance(inner, list) and inner:
            return inner[0]
        if isinstance(inner, dict):
            return inner
        return item

    detail_by_id = {unwrap(d)["서비스ID"]: unwrap(d) for d in detail_items if unwrap(d).get("서비스ID")}
    cond_by_id = {unwrap(c)["서비스ID"]: unwrap(c) for c in condition_items if unwrap(c).get("서비스ID")}

    merged = []
    for item in service_list:
        service_id = item.get("서비스ID")
        combined = {
            **item,
            **detail_by_id.get(service_id, {}),
            **cond_by_id.get(service_id, {}),
        }
        merged.append(combined)

    return merged


if __name__ == "__main__":
    merged = merge()
    Path(MERGED_OUT).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"병합 완료: {MERGED_OUT} (총 {len(merged)}건)")
