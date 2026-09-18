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

`quality_metrics_valid`가 False인 요약(문항 중 하나라도 실행이 실패해
validation_runner 자신도 "비교 가능한 Baseline으로 게시할 수 없다"고
표시한 run)은 합산에서 제외한다. 그 run의 headline 지표는 애초에
신뢰할 수 없는 부분 표본인데, 여기서 그대로 합치면 정상 run의 숫자와
섞여 "게시 불가"였던 값이 게시 가능한 것처럼 둔갑한다.

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
    all_summaries = summaries
    invalid = [s for s in all_summaries if not s.get("quality_metrics_valid")]
    summaries = [s for s in all_summaries if s.get("quality_metrics_valid")]

    # 운영 통계(LLM 호출·실패, 오류율/성공률)는 quality_metrics_valid와 무관하게
    # 시도된 전체(all_summaries)로 계산한다. quality_metrics_valid는 "이 run의
    # 회수(Recall 등) 지표가 완주한 표본만큼 신뢰할 수 있는가"를 보는 값이지,
    # "이 run에서 오류·LLM 실패가 있었는가"와는 다른 질문이다 - invalid한
    # run을 통째로 빼고 성공률을 내면, 정작 오류가 많아서 invalid해진 run이
    # 통계에서 사라져 남은 run들만의 성공률(예: 100문항 중 50문항이 실패한
    # run이 빠지면 나머지 run만으로 성공률 100%)이 "전체 운영 성공률"처럼
    # 보고된다.
    op_calls = sum(
        s["llm_status"]["calls"] or 0
        for s in all_summaries
        if s.get("llm_status", {}).get("calls") is not None
    )
    op_failures = sum(
        s["llm_status"]["failures"] or 0
        for s in all_summaries
        if s.get("llm_status", {}).get("failures") is not None
    )
    # llm_status와 같은 이유로 방어적으로 읽는다: all_summaries는 이제
    # quality_metrics_valid=False나 옛 포맷(operations/question_count가 없는)
    # 파일도 포함하므로, 이 필드들을 무조건 인덱싱하면 그런 입력에서
    # KeyError로 죽는다 - 예전에는 quality_metrics_valid=True인 파일만
    # 여기 도달해서 문제가 안 됐다.
    op_samples = sum((s.get("operations") or {}).get("sample_count") or 0 for s in all_summaries)
    op_err_weighted = sum(
        ((s.get("operations") or {}).get("error_rate") or 0)
        * ((s.get("operations") or {}).get("sample_count") or 0)
        for s in all_summaries
    )
    op_error_rate = op_err_weighted / op_samples if op_samples else 0.0
    op_total_questions = sum(s.get("question_count") or 0 for s in all_summaries)

    if not summaries:
        return {
            "total_questions": 0,
            "sets_included": 0,
            "sets_excluded_invalid": len(invalid),
            "sets_with_quality": 0,
            "quality_metrics_valid": False,
            "op_total_questions": op_total_questions,
            "op_sets_attempted": len(all_summaries),
            "llm_calls": op_calls,
            "llm_failures": op_failures,
            "success_rate": 1 - op_error_rate,
        }

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
        "sets_excluded_invalid": len(invalid),
        "sets_with_quality": len(quality_sets),
        "quality_metrics_valid": True,
        "op_total_questions": op_total_questions,
        "op_sets_attempted": len(all_summaries),
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
        "llm_calls": op_calls,
        "llm_failures": op_failures,
        "success_rate": 1 - op_error_rate,
    }


def build_report(label: str, summaries: list[dict], paths: list[Path]) -> str:
    p = pool(summaries)
    lines = [
        f"# {label}",
        "",
        f"- 합산 대상(Recall 등 품질 지표): {p['sets_included']}개 세트, 총 {p['total_questions']}문항",
        f"- 운영 통계(LLM 호출·Success Rate) 기준: 시도 전체 {p['op_sets_attempted']}개 세트, "
        f"총 {p['op_total_questions']}문항 - quality_metrics_valid=False로 제외된 세트도 포함합니다.",
        "- 계산 방식: 단순 평균이 아니라 분자·분모를 합쳐서 다시 나누는 count 가중 결합",
        "",
        "| 세트 | 문항 수 | 경로 | 상태 |",
        "| --- | ---: | --- | --- |",
    ]
    for s, p_path in zip(summaries, paths):
        status = "포함" if s.get("quality_metrics_valid") else "제외(quality_metrics_valid=False)"
        lines.append(
            f"| {s.get('model_name', '?')} / {s['question_set']} | {s['question_count']} | `{p_path}` | {status} |"
        )
    if p["sets_excluded_invalid"]:
        lines.append("")
        lines.append(
            "> [!WARNING]\n"
            f"> {p['sets_excluded_invalid']}개 세트가 quality_metrics_valid=False라 합산에서"
            " 제외됐습니다 - 문항 중 하나라도 실행이 실패해 그 run 자체가 (validation_runner"
            " 기준으로) 게시 불가인 표본입니다. 신뢰할 수 없는 부분 점수를 정상 run과 섞지"
            " 않기 위해 아예 뺐습니다."
        )
    # 운영 통계(LLM 호출 실패율·Success Rate)는 quality_metrics_valid 여부와
    # 무관하게 시도된 전체(op_sets_attempted개 세트, op_total_questions문항)
    # 기준이다 - Recall 등 나머지 지표와 분모가 다르므로 표 밑에 분모를 함께
    # 적어 헷갈리지 않게 한다.
    op_lines = [
        f"| LLM 호출 실패율 | {_fmt(p['llm_failures']/p['llm_calls']*100 if p['llm_calls'] else None, 2)}% | {p['llm_failures']}/{p['llm_calls']} (시도 전체 {p['op_sets_attempted']}세트 {p['op_total_questions']}문항 기준) |",
        f"| Success Rate | {_fmt(p['success_rate'])} | 시도 전체 {p['op_sets_attempted']}세트 {p['op_total_questions']}문항 기준 |",
    ]
    if not p.get("quality_metrics_valid", True):
        lines.append("")
        lines += [
            "## 합산 결과",
            "",
            "합산 가능한(quality_metrics_valid=True) 세트가 하나도 없어 Recall 등 품질 지표는"
            " 게시할 수 없습니다. 운영 통계(LLM 호출·오류)는 시도된 세트 전체를 기준으로 계속"
            " 표시합니다 - 이건 quality_metrics_valid의 영향을 받지 않는 별도 집계입니다.",
            "",
            "| 지표 | 값 | 근거(분자/분모) |",
            "| --- | ---: | --- |",
            *op_lines,
        ]
        return "\n".join(lines)
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
        *op_lines,
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
