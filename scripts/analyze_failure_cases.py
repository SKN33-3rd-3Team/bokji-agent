"""results_*.jsonl 한 파일을 failure category별로 분류해 보여준다.

run_model_evaluation.py/run_dev_validation.py가 이미 만든 결과 파일만 읽는다
- 새 LLM 호출을 하지 않는다(순수 분석). 카테고리 판정 기준:

  RETRIEVAL_MISS_OR_RANKING : 정상 질문인데 top-k 안에 정답 정책이 없음
                              (retrieved_policy_ids에 없음 - top-k 밖 순위 밀림과
                              완전 미검색을 이 필드만으로는 구분 못 해 하나로 묶는다.
                              더 정확히 가르려면 scripts/diagnose_gold_policy_retrieval.py처럼
                              top_k를 크게 잡은 재검색이 필요하다)
  RELEVANCE_GATE_FALSE_ABSTAIN : 정상 질문인데 검색 결과가 0건(관련성 게이트가
                              전부 걸러냈을 가능성이 가장 높은 신호)
  BAD_ABSTENTION            : should_abstain과 실제 abstained가 다름(위 항목과 겹칠 수 있음)
  UNSUPPORTED_CLAIM         : Faithfulness 판정이 "근거없음"
  PARTIAL_FAITHFULNESS      : Faithfulness 판정이 "부분근거"
  LOW_RELEVANCY             : Answer relevancy 판정이 "무관"
  LLM_FAILURE_OR_FALLBACK   : 이 질문 처리 중 LLM 호출 실패가 1건 이상 있었음
  EVALUATION_ERROR          : terminal_status == "failed"(예외로 중단)
  MULTI_TURN_ANOMALY        : turn_count > 1인데 unanswered_slots가 있음(정보 부족한 채로 답변 진행)
  OK                        : 위 어디에도 안 걸림

한 질문이 여러 카테고리에 동시에 걸릴 수 있다 - 실패는 보통 한 원인이
아니라 여러 신호가 겹쳐서 나타난다.

실행:
    python scripts/analyze_failure_cases.py <results.jsonl> [--limit 30]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _categorize(row: dict) -> list[str]:
    cats: list[str] = []
    expected = set(row.get("expected_policy_ids") or [])
    retrieved = set(row.get("retrieved_policy_ids") or [])
    should_abstain = bool(row.get("should_abstain"))
    abstained = bool(row.get("abstained"))

    if row.get("terminal_status") == "failed" or row.get("error"):
        cats.append("EVALUATION_ERROR")

    if not should_abstain:
        if expected and not (expected & retrieved):
            if not retrieved:
                cats.append("RELEVANCE_GATE_FALSE_ABSTAIN" if abstained else "RETRIEVAL_MISS_OR_RANKING")
            else:
                cats.append("RETRIEVAL_MISS_OR_RANKING")

    if should_abstain != abstained:
        cats.append("BAD_ABSTENTION")

    aq = row.get("answer_quality") or {}
    faith = aq.get("faithfulness") or {}
    if faith.get("verdict") == "근거없음":
        cats.append("UNSUPPORTED_CLAIM")
    elif faith.get("verdict") == "부분근거":
        cats.append("PARTIAL_FAITHFULNESS")
    rel = aq.get("relevancy") or {}
    if rel.get("verdict") == "무관":
        cats.append("LOW_RELEVANCY")

    llm_status = row.get("llm_status") or {}
    if llm_status.get("failures"):
        cats.append("LLM_FAILURE_OR_FALLBACK")

    if (row.get("turn_count") or 0) > 1 and row.get("unanswered_slots"):
        cats.append("MULTI_TURN_ANOMALY")

    return cats or ["OK"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--limit", type=int, default=30, help="상세 출력할 failure case 최대 건수")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="UTF-8로 저장할 경로 - Windows 콘솔(cp949)에 못 찍는 문자(judge가 섞어 쓰는 "
        "한자 등)가 있어도 안전하게 저장한다. 생략하면 stdout에 찍는다.",
    )
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    counts: Counter[str] = Counter()
    detailed: list[dict] = []
    for row in rows:
        cats = _categorize(row)
        for cat in cats:
            counts[cat] += 1
        if cats != ["OK"]:
            detailed.append({**row, "_categories": cats})

    out: list[str] = []
    out.append(f"총 {len(rows)}건 중 failure 신호가 있는 질문 {len(detailed)}건")
    out.append("")
    out.append("카테고리별 건수 (한 질문이 여러 카테고리에 동시에 잡힐 수 있음):")
    for cat, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        out.append(f"  {cat:<32} {count}")
    out.append("")

    for row in detailed[: args.limit]:
        out.append("=" * 70)
        out.append(f"question_id: {row['question_id']}")
        out.append(f"categories : {', '.join(row['_categories'])}")
        out.append(f"expected   : {row.get('expected_policy_ids')}")
        out.append(f"retrieved  : {row.get('retrieved_policy_ids')}")
        out.append(f"cited      : {row.get('cited_policy_ids')}")
        out.append(
            f"should_abstain={row.get('should_abstain')} abstained={row.get('abstained')} "
            f"answer_status={row.get('answer_status')}"
        )
        aq = row.get("answer_quality") or {}
        faith = aq.get("faithfulness") or {}
        rel = aq.get("relevancy") or {}
        if faith.get("verdict"):
            out.append(f"faithfulness={faith.get('verdict')} reason={faith.get('reason','')[:200]!r}")
        if rel.get("verdict"):
            out.append(f"relevancy={rel.get('verdict')} reason={rel.get('reason','')[:200]!r}")
        llm_status = row.get("llm_status") or {}
        if llm_status.get("failures"):
            out.append(f"llm failures={llm_status.get('failures')} messages={llm_status.get('messages')}")
        out.append("")

    text = "\n".join(out)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"저장: {args.output}")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
