"""보조금24 3개 결과 파일(list/detail/conditions)을 서비스ID 기준으로 병합한다.

사용법:
    python -m rag_chatbot.collectors.gov_24.merge_gov24
    python -m rag_chatbot.collectors.gov_24.merge_gov24 50
"""

import json
import sys
from pathlib import Path

from .gov24 import CONDITIONS_OUT, DETAIL_OUT, LIST_OUT

# main_gov24.py를 어느 위치(gov_24/ 안, 리포지토리 루트 등)에서 실행하든
# 항상 같은 data/ 폴더에 저장/조회하도록, 이 파일 위치를 기준으로 프로젝트
# 루트를 고정 경로로 잡는다(현재 작업 디렉터리(cwd)에 의존하지 않음).
PROJECT_ROOT = Path(__file__).resolve().parents[4]
MERGED_OUT = str(PROJECT_ROOT / "data" / "raw" / "gov24_merged.json")


def load(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def merge(limit: int | None = None) -> list[dict]:
    """service_list, detail, conditions 결과를 서비스ID 기준으로 병합한다.

    limit을 주면 목록조회 순서 기준 앞에서부터 그만큼만 병합한다 —
    gov24.run_detail(limit)/run_conditions(limit)이 같은 순서·같은 개수만큼만
    조회하므로, 여기서도 같은 limit을 줘야 "일부만 조회했는데 나머지는 원천
    결측으로 잘못 표시되는" 문제가 생기지 않는다.
    """
    service_list = load(LIST_OUT)
    if limit:
        service_list = service_list[:limit]

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


def run(limit: int | None = None) -> list[dict]:
    merged = merge(limit)
    Path(MERGED_OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(MERGED_OUT).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"병합 완료: {MERGED_OUT} (총 {len(merged)}건)", flush=True)
    return merged


if __name__ == "__main__":
    _limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(_limit)
