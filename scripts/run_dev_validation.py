"""Run the frozen Dev questions through the public chatbot service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_design.validation_runner import calculate_summary, load_questions, run_questions, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description="고정 Dev 질문 자동 검증 및 지표 보고서 생성")
    parser.add_argument("--questions", type=Path, default=ROOT / "data/evaluation/dev_questions.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/evaluation/dev")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        parser.error("--top-k must be between 1 and 20")

    try:
        from src.rag_chatbot.service import ask
    except ModuleNotFoundError as exc:
        parser.error(
            f"프로젝트 실행 의존성이 없습니다: {exc.name}. "
            "프로젝트 가상환경에서 requirements-graph.txt와 "
            "requirements-vector.txt를 설치한 뒤 다시 실행하세요."
        )

    questions = load_questions(args.questions)
    records = run_questions(questions, ask, top_k=args.top_k)
    summary = calculate_summary(records, top_k=args.top_k)
    write_report(args.output_dir, records, summary, question_path=args.questions, top_k=args.top_k)
    print(f"검증 완료: {len(records)}건")
    print(f"보고서: {args.output_dir / 'report.md'}")
    print(f"그래프: {args.output_dir / 'metrics.svg'}")
    return 1 if summary["operations"]["error_rate"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
