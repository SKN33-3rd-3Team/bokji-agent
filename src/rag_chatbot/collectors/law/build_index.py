"""law.go.kr 전체 목록(법령/행정규칙/자치법규)을 받아서 로컬에 캐시한다.

본문 없이 이름·ID만 받는 거라 19만 건 전체를 받아도 몇 분이면 끝난다.
한 번 받아두면, 그 이후엔 API 호출 없이 로컬에서 원하는 만큼 정확하게
공식 ID 기준으로 중복 제거해서 쓸 수 있다 (filter_index.py가 이 캐시를 읽어서 씀).

사용법:
    PYTHONPATH=src python -m rag_chatbot.collectors.law.build_index data/raw/law_index
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from rag_chatbot.collectors.law.law import TARGET_LABEL, TARGET_ORDER, list_all

PROGRESS_EVERY = 1000  # 이 건수마다 진행상황 출력


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/raw/law_index")
    out_dir.mkdir(parents=True, exist_ok=True)

    for target in TARGET_ORDER:
        last_reported = 0

        def on_progress(done: int, total: int, _last=[0]) -> None:
            if done - _last[0] >= PROGRESS_EVERY or done == total:
                print(
                    f"[build_index] {TARGET_LABEL[target]}: {done}/{total}건",
                    file=sys.stderr,
                )
                _last[0] = done

        start = time.time()
        items = list_all(target, on_progress=on_progress)
        elapsed = time.time() - start

        out_path = out_dir / f"{target}_index.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)

        print(
            f"[build_index] {TARGET_LABEL[target]} 완료: {len(items)}건 -> "
            f"{out_path} ({elapsed:.1f}초)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
