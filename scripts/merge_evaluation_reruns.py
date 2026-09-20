"""기준 실행의 results_*.jsonl에서 일부 문항을 재실행 결과로 바꿔 지표를 다시 계산한다.

예: LLM 호출이 실패했던 문항만 고친 코드로 다시 돌린 뒤, 나머지 문항은 기준
실행 그대로 두고 합쳐서 "이 문항들만 고쳤을 때"의 전체 지표를 본다.

점수를 새로 정의하지 않는다 - `rag_design.validation_runner.calculate_summary`와
`rag_design.answer_quality.summarize_answer_quality`를 그대로 호출한다.

실행:
    python scripts/merge_evaluation_reruns.py \
        --base a_dev.jsonl --rerun e_dev.jsonl --name dev150 [--output merged.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_design.answer_quality import AnswerQualityRecord, Judgment, summarize_answer_quality
from rag_design.validation_runner import calculate_summary


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _quality_record(row: dict) -> AnswerQualityRecord | None:
    quality = row.get("answer_quality")
    if not quality:
        return None
    return AnswerQualityRecord(
        question_id=row["question_id"],
        faithfulness=Judgment(**quality["faithfulness"]),
        relevancy=Judgment(**quality["relevancy"]),
    )


def merge(base: list[dict], rerun: list[dict], *, top_k: int) -> dict:
    rerun_by_id = {row["question_id"]: row for row in rerun}
    unknown = set(rerun_by_id) - {row["question_id"] for row in base}
    if unknown:
        raise SystemExit(f"기준 실행에 없는 문항이 재실행에 있습니다: {sorted(unknown)}")
    merged = [rerun_by_id.get(row["question_id"], row) for row in base]

    quality = [q for q in (_quality_record(row) for row in merged) if q is not None]
    calls = sum((row.get("llm_status") or {}).get("calls", 0) for row in merged)
    failures = sum((row.get("llm_status") or {}).get("failures", 0) for row in merged)
    return {
        "question_count": len(merged),
        "replaced_question_count": len(rerun_by_id),
        "summary": calculate_summary(merged, top_k=top_k),
        "answer_quality": summarize_answer_quality(quality) if quality else None,
        "llm": {"calls": calls, "failures": failures},
        "merged_records": merged,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--rerun", type=Path, required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = merge(_load(args.base), _load(args.rerun), top_k=args.top_k)
    printable = {k: v for k, v in result.items() if k != "merged_records"}
    print(json.dumps({"name": args.name, **printable}, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"name": args.name, **result}, ensure_ascii=False), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
