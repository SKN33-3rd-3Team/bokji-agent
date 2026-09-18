"""Freeze existing 100x3 cases; replay independently against the owned Ollama server."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts import eval_local_ollama as local
from scripts.eval_local_ollama import MODELS, CaptureClient, api, digest, dump, followup_metrics
from rag_chatbot.graph.nodes.slot_parser import parse_slots
from rag_chatbot.graph.nodes.slot_completeness_gate import check_slot_completeness
from rag_chatbot.graph.llm_gateway import generate_followup_question
from rag_chatbot.light_followup import respond_to_policy_question
from rag_chatbot.llm.ollama import OllamaClient

AS_OF = date(2026, 9, 16)
BASE_URL = "http://127.0.0.1:11435"
OPTIONS = dict(num_ctx=8192, num_predict=1024, temperature=0, top_p=1, top_k=0,
               repeat_penalty=1, presence_penalty=0, frequency_penalty=0,
               seed=42, num_batch=128)
PROTOCOL = "experiments/model_evaluation/local_300_20260916/PROTOCOL.md"
PREVIOUS = {key: f"experiments/model_evaluation/local_20260915/team_final/{key}_metadata.json"
            for key in MODELS}
FROZEN = ("cases.json", "scenarios.json", "policies.json", "dataset_review.json", "rubric.json")


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def source_hashes():
    paths = {ROOT / PROTOCOL, ROOT / "data/evaluation/light_followup_policies.json",
             *(ROOT / name for name in PREVIOUS.values()),
             *(ROOT / "scripts" / name for name in (
                 "eval_300_ollama.py", "eval_local_ollama.py", "prepare_300_questions.py", "start_300_ollama.ps1"))}
    for folder in ("src", "rag_design"):
        paths.update(p for p in (ROOT / folder).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and
                     (p.suffix == ".py" or ("prompts" in p.parts and p.suffix in (".txt", ".md", ".jinja2", ".json"))))
    return {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sorted(paths)}


def git_commit():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def validate_cases(output):
    cases, scenarios, policies = (read(output / name) for name in FROZEN[:3])
    if not isinstance(cases, list) or len(cases) != 300:
        raise ValueError("Expected 300 cases")
    if not isinstance(scenarios, list) or len(scenarios) != 100:
        raise ValueError("Expected 100 scenarios")
    scenario_map = {s.get("situation_id", s.get("id")): s for s in scenarios}
    if len(scenario_map) != 100 or None in scenario_map:
        raise ValueError("Duplicate/missing scenario IDs")
    if len({c["id"] for c in cases}) != 300:
        raise ValueError("Duplicate case IDs")
    if Counter(c["situation_id"] for c in cases) != Counter({s: 3 for s in scenario_map}):
        raise ValueError("Expected exactly three cases per scenario")
    for case in cases:
        scenario = scenario_map[case["situation_id"]]
        if (case["policy_id"] != scenario["policy_id"] or
                case["policy"] != policies[case["policy_id"]]):
            raise ValueError("Case/scenario/frozen policy mismatch")
        if case["id"] not in {case["situation_id"] + "-" + suffix for suffix in "ABC"}:
            raise ValueError("Unexpected case ID")
        if case["expected_kind"] not in ("answer", "guidance") or case["suite"] != "expanded_followup":
            raise ValueError("Unexpected case kind/suite")
        for field in ("category", "style", "question", "facts", "required_points", "forbidden_points",
                      "missing_info", "reference_quotes", "reference_answer", "n1_review"):
            if field not in case:
                raise ValueError(f"Missing case field: {field}")
    return cases


def prepare(output):
    if (output / "manifest.json").exists():
        raise FileExistsError("Manifest already exists; never overwrite frozen inputs")
    cases = validate_cases(output)
    expected = {}
    for key, name in PREVIOUS.items():
        metadata = read(ROOT / name)
        if metadata["model"] != MODELS[key] or not metadata.get("digest"):
            raise ValueError("Previous model metadata mismatch")
        expected[key] = {"digest": metadata["digest"], "details": metadata["details"],
                         "version": metadata["ollama"]["version"]}
    order = sorted(c["id"] for c in cases)
    random.Random(42).shuffle(order)
    manifest = dict(prepared_at=now(), as_of=AS_OF.isoformat(), seed=42, git_commit=git_commit(),
                    models=MODELS, generation=OPTIONS, disable_thinking=True,
                    timeout_seconds=600, expected=expected, order=order, case_count=300,
                    source_sha256=source_hashes(),
                    frozen_sha256={name: digest((output / name).read_bytes()) for name in FROZEN})
    dump(output / "manifest.json", manifest, exclusive=True)


def validate_manifest(output):
    raw = (output / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    for root, hashes in ((ROOT, manifest["source_sha256"]), (output, manifest["frozen_sha256"])):
        for name, expected in hashes.items():
            if digest((root / name).read_bytes()) != expected:
                raise ValueError(f"Frozen hash changed: {name}")
    if (manifest["models"] != MODELS or manifest["generation"] != OPTIONS or
            manifest["as_of"] != AS_OF.isoformat() or manifest["seed"] != 42):
        raise ValueError("Frozen execution settings changed")
    cases = validate_cases(output)
    order = sorted(c["id"] for c in cases)
    random.Random(42).shuffle(order)
    if manifest["order"] != order or set(manifest["frozen_sha256"]) != set(FROZEN):
        raise ValueError("Frozen order/input list mismatch")
    if manifest["source_sha256"] != source_hashes():
        raise ValueError("Source inventory changed")
    return manifest, digest(raw), {c["id"]: c for c in cases}


def understand(question, client=None):
    state = {"user_input": question, "slots": {}, "as_of": AS_OF}
    result = parse_slots(state, llm_client=client)
    missing = check_slot_completeness({**state, **result})["missing_slots"]
    return dict(slots=result["slots"], missing_slots=missing,
                followup=generate_followup_question(0, missing) if missing else None)


def evaluate(case, client):
    row = {k: case[k] for k in ("id", "situation_id", "category", "style", "question", "policy_id")}
    row["started_at"] = now()
    baseline = understand(case["question"])
    client.calls = []
    started = time.perf_counter()
    n1 = understand(case["question"], client)
    row["n1"] = dict(**n1, baseline=baseline, calls=list(client.calls),
                     elapsed_s=time.perf_counter() - started)
    client.calls = []
    started = time.perf_counter()
    final = respond_to_policy_question(deepcopy(case["policy"]), case["question"], llm_client=client)
    metrics = followup_metrics(case, final, client.calls)
    row["detail"] = {key: metrics[key] for key in ("final", "generation", "quotes_valid", "outcome_reason")}
    row["detail"].update(calls=list(client.calls), elapsed_s=time.perf_counter() - started)
    row["transport_failure"] = any(transport_failure(c) for track in ("n1", "detail")
                                   for c in row[track]["calls"])
    return row


def transport_failure(call):
    error = call.get("error", "")
    return bool(error and (not call.get("done") and call.get("done_reason") != "length")
                and "empty or invalid content" not in error)


def completed_rows(path, order, identity, resume):
    if not path.exists():
        if resume:
            raise ValueError("Resume requires an existing results file")
        return []
    if not resume:
        raise FileExistsError("Results exist; explicit --resume required")
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Incomplete JSONL tail; report to parent, never truncate")
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
            if (row["id"] != order[len(rows)] or
                    any(row[k] != v for k, v in identity.items()) or
                    not all(isinstance(row[k], dict) for k in ("n1", "detail")) or
                    not all(k in row for k in ("started_at", "running_models", "physical_gpu", "transport_failure"))):
                raise ValueError("Result order/identity/schema mismatch")
            for track, required in (("n1", ("slots", "missing_slots", "followup", "baseline", "calls", "elapsed_s")),
                                    ("detail", ("final", "generation", "quotes_valid", "outcome_reason", "calls", "elapsed_s"))):
                if not all(k in row[track] for k in required):
                    raise ValueError("Incomplete result row")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("Malformed/duplicate/partial result; report to parent") from exc
        rows.append(row)
    return rows


def write_row(handle, row):
    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def physical_gpu():
    try:
        return subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                                        "--format=csv,noheader"], text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def running(model):
    models = api("/api/ps").get("models", [])
    if any(m.get("name", m.get("model")) != model for m in models):
        raise ValueError("Other model present on owned server; refusing to run/unload it")
    return models


def run(model_key, output, resume=False):
    # A crash leaves an explicit lock for the parent to inspect; never steal it.
    lock = output / "active_run.lock"
    with lock.open("x", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "model": model_key, "started_at": now()}, handle)
    try:
        return _run(model_key, output, resume)
    finally:
        lock.unlink()


def _run(model_key, output, resume=False):
    if os.environ.get("OLLAMA_BASE_URL") != BASE_URL:
        raise ValueError(f"OLLAMA_BASE_URL must be exactly {BASE_URL}")
    local.BASE_URL = BASE_URL  # Reused api resolves this module global; no old file changes.
    manifest, manifest_hash, cases = validate_manifest(output)
    model = MODELS[model_key]
    expected = manifest["expected"][model_key]
    identity = dict(model=model, model_digest=expected["digest"], manifest_sha256=manifest_hash)
    directory = output / "runs"
    destination = directory / f"{model_key}.jsonl"
    rows = completed_rows(destination, manifest["order"], identity, resume)
    starts = sorted(directory.glob(f"{model_key}_session_*_start.json"))
    for path in starts:
        previous = read(path)
        if any(previous.get(k) != v for k, v in identity.items()):
            raise ValueError("Previous session identity mismatch")
    if resume and not starts:
        raise ValueError("Resume requires session identity metadata")
    if len(rows) == len(cases):
        print(json.dumps({"model": model_key, "completed": len(rows), "remaining": 0}))
        return
    before = running(model)
    tags = [t for t in api("/api/tags")["models"] if t.get("name") == model]
    show = api("/api/show", {"model": model})
    version = api("/api/version")
    if (len(tags) != 1 or tags[0].get("digest") != expected["digest"] or
            show.get("details") != expected["details"] or
            show.get("details", {}).get("quantization_level") != "Q4_K_M" or
            version.get("version") != expected["version"]):
        raise ValueError("Installed model digest/details/version mismatch")
    directory.mkdir(parents=True, exist_ok=True)
    number = max((int(p.name.split("_session_")[1].split("_")[0]) for p in starts), default=0) + 1
    stem = f"{model_key}_session_{number:03d}"
    metadata = dict(**identity, started_at=now(), options=OPTIONS, details=show["details"],
                    ollama=version, running_models_before=before, completed_before=len(rows))
    dump(directory / f"{stem}_start.json", metadata, exclusive=True)
    client = CaptureClient(OllamaClient(model=model, base_url=BASE_URL, timeout_seconds=600,
                                        disable_thinking=True, **OPTIONS))
    scored_start = None
    loaded = False
    try:
        with destination.open("a" if resume else "x", encoding="utf-8", newline="\n") as handle:
            loaded = True
            try:
                warmup = client.complete("준비되었다고 짧게 답하세요.", max_tokens=32)
                if not warmup.strip():
                    raise ValueError("Empty warmup content")
            finally:
                metadata["warmup"] = dict(scored=False, max_tokens=32, calls=list(client.calls))
            metadata["running_models_after_warmup"] = running(model)
            scored_start = time.perf_counter()
            for case_id in manifest["order"][len(rows):]:
                running(model)
                row = evaluate(cases[case_id], client)
                row.update(identity)
                try:
                    row["running_models"] = running(model)
                except Exception as exc:
                    row["running_models"] = None
                    row["sampling_error"] = type(exc).__name__
                    row["transport_failure"] = True
                row["physical_gpu"] = physical_gpu()
                if row["transport_failure"]:
                    # Preserve the failed attempt separately. Resume retries this
                    # case while the completed-row prefix remains immutable.
                    failure_path = directory / f"{stem}_transport_failure.jsonl"
                    with failure_path.open("x", encoding="utf-8", newline="\n") as failure:
                        write_row(failure, row)
                    raise RuntimeError("Transport failure attempt saved; halted for parent")
                write_row(handle, row)
                rows.append(row)
                print(f"{model_key}: {len(rows)}/300 {case_id}", flush=True)
    except BaseException as exc:
        metadata["termination"] = type(exc).__name__
        raise
    finally:
        metadata["scored_elapsed_s"] = time.perf_counter() - scored_start if scored_start is not None else 0
        metadata["completed_after"] = len(rows)
        metadata["finished_at"] = now()
        try:
            if loaded:
                api("/api/generate", {"model": model, "keep_alive": 0})
                metadata["running_models_after_unload"] = api("/api/ps").get("models", [])
        except Exception as exc:
            metadata["unload_error"] = type(exc).__name__
        finally:
            dump(directory / f"{stem}.json", metadata, exclusive=True)
    print(json.dumps({"model": model_key, "completed": len(rows), "remaining": 300 - len(rows),
                      "transport_failures": sum(r["transport_failure"] for r in rows)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--model", choices=MODELS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.prepare and args.resume:
        parser.error("--resume requires --model")
    prepare(args.output) if args.prepare else run(args.model, args.output, args.resume)


if __name__ == "__main__":
    main()
