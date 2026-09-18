"""여러 summary_*.json을 하나의 표본으로 합산(pool)한다.

`compare_evaluation_runs.py`가 "두 실행을 나란히 놓고 비교"한다면, 이
스크립트는 "N개 실행을 합쳐서 하나의 지표"로 만든다 - 예를 들어
dev_questions.jsonl(150) + policy_eval_questions.jsonl(100) +
quasi_holdout_questions.jsonl(100) = 총 250(또는 350)문항 기준 하나의
Recall/Citation Precision/Abstention/Faithfulness를 보고 싶을 때 쓴다.

단순 평균이 아니라 **분자·분모를 실제로 더한 뒤 다시 나누는 count
가중 결합**을 쓴다(각 요약 파일의 raw 카운트 필드만 사용, 새로 추정하지
않음):

- Recall@k, MRR@k: evaluated_queries로 가중(둘 다 그 개수에 대한
  단순평균이므로, 가중평균 = 합친 표본의 평균과 정확히 같다).
- Citation precision/coverage: citation_pair_count/required_claim_count로
  가중(precision = valid_pairs/pairs이므로 valid_pairs를 역산해 더한다).
- Abstention precision/recall: true_positive/predicted_positive/actual_positive를
  그대로 더한다(비율이 아니라 원본 카운트라 가장 정확하다).
- Faithfulness/Relevancy: judged_count로 가중.
- LLM 호출 실패율: calls/failures를 그대로 더한다.
- Success rate: sample_count로 가중.

한 요약 파일이라도 `answer_quality`가 없으면(--skip-answer-quality로 실행한
경우) Faithfulness/Relevancy는 그 파일을 계산에서 빼고 "일부 세트만
반영"이라고 표시한다 - 없는 값을 0으로 넣지 않는다.

실행:
    python scripts/pool_evaluation_runs.py a.json b.json c.json --output pooled.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _fmt(value, digits: int = 3) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def pool(summaries: list[dict]) -> dict:
    total_n = sum(s["question_count"] for s in summaries)

    r_n = sum(s["retrieval"]["evaluated_queries"] for s in summaries)
    recall = sum(
        s["retrieval"]["recall_at_k"] * s["retrieval"]["evaluated_queries"] for s in summaries
    ) / r_n if r_n else 0.0
    mrr = sum(
        s["retrieval"]["mrr_at_k"] * s["retrieval"]["evaluated_queries"] for s in summaries
    ) / r_n if r_n else 0.0

    pairs = sum(s["citation"]["citation_pair_count"] for s in summaries)
    valid_pairs = sum(
        s["citation"]["precision"] * s["citation"]["citation_pair_count"] for s in summaries
    )
    precision = valid_pairs / pairs if pairs else 0.0
    req = sum(s["citation"]["required_claim_count"] for s in summaries)
    covered = sum(
        s["citation"]["coverage"] * s["citation"]["required_claim_count"] for s in summaries
    )
    coverage = covered / req if req else 0.0

    tp = sum(s["abstention"]["true_positive"] for s in summaries)
    pp = sum(s["abstention"]["predicted_positive"] for s in summaries)
    ap = sum(s["abstention"]["actual_positive"] for s in summaries)
    abstention_precision = tp / pp if pp else 0.0
    abstention_recall = tp / ap if ap else 0.0

    calls = sum(s["llm_status"]["calls"] or 0 for s in summaries if s["llm_status"].get("calls") is not None)
    failures = sum(
        s["llm_status"]["failures"] or 0 for s in summaries if s["llm_status"].get("failures") is not None
    )

    samples = sum(s["operations"]["sample_count"] for s in summaries)
    err_weighted = sum(
        s["operations"]["error_rate"] * s["operations"]["sample_count"] for s in summaries
    )
    error_rate = err_weighted / samples if samples else 0.0

    quality_sets = [s for s in summaries if s.get("answer_quality")]
    faith = None
    relevancy = None
    faith_judged = relevancy_judged = 0
    if quality_sets:
        faith_judged = sum(s["answer_quality"]["faithfulness"]["judged_count"] for s in quality_sets)
        if faith_judged:
            faith = sum(
                (s["answer_quality"]["faithfulness"]["mean_score"] or 0)
                * s["answer_quality"]["faithfulness"]["judged_count"]
                for s in quality_sets
            ) / faith_judged
        relevancy_judged = sum(s["answer_quality"]["relevancy"]["judged_count"] for s in quality_sets)
        if relevancy_judged:
            relevancy = sum(
                (s["answer_quality"]["relevancy"]["mean_score"] or 0)
                * s["answer_quality"]["relevancy"]["judged_count"]
                for s in quality_sets
            ) / relevancy_judged

    return {
        "total_questions": total_n,
        "sets_included": len(summaries),
        "sets_with_quality": len(quality_sets),
        "recall_at_k": recall,
        "mrr_at_k": mrr,
        "citation_precision": precision,
        "citation_coverage": coverage,
        "citation_pair_count": pairs,
        "citation_valid_pairs": round(valid_pairs),
        "abstention_precision": abstention_precision,
        "abstention_recall": abstention_recall,
        "abstention_tp": tp,
        "abstention_pp": pp,
        "abstention_ap": ap,
        "faithfulness": faith,
        "faithfulness_judged": faith_judged,
        "relevancy": relevancy,
        "relevancy_judged": relevancy_judged,
        "llm_calls": calls,
        "llm_failures": failures,
        "success_rate": 1 - error_rate,
    }


def build_report(label: str, summaries: list[dict], paths: list[Path]) -> str:
    p = pool(summaries)
    lines = [
        f"# {label}",
        "",
        f"- 합산 대상: {p['sets_included']}개 세트, 총 {p['total_questions']}문항",
        "- 계산 방식: 단순 평균이 아니라 분자·분모를 합쳐서 다시 나누는 count 가중 결합",
        "",
        "| 세트 | 문항 수 | 경로 |",
        "| --- | ---: | --- |",
    ]
    for s, p_path in zip(summaries, paths):
        lines.append(f"| {s.get('model_name', '?')} / {s['question_set']} | {s['question_count']} | `{p_path}` |")
    lines += [
        "",
        "## 합산 결과",
        "",
        "| 지표 | 값 | 근거(분자/분모) |",
        "| --- | ---: | --- |",
        f"| Recall@k | {_fmt(p['recall_at_k'])} | evaluated_queries 가중 |",
        f"| MRR@k | {_fmt(p['mrr_at_k'])} | evaluated_queries 가중 |",
        f"| Citation Precision | {_fmt(p['citation_precision'])} | {p['citation_valid_pairs']} / {p['citation_pair_count']} |",
        f"| Citation Coverage | {_fmt(p['citation_coverage'])} | - |",
        f"| Abstention Precision | {_fmt(p['abstention_precision'])} | tp={p['abstention_tp']} / pp={p['abstention_pp']} |",
        f"| Abstention Recall | {_fmt(p['abstention_recall'])} | tp={p['abstention_tp']} / ap={p['abstention_ap']} |",
        f"| Faithfulness | {_fmt(p['faithfulness']) if p['faithfulness'] is not None else 'N/A'} | 판정 {p['faithfulness_judged']}건 ({p['sets_with_quality']}/{p['sets_included']}개 세트에 답변 품질 데이터 있음) |",
        f"| Answer Relevancy | {_fmt(p['relevancy']) if p['relevancy'] is not None else 'N/A'} | 판정 {p['relevancy_judged']}건 |",
        f"| LLM 호출 실패율 | {_fmt(p['llm_failures']/p['llm_calls']*100 if p['llm_calls'] else None, 2)}% | {p['llm_failures']}/{p['llm_calls']} |",
        f"| Success Rate | {_fmt(p['success_rate'])} | - |",
        "",
    ]
    if p["sets_with_quality"] < p["sets_included"]:
        lines.append(
            "> [!WARNING]\n"
            f"> {p['sets_included']}개 세트 중 {p['sets_with_quality']}개만 답변 품질(Faithfulness/"
            "Relevancy) 데이터가 있습니다 - 나머지는 그 단계가 건너뛰어졌거나(--skip-answer-quality)"
            " judge 호출이 전부 실패한 세트입니다. 위 Faithfulness/Relevancy 합산치는 그 세트들을"
            " 제외하고 계산했습니다.\n"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", type=Path, nargs="+", help="합산할 summary_*.json 경로들")
    parser.add_argument("--label", type=str, default="평가 결과 합산 리포트")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in args.summaries]
    report = build_report(args.label, loaded, args.summaries)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"저장: {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
