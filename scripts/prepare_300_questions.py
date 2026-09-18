"""Validate reviewed drafts and write a new, source-bound 300-question set."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STYLES = ["정중한 질문", "편한 반말", "짧은 메모", "긴 사연", "급한 말투",
          "걱정하는 말투", "가벼운 오타", "띄어쓰기 부족", "구어체 존댓말", "항목 나열"]
CATEGORIES = dict(normal=25, missing=20, boundary=20, subject=15, unknown=10, trap=10)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def validate(scenarios, policies):
    errors, questions, styles = [], [], []
    if len(scenarios) != 100:
        errors.append(f"Expected 100 situations, got {len(scenarios)}")
    if Counter(s["category"] for s in scenarios) != CATEGORIES:
        errors.append("Category quotas differ")
    expected_ids = [f"S{n:03}" for n in range(1, 101)]
    if [s["id"] for s in scenarios] != expected_ids:
        errors.append("Situation IDs are missing, duplicated, or unordered")
    for s in scenarios:
        sid = s["id"]
        policy = policies.get(s["policy_id"])
        if policy is None:
            errors.append(f"{sid}: unknown policy")
            continue
        for key in ("title", "required_points", "reference_answer", "reference_quotes", "n1_review"):
            if not s.get(key):
                errors.append(f"{sid}: missing {key}")
        source_strings = list(strings(policy))
        for quote in s["reference_quotes"]:
            if not isinstance(quote, str) or not quote.strip():
                errors.append(f"{sid}: empty evidence")
            elif len(quote.strip()) < 8 and quote not in source_strings:
                errors.append(f"{sid}: short evidence must quote an entire source field")
            elif not any(quote in text for text in source_strings):
                errors.append(f"{sid}: evidence not verbatim: {quote[:80]}")
        variants = s.get("variants", [])
        if len(variants) != 3:
            errors.append(f"{sid}: expected three phrasings")
        n = int(sid[1:]) - 1
        for j, v in enumerate(variants):
            if v["id"] != f"{sid}-{'ABC'[j]}":
                errors.append(f"{sid}: bad variant ID")
            if v["style"] != STYLES[(n + 3*j) % 10]:
                errors.append(f"{v['id']}: unexpected style")
            question = v["question"]
            if not isinstance(question, str) or not question.strip():
                errors.append(f"{v['id']}: empty question")
            questions.append(re.sub(r"\s+", "", question))
            styles.append(v["style"])
    if len(set(questions)) != len(questions):
        errors.append("Duplicate questions after removing whitespace")
    if Counter(styles) != dict.fromkeys(STYLES, 30):
        errors.append("Expected 30 questions per style")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Read drafts without writing a frozen dataset")
    args = parser.parse_args()
    policies = read(ROOT / "data/evaluation/light_followup_policies.json")
    scenarios = sorted([s for group in "abcd" for s in read(args.output / "drafts" / f"{group}.json")],
                       key=lambda s: s["id"])
    errors = validate(scenarios, policies)
    if errors:
        raise SystemExit("\n".join(errors))
    print("Validated: 100 situations, 300 unique questions, 10 styles x 30, verbatim evidence")
    if args.check:
        return
    review = read(args.output / "dataset_review.json")
    if review.get("approved_situation_ids") != [s["id"] for s in scenarios]:
        raise ValueError("Independent review must approve all 100 situations before freezing")
    if review.get("unresolved_findings"):
        raise ValueError("Unresolved review findings remain")
    current_hashes = {f"{g}.json": hashlib.sha256((args.output / "drafts" / f"{g}.json").read_bytes()).hexdigest()
                      for g in "abcd"}
    if review.get("draft_sha256") != current_hashes:
        raise ValueError("Drafts changed after review")
    for s in scenarios:
        decision = review["n1_applicability"][s["id"]]
        if type(decision) is not bool:
            raise ValueError("N1 applicability must be decided before model outputs")
        s["n1_applicable"] = decision
    cases = []
    for s in scenarios:
        common = {key: value for key, value in s.items() if key not in ("id", "variants")}
        for v in s["variants"]:
            cases.append({**common, **v, "situation_id": s["id"], "policy": policies[s["policy_id"]],
                          "suite": "expanded_followup",
                          "expected_kind": "guidance" if s["category"] == "unknown" else "answer"})
    destinations = [args.output / name for name in ("scenarios.json", "cases.json", "policies.json")]
    if any(path.exists() for path in destinations):
        raise FileExistsError("Refusing to overwrite any frozen question artifact")
    for path, value in zip(destinations, (scenarios, cases, policies)):
        write_new(path, value)
    print(f"Created a new 300-question dataset in {args.output}")


if __name__ == "__main__":
    main()
