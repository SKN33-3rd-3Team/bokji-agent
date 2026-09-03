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
    parser.add_argument("--workers", type=int, default=4, help="병렬 질문 실행 수(1~32, 기본 4)")
    parser.add_argument(
        "--max-turns", type=int, default=4, help="질문별 최대 서비스 호출 수(1~32, 기본 4)"
    )
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        parser.error("--top-k must be between 1 and 20")
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    if not 1 <= args.max_turns <= 32:
        parser.error("--max-turns must be between 1 and 32")

    try:
        from src.rag_chatbot.service import answer_followup, ask, get_graph
    except ModuleNotFoundError as exc:
        parser.error(
            f"프로젝트 실행 의존성이 없습니다: {exc.name}. "
            "프로젝트 가상환경에서 requirements-graph.txt와 "
            "requirements-vector.txt를 설치한 뒤 다시 실행하세요."
        )

    # get_graph()/get_store()의 지연 초기화는 프로세스 전역 cache를 사용한다.
    # 여러 worker가 첫 요청에서 동시에 초기화하지 않도록 한 번 직렬로 준비한다.
    get_graph()
    questions = load_questions(args.questions)
    records = run_questions(
        questions,
        ask,
        answer_followup,
        top_k=args.top_k,
        workers=args.workers,
        max_turns=args.max_turns,
    )
    summary = calculate_summary(records, top_k=args.top_k)
    write_report(
        args.output_dir,
        records,
        summary,
        question_path=args.questions,
        top_k=args.top_k,
        workers=args.workers,
        max_turns=args.max_turns,
    )
    if summary["quality_metrics_valid"]:
        print(f"검증 완료: {len(records)}건, 품질 지표 게시 가능")
    else:
        failed = summary["conversation"]["terminal_status_counts"].get("failed", 0)
        print(
            f"검증 실패: {failed}건이 terminal answered 상태에 도달하지 못해 "
            "품질 지표를 게시할 수 없습니다.",
            file=sys.stderr,
        )
    print(f"보고서: {args.output_dir / 'report.md'}")
    print(f"그래프: {args.output_dir / 'metrics.svg'}")
    return 0 if summary["quality_metrics_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
