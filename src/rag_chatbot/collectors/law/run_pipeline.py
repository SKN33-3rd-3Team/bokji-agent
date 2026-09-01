"""법령/행정규칙/자치법규 파이프라인을 순서대로 한 번에 실행한다.

    1. build_index  - 전체 목록(이름+ID, 본문 없음) 캐싱 (API 호출, 오래 걸림)
    2. filter_index - 공식 ID 기준 중복 제거 (API 호출 없음, 빠름)
    3. filtered_to_document - rag_design.contracts.Document 스키마로 변환

본문(lawService.do) API는 쓰지 않는다 — 목록조회 API 필드만 쓴다(2번 단계
결과 그대로 metadata에 담김). 1번은 캐시가 이미 있으면 --skip-index로
건너뛸 수 있다. 3번은 rag_design 모듈이 아직 main에 없으면(PR #2 merge
전) 자동으로 건너뛰고 안내만 출력한다.

이 파일은 -m 없이 직접 실행해도 되게 만들어뒀다:
    python run_pipeline.py
    python run_pipeline.py --skip-index          # 이미 캐싱된 목록 재사용
    python run_pipeline.py --skip-index --skip-document

물론 -m 방식으로 저장소 루트에서 실행해도 된다:
    PYTHONPATH=src python -m rag_chatbot.collectors.law.run_pipeline --skip-index
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# python run_pipeline.py 처럼 -m 없이 직접 실행하면 "rag_chatbot" 패키지가
# 기본적으로 안 보인다 (스크립트 자기 폴더만 sys.path에 잡히기 때문). 그래서
# src/ 폴더를 직접 sys.path에 넣어준다.
_THIS_FILE = Path(__file__).resolve()
# .../src/rag_chatbot/collectors/law/run_pipeline.py -> parents[3] == src
_SRC_DIR = _THIS_FILE.parents[3]
_REPO_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from rag_chatbot.collectors.law.build_index import PROGRESS_EVERY
from rag_chatbot.collectors.law.law import TARGET_LABEL, TARGET_ORDER, list_all
from rag_chatbot.collectors.law.filter_index import filter_target, load_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="법령/행정규칙/자치법규 파이프라인 (목록 인덱싱 -> ID 중복 제거 -> Document 변환)"
    )
    parser.add_argument(
        "--index-dir", default=str(_REPO_ROOT / "data/raw/law_index")
    )
    parser.add_argument(
        "--filtered-out", default=str(_REPO_ROOT / "data/processed/law_filtered.jsonl")
    )
    parser.add_argument(
        "--jsonl-out", default=str(_REPO_ROOT / "data/processed/law_documents.jsonl")
    )
    parser.add_argument(
        "--manifest-out", default=str(_REPO_ROOT / "data/processed/law_manifest.json")
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="1단계 건너뛰기 (이미 캐싱된 --index-dir 재사용)",
    )
    parser.add_argument(
        "--skip-document",
        action="store_true",
        help="3단계(Document 변환)를 건너뛴다 (rag_design 모듈이 없을 때 등)",
    )
    return parser.parse_args()


def step1_build_index(index_dir: Path) -> None:
    print("=== 1/3 전체 목록 인덱싱 ===", file=sys.stderr)
    index_dir.mkdir(parents=True, exist_ok=True)

    for target in TARGET_ORDER:
        def on_progress(done: int, total: int, _last=[0]) -> None:
            if done - _last[0] >= PROGRESS_EVERY or done == total:
                print(f"[run_pipeline] {TARGET_LABEL[target]}: {done}/{total}건", file=sys.stderr)
                _last[0] = done

        items = list_all(target, on_progress=on_progress)
        out_path = index_dir / f"{target}_index.json"
        import json

        with out_path.open("w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        print(f"[run_pipeline] {TARGET_LABEL[target]} {len(items)}건 -> {out_path}", file=sys.stderr)


def step2_filter(index_dir: Path, out_path: Path) -> int:
    print("=== 2/3 공식 ID 기준 중복 제거 ===", file=sys.stderr)
    import json

    all_matched: list[dict] = []
    for target in ("law", "admrul", "ordin"):
        items = load_index(index_dir, target)
        matched = filter_target(items, target)
        print(
            f"[run_pipeline] {target}: 전체 {len(items)}건 중 "
            f"ID 중복 제거 후 {len(matched)}건",
            file=sys.stderr,
        )
        all_matched.extend(matched)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in all_matched:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[run_pipeline] 합계 {len(all_matched)}건 -> {out_path}", file=sys.stderr)
    return len(all_matched)


def step3_convert_to_document(filtered_path: Path, out_jsonl: Path, out_manifest: Path) -> None:
    print("=== 3/3 Document 변환 ===", file=sys.stderr)
    try:
        from rag_chatbot.collectors.law.filtered_to_document import convert_all, write_outputs
    except ModuleNotFoundError as exc:
        print(
            f"[run_pipeline] rag_design 모듈을 찾을 수 없어 3단계를 건너뜁니다 ({exc}). "
            "PR #2(RAG설계)가 main에 merge된 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        return

    documents, warnings = convert_all(filtered_path)
    write_outputs(documents, warnings, out_jsonl, out_manifest)


def main() -> None:
    args = parse_args()
    index_dir = Path(args.index_dir)
    filtered_path = Path(args.filtered_out)

    if args.skip_index:
        print("[run_pipeline] --skip-index 지정됨 -> 1단계 건너뜀 (기존 캐시 사용)", file=sys.stderr)
    else:
        step1_build_index(index_dir)

    step2_filter(index_dir, filtered_path)

    if args.skip_document:
        print("[run_pipeline] --skip-document 지정됨 -> 3단계 건너뜀", file=sys.stderr)
        return

    step3_convert_to_document(filtered_path, Path(args.jsonl_out), Path(args.manifest_out))


if __name__ == "__main__":
    main()
