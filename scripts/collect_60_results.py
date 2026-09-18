"""Create blinded review packets and aggregate fully reviewed 60-question runs."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import statistics

MODELS = ("qwen", "ax", "bllossom")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path, data):
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


DEFAULT_BASE = Path("experiments/model_evaluation/local_300_20260916/comparison_runpod_60_20260916")


def load_selection(base):
    # Validate the original frozen 300-case manifest before selecting any rows.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.eval_300_ollama import validate_manifest
    manifest, manifest_hash, frozen = validate_manifest(base)
    selection = read(base / "selection.json")
    if selection["source_manifest_sha256"] != manifest_hash:
        raise ValueError("Selection source manifest differs")
    hashes = selection["selected_sha256"]
    if set(hashes) != {"selected_cases.json", "selected_scenarios.json"}:
        raise ValueError("Selected input inventory differs")
    for name, expected in hashes.items():
        if hashlib.sha256((base / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Selected input changed: {name}")
    cases = read(base / "selected_cases.json")
    scenarios = read(base / "selected_scenarios.json")
    ids = selection["ordered_ids"]
    case_map = {c["id"]: c for c in cases}
    original_scenarios = {s["id"]: s for s in read(base / "scenarios.json")}
    if (len(cases) != 60 or len(case_map) != 60 or len(ids) != 60
            or set(ids) != set(case_map) or len(set(ids)) != 60
            or any(c != frozen.get(c["id"]) for c in cases)):
        raise ValueError("Selection must contain 60 unique unchanged frozen cases")
    if ids != [cid for cid in manifest["order"] if cid in case_map]:
        raise ValueError("Selected execution order differs from frozen order")
    sids = {s["id"] for s in scenarios}
    if sorted(selection["situation_ids"]) != sorted(sids):
        raise ValueError("Selected situation inventory differs")
    if (len(scenarios) != 20 or len(sids) != 20
            or Counter(c["situation_id"] for c in cases) != Counter({sid: 3 for sid in sids})
            or any(s != original_scenarios.get(s["id"]) for s in scenarios)
            or any(len({c["style"] for c in cases if c["situation_id"] == sid}) != 3 for sid in sids)):
        raise ValueError("Selection must contain 20 unchanged situations with three styles each")
    return selection, [case_map[cid] for cid in ids], hashlib.sha256((base / "selection.json").read_bytes()).hexdigest()


def load_runs(base, cases, models=MODELS, allow_partial=False):
    selection, selected, selection_hash = load_selection(base)
    if cases != selected:
        raise ValueError("Cases must follow selected execution order")
    runs = {}
    expected = {c["id"] for c in cases}
    case_map = {c["id"]: c for c in cases}
    manifest_bytes = (base / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    for model in models:
        path = base / "runs" / f"{model}.jsonl"
        if allow_partial and not path.exists():
            continue
        raw = path.read_bytes()
        # An append may be in flight. Only newline-committed rows are ready.
        if allow_partial and raw and not raw.endswith(b"\n"):
            raw = raw[:raw.rfind(b"\n") + 1]
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if allow_partial and not rows:
            continue
        ids = {r["id"] for r in rows}
        if len(ids) != len(rows) or not ids <= expected or (not allow_partial and len(rows) != len(cases)):
            raise ValueError(f"{model}: expected unique completed question rows")
        if [r["id"] for r in rows] != selection["ordered_ids"][:len(rows)]:
            raise ValueError(f"{model}: execution order differs")
        profile_path = base / f"{model}_runtime_profile.json"
        profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest() if profile_path.exists() else None
        profile = read(profile_path)
        if (profile.get("selection_sha256") != selection_hash
                or profile.get("manifest_sha256") != manifest_hash
                or profile.get("model") != manifest["models"][model]
                or profile.get("generation") != manifest["generation"]
                or profile.get("disable_thinking") != manifest["disable_thinking"]
                or profile.get("wrapper_sha256") != hashlib.sha256((base / "runtime_wrapper_snapshot.py").read_bytes()).hexdigest()):
            raise ValueError(f"{model}: runtime selection or wrapper provenance mismatch")
        server_hash = hashlib.sha256((base / "server_runpod.json").read_bytes()).hexdigest()
        if profile.get("server_record_sha256") != server_hash:
            raise ValueError(f"{model}: server record changed")
        for row in rows:
            loaded = row.get("running_models") or []
            if (len(loaded) != 1 or not loaded[0].get("size", 0)
                    or loaded[0].get("size_vram", 0) != loaded[0]["size"]):
                raise ValueError(f"{model}: full GPU residency evidence missing")
            runtime = row.get("runtime_profile", {})
            if runtime.get("selection_sha256") != selection_hash or runtime.get("server_record_sha256") != server_hash:
                raise ValueError(f"{model}: row selection or server provenance mismatch")
            if (row.get("transport_failure") or row.get("runtime_alert") or row.get("model") != manifest["models"][model]
                    or row.get("model_digest") != manifest["expected"][model]["digest"]
                    or row.get("manifest_sha256") != manifest_hash):
                raise ValueError(f"{model}: incomplete attempt, runtime alert or provenance mismatch")
            if profile_hash and row.get("runtime_profile", {}).get("profile_sha256") != profile_hash:
                raise ValueError(f"{model}: runtime profile mismatch")
            if any(row[k] != case_map[row["id"]][k] for k in ("situation_id", "question", "category", "style", "policy_id")):
                raise ValueError(f"{model}: result does not match frozen question")
        runs[model] = {r["id"]: r for r in rows}
    return runs


def blind(base, cases, runs):
    target = base / "blind_review"
    target.mkdir(exist_ok=True)
    labels = ["응답 가", "응답 나", "응답 다"]
    random.Random(1942).shuffle(labels)
    mapping = dict(zip(MODELS, labels))
    mapping_path = base / "review_model_mapping.json"
    if mapping_path.exists():
        if read(mapping_path) != mapping:
            raise ValueError("Blind model mapping changed")
    else:
        write_new(mapping_path, mapping)
    existing = sorted(target.glob("packet_*.json"))
    seen = {(o["label"], o["id"]) for p in existing for s in read(p)["situations"] for o in s["outputs"]}
    pending = [(model, row) for model in runs for row in runs[model].values()
               if (mapping[model], row["id"]) not in seen]
    scenarios = {s["id"]: s for s in read(base / "selected_scenarios.json")}
    policies = read(base / "policies.json")
    rubric = read(base / "rubric.json")
    created = []
    for offset in range(0, len(pending), 30):
        batch = pending[offset:offset+30]
        # Leave a small incomplete batch until the next poll, except at the end.
        if len(batch) < 30 and sum(len(rows) for rows in runs.values()) < len(cases) * len(MODELS):
            break
        grouped = {}
        for model, row in batch:
            sid = row["situation_id"]
            if sid not in grouped:
                s = scenarios[sid]
                grouped[sid] = {"situation": s, "outputs": []}
            n1, detail = row["n1"], row["detail"]
            grouped[sid]["outputs"].append({
                "id": row["id"], "label": mapping[model],
                "n1": {"slots": n1["slots"], "baseline": n1.get("baseline"),
                       "parsed_calls": [c.get("parsed") for c in n1["calls"]],
                       "call_format": [{"strict_json": c.get("strict_json"), "done_reason": c.get("done_reason"),
                                        "error": c.get("error")} for c in n1["calls"]]},
                "detail": {k: detail.get(k) for k in ("final", "generation", "quotes_valid", "outcome_reason")},
            })
        packet = target / f"packet_{len(existing)+len(created)+1:03}.json"
        used_policies = {g["situation"]["policy_id"]: policies[g["situation"]["policy_id"]] for g in grouped.values()}
        write_new(packet, {"rubric": rubric, "policies": used_policies, "situations": list(grouped.values())})
        created.append(packet.name)
    print(json.dumps({"new_packets": created, "new_output_records": len(created)*30}, ensure_ascii=False))


def aggregate(base, cases, runs):
    mapping = read(base / "review_model_mapping.json")
    inverse = {label: model for model, label in mapping.items()}
    review_files = sorted((base / "blind_review").glob("scores_*.json"))
    reviewed = {}
    case_map = {c["id"]: c for c in cases}
    for path in review_files:
        source = read(path)
        for score in source["scores"]:
            key = (inverse[score["label"]], score["id"])
            if key in reviewed:
                raise ValueError(f"Duplicate score {key}")
            case = case_map[score["id"]]
            for field in ("detail_helpful", "detail_unsupported", "n1_invented"):
                if type(score.get(field)) is not bool:
                    raise ValueError(f"Missing boolean {key}: {field}")
            for field in ("raw_supported", "raw_helpful"):
                if score.get(field) is not None and type(score[field]) is not bool:
                    raise ValueError(f"Invalid {key}: {field}")
            for field in ("n1_score", "expression_score"):
                if score.get(field) is not None and (type(score[field]) is not int or not 1 <= score[field] <= 5):
                    raise ValueError(f"Invalid {key}: {field}")
            if case["n1_applicable"] != (score.get("n1_score") is not None):
                raise ValueError(f"N1 applicability differs from frozen dataset: {key}")
            generation = runs[key[0]][key[1]]["detail"].get("generation")
            generated_answer = bool(generation and generation.get("answerable"))
            if generated_answer != (score.get("expression_score") is not None):
                raise ValueError(f"Expression applicability mismatch: {key}")
            if generated_answer != (score.get("raw_supported") is not None) or generated_answer != (score.get("raw_helpful") is not None):
                raise ValueError(f"Raw answer applicability mismatch: {key}")
            if not score.get("detail_note") or not score.get("n1_note"):
                raise ValueError(f"Review rationale missing: {key}")
            if score.get("n1_error_origin") not in ("오류 없음", "모델 출력", "공통 처리", "둘 다", "구분 어려움"):
                raise ValueError(f"N1 error origin missing or invalid: {key}")
            score["detail_success"] = score["detail_helpful"] and not score["detail_unsupported"]
            reviewed[key] = score
    amendment_files = sorted((base / "blind_review").glob("amendments_*.json"))
    amended = set()
    for path in amendment_files:
        amendment = read(path)
        source = amendment["source"]
        if Path(source).name != source or source not in {p.name for p in review_files}:
            raise ValueError("Amendment source must be an original score file")
        original_path = base / "blind_review" / source
        if hashlib.sha256(original_path.read_bytes()).hexdigest() != amendment["source_sha256"]:
            raise ValueError("Original review changed before amendment")
        original_keys = {(s["label"], s["id"]) for s in read(original_path)["scores"]}
        for change in amendment["changes"]:
            key = (inverse[change["label"]], change["id"])
            if (change["label"], change["id"]) not in original_keys or key in amended:
                raise ValueError("Duplicate amendment or wrong source record")
            before, after = change["before"], change["after"]
            if set(before) != set(after) or not set(after) <= {"n1_score", "n1_error_origin", "n1_note", "detail_helpful", "raw_helpful", "detail_note"}:
                raise ValueError("Only documented review amendments are accepted")
            if any(reviewed[key].get(k) != v for k, v in before.items()) or not change.get("reason"):
                raise ValueError("Amendment does not match original score")
            updated = dict(reviewed[key], **after)
            if (type(updated["detail_helpful"]) is not bool or not updated["detail_note"]
                    or (updated["raw_helpful"] is not None and type(updated["raw_helpful"]) is not bool)
                    or (updated["raw_helpful"] is None) != (reviewed[key]["raw_helpful"] is None)):
                raise ValueError("Invalid amended helpfulness score")
            updated["detail_success"] = updated["detail_helpful"] and not updated["detail_unsupported"]
            value = updated["n1_score"]
            if ((value is not None) != case_map[key[1]]["n1_applicable"]
                    or (value is not None and (type(value) is not int or not 1 <= value <= 5))
                    or updated["n1_error_origin"] not in ("오류 없음", "모델 출력", "공통 처리", "둘 다", "구분 어려움")
                    or not updated["n1_note"]):
                raise ValueError("Invalid amended N1 score")
            reviewed[key] = updated
            amended.add(key)
    if len(reviewed) != len(cases) * len(MODELS):
        raise ValueError(f"All 180 outputs need review; only {len(reviewed)} found")
    combined, summaries, situations = [], {}, []
    for model in MODELS:
        rows = []
        for case in cases:
            row = runs[model][case["id"]]
            combined.append({"model": model, "case": {k: v for k, v in case.items() if k != "policy"},
                             "result": row, "score": reviewed[(model, case["id"]) ]})
            rows.append(combined[-1])
        grouped = []
        for sid in sorted({c["situation_id"] for c in cases}):
            members = [r for r in rows if r["case"]["situation_id"] == sid]
            score = {"model": model, "situation_id": sid, "category": members[0]["case"]["category"],
                     "success_count": sum(r["score"]["detail_success"] for r in members),
                     "unsupported_count": sum(r["score"]["detail_unsupported"] for r in members)}
            situations.append(score)
            grouped.append(score)
        n1_scores = [r["score"]["n1_score"] for r in rows if r["score"]["n1_score"] is not None]
        expression = [r["score"]["expression_score"] for r in rows if r["score"]["expression_score"] is not None]
        calls = [call for r in rows for track in ("n1", "detail") for call in r["result"][track]["calls"]]
        times = [r["result"]["detail"]["elapsed_s"] for r in rows]
        summaries[model] = {
            "sentences": len(cases), "situations": len(grouped),
            "detail_success": sum(r["score"]["detail_success"] for r in rows),
            "detail_helpful": sum(r["score"]["detail_helpful"] for r in rows),
            "detail_unsupported": sum(r["score"]["detail_unsupported"] for r in rows),
            "all_three_success": sum(s["success_count"] == 3 for s in grouped),
            "style_sensitive_situations": sum(0 < s["success_count"] < 3 for s in grouped),
            "all_three_failed": sum(s["success_count"] == 0 for s in grouped),
            "n1_mean": statistics.mean(n1_scores) if n1_scores else None, "n1_count": len(n1_scores),
            "n1_invented": sum(r["score"]["n1_invented"] for r in rows),
            "expression_mean": statistics.mean(expression) if expression else None, "expression_count": len(expression),
            "raw_supported_count": sum(r["score"]["raw_supported"] is True for r in rows),
            "raw_helpful_count": sum(r["score"]["raw_helpful"] is True for r in rows),
            "raw_answer_count": sum(r["score"]["raw_supported"] is not None for r in rows),
            "helpful_supported_but_blocked": sum(r["score"]["raw_supported"] is True and r["score"]["raw_helpful"] is True
                                                  and r["result"]["detail"]["final"]["kind"] != "answer" for r in rows),
            "outcome_counts": dict(Counter(r["result"]["detail"]["outcome_reason"] for r in rows)),
            "calls": len(calls), "call_errors": sum("error" in c for c in calls),
            "length_limited_calls": sum(c.get("done_reason") == "length" for c in calls),
            "strict_json_calls": sum(bool(c.get("strict_json")) for c in calls),
            "detail_median_seconds": statistics.median(times),
            "n1_median_seconds": statistics.median(r["result"]["n1"]["elapsed_s"] for r in rows),
        }
        for field in ("category", "style"):
            summaries[model][field] = {key: {"n": len(group), "success": sum(r["score"]["detail_success"] for r in group),
                                           "unsupported": sum(r["score"]["detail_unsupported"] for r in group)}
                                     for key in sorted({r["case"][field] for r in rows})
                                     for group in [[r for r in rows if r["case"][field] == key]]}
    result = {"models": summaries, "situation_results": situations,
              "review_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in review_files},
              "amendment_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in amendment_files},
              "manifest_sha256": hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest(),
              "selection_sha256": hashlib.sha256((base / "selection.json").read_bytes()).hexdigest()}
    write_new(base / "reviewed_results.json", combined)
    write_new(base / "result_summary.json", result)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--blind", action="store_true")
    parser.add_argument("--ready", action="store_true", help="Package completed immutable rows during inference")
    parser.add_argument("--blind-model", choices=MODELS, help="Prepare one completed model while other models run")
    parser.add_argument("--final", action="store_true", help="Require all 180 independently reviewed outputs")
    args = parser.parse_args()
    if args.final and (args.blind or args.blind_model or args.ready):
        parser.error("--final cannot be combined with packet modes")
    _, cases, _ = load_selection(args.output)
    runs = load_runs(args.output, cases, (args.blind_model,) if args.blind_model else MODELS, allow_partial=args.ready)
    blind(args.output, cases, runs) if args.blind or args.blind_model or args.ready else aggregate(args.output, cases, runs)
