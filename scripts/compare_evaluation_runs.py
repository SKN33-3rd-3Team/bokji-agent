"""두 `run_model_evaluation.py` 실행의 summary.json을 비교해 Before/After
리포트를 만든다.

왜 필요한가
-----------
"한 조건만 바꿔서 Baseline과 비교한다"(docs/PROJECT_COMPLIANCE.md 평가 규칙)를
매번 손으로 숫자를 옮겨적지 않고 재현 가능하게 하기 위함이다. 두
summary_<model>_<date>.json 경로만 주면 검색·인용·보류·운영 지표를 표로
뽑는다. Faithfulness/Answer relevancy는 두 실행 모두 judge가 돌았을 때만
표에 넣는다 - 한쪽이라도 건너뛰었으면(judged_count=0) "0.000"처럼 있어
보이는 숫자를 만들지 않고 "N/A(미측정)"라고 밝힌다.

이 스크립트는 점수를 계산하지 않는다 - 이미 `run_model_evaluation.py`가
계산해서 저장한 summary.json 두 개를 읽어 나란히 놓을 뿐이다.

실행:
    python scripts/compare_evaluation_runs.py <before_summary.json> <after_summary.json>
    python scripts/compare_evaluation_runs.py <before_summary.json> <after_summary.json> \
        --output artifacts/evaluation/citation-fix-compare/comparison_report.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _delta(before, after, digits: int = 3) -> str:
    if before is None or after is None:
        return ""
    diff = after - before
    sign = "+" if diff >= 0 else ""
    return f" ({sign}{diff:.{digits}f})"


_INVALID_MARKER = " ⚠️진단용"


def _quality_score(summary: dict, metric: str) -> float | None:
    quality = summary.get("answer_quality")
    if not quality:
        return None
    block = quality.get(metric) or {}
    if not block.get("judged_count"):
        return None
    return block.get("mean_score")


def _success_rate(summary: dict) -> float | None:
    operations = summary.get("operations") or {}
    error_rate = operations.get("error_rate")
    if not operations.get("sample_count") or error_rate is None:
        return None
    return 1 - error_rate


def _row(label: str, before, after, digits: int = 3, *, invalid: bool = False) -> str:
    # invalid=True(둘 중 하나라도 quality_metrics_valid=False)면 Delta를
    # 아예 안 보여준다 - "+0.15" 같은 구체적 증감 숫자는 경고 문단을 건너뛰고
    # 표만 보는 사람에게 실제 성능 개선처럼 읽히기 쉽다. 값 자체는 계속
    # 보여주되(투명성), 신뢰할 수 없는 부분 표본이라는 표시를 값 옆에 남긴다.
    if invalid:
        return f"| {label} | {_fmt(before, digits)} | {_fmt(after, digits)}{_INVALID_MARKER} |"
    return (
        f"| {label} | {_fmt(before, digits)} | {_fmt(after, digits)}{_delta(before, after, digits)} |"
    )


def build_report(before: dict, after: dict, *, before_label: str, after_label: str) -> str:
    if before.get("question_set_sha256") != after.get("question_set_sha256"):
        mismatch = (
            "> [!WARNING]\n"
            "> 두 실행의 질문 세트 SHA-256이 다릅니다 - 같은 질문 파일로 비교하고 "
            "있는지 확인하세요. 이 리포트의 비교는 신뢰할 수 없습니다.\n\n"
        )
    else:
        mismatch = ""
    if before.get("question_count") != after.get("question_count"):
        mismatch += (
            f"> [!WARNING]\n> 질문 수가 다릅니다 (before={before.get('question_count')}, "
            f"after={after.get('question_count')}) - `--max-questions`/`--question-ids`를 "
            "맞췄는지 확인하세요.\n\n"
        )
    before_ids_sha, after_ids_sha = (
        before.get("executed_question_ids_sha256"),
        after.get("executed_question_ids_sha256"),
    )
    # 같은 파일(question_set_sha256 일치)에서 --question-ids로 서로 다른
    # 부분집합을 같은 개수만큼 고르면 파일 해시·질문 수가 둘 다 같아져 위
    # 두 체크를 통과한다 - 실제로 실행한 question_id 집합의 해시로 그 경우를
    # 따로 잡는다. 옛 summary.json(이 필드가 없는)과의 비교는 건너뛴다.
    if before_ids_sha and after_ids_sha and before_ids_sha != after_ids_sha:
        mismatch += (
            "> [!WARNING]\n"
            "> 같은 질문 파일에서 서로 다른 질문 부분집합을 비교하고 있습니다"
            "(question_set_sha256과 질문 수는 같지만 실행된 question_id 집합이 다릅니다) - "
            "`--question-ids`를 맞췄는지 확인하세요. 이 리포트의 점수 차이에는 질문 구성의"
            " 차이가 섞여 있을 수 있습니다.\n\n"
        )
    invalid_sides = [
        name
        for name, summary in (("Before", before), ("After", after))
        if not summary.get("quality_metrics_valid")
    ]
    if invalid_sides:
        mismatch += (
            "> [!WARNING]\n"
            f"> {', '.join(invalid_sides)} 실행이 quality_metrics_valid=False입니다 - 문항 중"
            " 하나라도 실행이 실패해 validation_runner 자신도 이 run의 headline 지표를 게시"
            " 불가로 표시한 상태입니다. 아래 숫자·Delta는 그 신뢰할 수 없는 부분 표본을"
            " 그대로 포함한 값이니 정상 실행과 비교한 것처럼 취급하지 마세요.\n\n"
        )

    r_before, r_after = before.get("retrieval") or {}, after.get("retrieval") or {}
    c_before, c_after = before.get("citation") or {}, after.get("citation") or {}
    a_before, a_after = before.get("abstention") or {}, after.get("abstention") or {}
    o_before, o_after = before.get("operations") or {}, after.get("operations") or {}

    faith_before = _quality_score(before, "faithfulness")
    faith_after = _quality_score(after, "faithfulness")
    rel_before = _quality_score(before, "relevancy")
    rel_after = _quality_score(after, "relevancy")

    lines = [
        "# 평가 비교 리포트",
        "",
        f"- Before: `{before_label}` ({before.get('question_count')}건, "
        f"{before.get('generated_at', '?')})",
        f"- After : `{after_label}` ({after.get('question_count')}건, "
        f"{after.get('generated_at', '?')})",
        f"- 질문 세트: `{before.get('question_set', '?')}`",
        "",
        mismatch,
        "## Evaluation",
        "",
        "| 지표 | Before | After |",
        "| --- | ---: | ---: |",
        _row("Recall@k", r_before.get("recall_at_k"), r_after.get("recall_at_k"), invalid=bool(invalid_sides)),
        _row("MRR@k", r_before.get("mrr_at_k"), r_after.get("mrr_at_k"), invalid=bool(invalid_sides)),
        _row("Citation Precision", c_before.get("precision"), c_after.get("precision"), invalid=bool(invalid_sides)),
        _row("Citation Coverage", c_before.get("coverage"), c_after.get("coverage"), invalid=bool(invalid_sides)),
        _row("Abstention Precision", a_before.get("precision"), a_after.get("precision"), invalid=bool(invalid_sides)),
        _row("Abstention Recall", a_before.get("recall"), a_after.get("recall"), invalid=bool(invalid_sides)),
        # Success Rate/latency는 quality_metrics_valid로 걸러지지 않는
        # operations 블록(validation_runner.calculate_summary가 항상 records
        # 전체로 계산)에서 나온다 - Recall 등과 달리 "일부 문항 실패 = 이
        # 값도 못 믿음"이 아니라, 오히려 그 실패 자체를 보여주는 지표다.
        # pool_evaluation_runs.py도 같은 이유로 이 둘은 invalid 여부와
        # 무관하게 계산한다(그쪽 주석 참고) - 여기서도 Delta를 죽이지 않는다.
        _row(
            "Success Rate (오류 없이 완료)",
            _success_rate(before),
            _success_rate(after),
        ),
        _row("Faithfulness (LLM-judge)", faith_before, faith_after, invalid=bool(invalid_sides)),
        _row("Answer relevancy (LLM-judge)", rel_before, rel_after, invalid=bool(invalid_sides)),
        _row(
            "p50 latency (ms)",
            o_before.get("p50_latency_ms"),
            o_after.get("p50_latency_ms"),
            0,
        ),
        "",
        f"- Citation pair count: before={c_before.get('citation_pair_count')}, "
        f"after={c_after.get('citation_pair_count')} "
        f"(required_claim_count: before={c_before.get('required_claim_count')}, "
        f"after={c_after.get('required_claim_count')})",
        f"- Abstention 분모: before actual_positive={a_before.get('actual_positive')}, "
        f"after actual_positive={a_after.get('actual_positive')}",
    ]
    if faith_before is None or faith_after is None:
        lines.append(
            "- Faithfulness/Answer relevancy 중 하나 이상이 N/A인 실행이 있습니다 "
            "(judge LLM 호출이 없었거나 전부 실패/건너뜀 - `llm_status`/`answer_quality` "
            "필드에서 원인을 확인하세요). N/A를 0으로 해석하지 마세요."
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path, help="수정 전 summary_<model>_<date>.json")
    parser.add_argument("after", type=Path, help="수정 후 summary_<model>_<date>.json")
    parser.add_argument("--output", type=Path, default=None, help="저장할 .md 경로 (생략하면 stdout)")
    args = parser.parse_args()

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    report = build_report(
        before, after, before_label=args.before.name, after_label=args.after.name
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"저장: {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
