"""정책 상세 문의(light_followup) 정확도 평가.

``data/evaluation/light_followup_policies.json``(동결된 정책 컨텍스트) +
``data/evaluation/light_followup_dev.jsonl``(질문 + 기대 결과)로 현재 LLM
설정(.env의 LLM_MODEL_NAME 등)의 정확도를 측정한다. 팀 평가 방식과 같은
사상: 질문·정답은 동결, LLM/프롬프트/검증 로직만 바꿔가며 같은 셋으로 재비교.

실행 (레포 루트, .venv):
    .venv\\Scripts\\python.exe scripts/eval_light_followup.py
    .venv\\Scripts\\python.exe scripts/eval_light_followup.py --save baseline

옵션:
    --save <이름>   결과를 data/evaluation/results/light_followup_<이름>.json 으로 저장
    --limit N       앞 N개 케이스만 (프롬프트 실험 중 빠르게 돌려볼 때)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
load_dotenv(_ROOT / ".env")

_POLICIES_PATH = _ROOT / "data" / "evaluation" / "light_followup_policies.json"
_DEV_PATH = _ROOT / "data" / "evaluation" / "light_followup_dev.jsonl"
_RESULTS_DIR = _ROOT / "data" / "evaluation" / "results"


def _load_cases() -> list[dict]:
    cases = []
    for line in _DEV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _load_policies() -> dict[str, dict]:
    return json.loads(_POLICIES_PATH.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", default=None, help="결과 저장 이름 (예: baseline)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--pace", type=float, default=1.0,
        help="케이스 사이 대기 초(기본 1초) - 무료 엔드포인트 버스트 방지",
    )
    args = parser.parse_args()

    from rag_chatbot.light_followup import respond_to_policy_question
    from rag_chatbot.service import build_llm_client

    llm = build_llm_client()
    if llm is None:
        print("LLM 클라이언트를 못 만들었습니다 (.env의 HF_TOKEN/LLM_BACKEND 확인).")
        sys.exit(1)

    model_name = getattr(llm, "model", None) or "?"
    policies = _load_policies()
    cases = _load_cases()
    if args.limit:
        cases = cases[: args.limit]

    print(f"모델: {model_name}  ·  케이스: {len(cases)}개\n")

    rows = []
    for i, case in enumerate(cases, 1):
        policy = policies.get(case["policy_id"])
        if policy is None:
            print(f"[{i}/{len(cases)}] {case['id']}: 정책 {case['policy_id']} 없음 - 스킵")
            continue

        if i > 1 and args.pace:
            time.sleep(args.pace)
        started = time.perf_counter()
        out = respond_to_policy_question(policy, case["question"], llm_client=llm)
        elapsed = time.perf_counter() - started

        kind_ok = out["kind"] == case["expected_kind"]
        contains_ok = True
        if case["expected_kind"] == "answer" and case.get("expected_contains"):
            text = out.get("text") or ""
            contains_ok = any(needle in text for needle in case["expected_contains"])
        passed = kind_ok and contains_ok

        mark = "OK  " if passed else "MISS"
        print(
            f"[{i}/{len(cases)}] {mark} ({elapsed:4.1f}s) {case['id']:<10} "
            f"기대={case['expected_kind']:<8} 실제={out['kind']:<8} "
            f"| {case['question']}"
        )
        if not passed:
            print(f"        note: {case.get('note', '')}")
            print(f"        답변: {(out.get('text') or '')[:150]!r}")

        rows.append(
            {
                "id": case["id"],
                "policy_id": case["policy_id"],
                "question": case["question"],
                "expected_kind": case["expected_kind"],
                "actual_kind": out["kind"],
                "kind_ok": kind_ok,
                "contains_ok": contains_ok,
                "passed": passed,
                "answer_text": out.get("text"),
                "elapsed_s": round(elapsed, 1),
            }
        )

    total = len(rows)
    passed_n = sum(r["passed"] for r in rows)
    answerable = [r for r in rows if r["expected_kind"] == "answer"]
    guidance = [r for r in rows if r["expected_kind"] == "guidance"]
    ans_pass = sum(r["passed"] for r in answerable)
    guide_pass = sum(r["passed"] for r in guidance)

    print("\n" + "=" * 60)
    print(f"전체:        {passed_n}/{total}  ({passed_n/total*100:.0f}%)" if total else "케이스 없음")
    if answerable:
        print(
            f"답변 가능:   {ans_pass}/{len(answerable)}  ({ans_pass/len(answerable)*100:.0f}%)  "
            "- 과잉 거절 여부"
        )
    if guidance:
        print(
            f"거절(함정):  {guide_pass}/{len(guidance)}  ({guide_pass/len(guidance)*100:.0f}%)  "
            "- 근거 없이 답하지 않는지"
        )

    if args.save:
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        dest = _RESULTS_DIR / f"light_followup_{args.save}.json"
        dest.write_text(
            json.dumps(
                {
                    "model": model_name,
                    "total": total,
                    "passed": passed_n,
                    "answerable_passed": ans_pass,
                    "answerable_total": len(answerable),
                    "guidance_passed": guide_pass,
                    "guidance_total": len(guidance),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n저장: {dest}")


if __name__ == "__main__":
    main()
