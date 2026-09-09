"""Compare archived N4 revisions with frozen profiles; never run the service/LLM.

Example: python -B scripts/evaluate_n4.py --manifest INPUT_FREEZE --output-dir NEW_DIR
The output directory is create-only. --smoke is a separate, non-benchmark check.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISIONS = {"A": "d699d6718fb254ef4d5f7255948cd1b64430ae64",
             "B": "5358e191d2676aee4d14f1a60cee34cc86ec9c16"}
INPUT_SHA = "507472db72172ec3446642a4d371c7de82f880c7326d98d09332247aa44e41a4"
MODEL_COMMIT = "d128750597153bb5987e10b1c3493a34e5a4502a"
MODEL_NAME = "intfloat/multilingual-e5-base"
ORDER = list("ABBAAB")
METRIC_LABEL = "given frozen profile original designated policy recall; not eligibility"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def read_partial(path):
    """Keep flushed rows even if termination interrupts the last JSONL write."""
    rows = []
    if not Path(path).exists():
        return rows, False
    with Path(path).open("rb") as stream:
        for line in stream:
            try:
                rows.append(json.loads(line))
            except (ValueError, UnicodeError):
                return rows, True
    return rows, False


def file_hashes(root):
    return {str(p.relative_to(root)): sha256(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}


def verify_files(root, expected):
    actual = file_hashes(Path(root))
    require(actual == expected, "Frozen file inventory/hash mismatch")
    return actual


def copy_sidecar(source, destination, expected_hash):
    require(Path(source).resolve() != Path(destination).resolve(), "Sidecar copy must be isolated")
    with Path(source).open("rb") as reader, Path(destination).open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    require(sha256(destination) == expected_hash, "Copied sidecar hash mismatch")
    return Path(destination).resolve()


def quantile(values, q):
    """Type 7, with no rounding, trimming or nearest-rank substitution."""
    require(0 <= q <= 1, "Invalid quantile")
    if not values:
        return None
    ordered = sorted(values)
    require(all(math.isfinite(x) and x >= 0 for x in ordered), "Invalid latency")
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def score(expected, retrieved):
    gold = set(expected)
    require(bool(gold), "Primary case needs designated policy")
    top = retrieved[:5]
    return (len(gold.intersection(top)) / len(gold),
            next((1 / rank for rank, item in enumerate(top, 1) if item in gold), 0.0))


def summarize(rows, cases):
    by_id = {c["question_id"]: c for c in cases}
    values = [score(by_id[r["question_id"]]["expected_policy_ids"],
                    [] if r["error"] else r["policy_ids"]) for r in rows]
    latencies = [r["latency_ms"] for r in rows if not r["error"]]
    return {"observations": len(rows), "distinct_questions": len({r["question_id"] for r in rows}),
            "successful": len(latencies), "errors": sum(bool(r["error"]) for r in rows),
            "recall_at_5": statistics.mean(v[0] for v in values) if values else None,
            "mrr_at_5": statistics.mean(v[1] for v in values) if values else None,
            "p50_ms": quantile(latencies, .5), "p95_ms": quantile(latencies, .95),
            "latency_samples": len(latencies), "latency_scope": "successful calls only; diagnostic if incomplete"}


def aggregate(rounds, cases):
    result = {"metric_label": METRIC_LABEL, "revisions": {}}
    groups = {}
    for case in cases:
        labels = [f"{key}={value}" for key, value in case["strata"].items()]
        labels += [f"quality_flag={flag}" for flag in case["quality_flags"]]
        labels += ["label_validity=" + json.dumps(case["label_validity"], sort_keys=True, ensure_ascii=False)]
        for label in labels:
            groups.setdefault(label, set()).add(case["question_id"])
    for revision in REVISIONS:
        selected = [r for r in rounds if r["revision"] == revision]
        rows = [row for r in selected for row in r["rows"]]
        per_round = [dict(round=r["round"], **summarize(r["rows"], cases)) for r in selected]
        total = summarize(rows, cases)
        total["per_round"] = per_round
        for metric in ("recall_at_5", "mrr_at_5"):
            scores = [r[metric] for r in per_round]
            total[metric] = statistics.mean(scores)
            total[metric + "_range"] = [min(scores), max(scores)]
        total["groups_overlapping"] = {label: summarize([r for r in rows if r["question_id"] in ids], cases)
                                        for label, ids in groups.items()}
        result["revisions"][revision] = total
    return result


def complete_rows(partial, cases, reason):
    known = {c["question_id"] for c in cases}
    seen = {}
    for row in partial:
        require(row["question_id"] in known and row["question_id"] not in seen, "Unexpected/duplicate result ID")
        require(isinstance(row["policy_ids"], list), "Invalid policy IDs")
        if not row["error"]:
            quantile([row["latency_ms"]], .5)
        seen[row["question_id"]] = row
    require(list(seen) == [c["question_id"] for c in cases][:len(seen)], "Result order changed")
    return [seen.get(c["question_id"], {"question_id": c["question_id"], "policy_ids": [],
            "latency_ms": None, "error": reason, "censored": True}) for c in cases]


def load_inputs(path):
    freeze = read_json(path)
    require(freeze["revisions"] == REVISIONS and freeze["round_order"] == ORDER, "Revision/protocol mismatch")
    require(freeze["model_revision"] == MODEL_COMMIT, "Model revision mismatch")
    require(freeze["n"] == 95 and freeze["excluded_n"] == 5 and freeze["top_k"] == 5,
            "Dataset dimensions changed")
    require(freeze["cpu_threads"] == 4 and freeze["workers"] == 1, "CPU protocol changed")
    require(sha256(freeze["input_path"]) == freeze["input_file_sha256"] == INPUT_SHA, "Input hash mismatch")
    require(sha256(freeze["original_data_path"]) == freeze["original_data_sha256"], "Original Dev changed")
    require(sha256(freeze["sidecar_path"]) == freeze["sidecar_sha256"], "Sidecar changed")
    for name, key in (("source-audit.json", "source_audit_sha256"),
                      ("excluded-dev.jsonl", "excluded_sha256"), ("input-audit.md", "audit_note_sha256")):
        require(sha256(Path(path).parent / name) == freeze[key], "Source audit changed")
    cases = read_rows(freeze["input_path"])
    require(len(cases) == 95 and len({c["question_id"] for c in cases}) == 95, "Invalid cases")
    require([c["question_id"] for c in cases] == freeze["case_order"], "Case order changed")
    require(len(freeze["warmup_ids"]) == len(set(freeze["warmup_ids"])) == 5 and
            set(freeze["warmup_ids"]) <= set(freeze["case_order"]), "Invalid warmup overlap")
    for case in cases:
        require(case["expected_policy_ids"] and case["n4_input"]["query_id"] == case["question_id"], "Invalid case contract")
        require(case["n4_input"]["as_of"] == freeze["as_of"] and
                case["n4_input"]["policy_top_k"] == 5, "N4 state protocol mismatch")
    return freeze, cases


def archive_revision(revision, destination):
    archive = destination.with_suffix(".zip")
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive",
                    "--format=zip", "--output", str(archive), revision], cwd=ROOT, check=True)
    tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", revision], cwd=ROOT).split(b"\0")
    blobs = {}
    for entry in filter(None, tree):
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        require(kind == "blob" and mode in ("100644", "100755"), "Unsupported archive entry")
        blobs[name.decode()] = oid
    destination.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        for name, oid in blobs.items():
            target = destination / name
            require(target.resolve().is_relative_to(destination.resolve()), "Unsafe archive path")
            data = zipped.read(name)
            require(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == oid,
                    "Archive differs from git blob")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return {"revision": revision, "archive_sha256": sha256(archive), "git_blobs": blobs,
            "files_sha256": file_hashes(destination)}


def verify_imports(archive, files):
    observed = {}
    for name, module in list(sys.modules.items()):
        if name.split(".")[0] not in {"rag_design", "rag_chatbot", "src"}:
            continue
        filename = getattr(module, "__file__", None)
        if filename is None:
            continue
        path = Path(filename).resolve()
        require(path.is_relative_to(archive), "Project import escaped archive")
        relative = str(path.relative_to(archive))
        require(relative in files and sha256(path) == files[relative], "Imported source hash mismatch")
        observed[name] = relative
    require("rag_chatbot.graph.nodes.policy_search" in observed, "Actual N4 was not imported")
    return observed


def model_proof(provider, freeze):
    from huggingface_hub import hf_hub_download
    snapshot = Path(freeze["model_path"]).resolve()
    cache = snapshot.parents[2]
    ref = snapshot.parent.parent / "refs/main"
    require(ref.read_text().strip() == MODEL_COMMIT, "Cache ref mismatch before load")
    model = provider._load()
    transformer = model[0]
    require(transformer.auto_model.config._commit_hash == MODEL_COMMIT, "Loaded model commit unproven")
    require(str(transformer.auto_model.device) == "cpu", "Loaded model is not CPU")
    resolved = {}
    for filename, expected in freeze["model_files_sha256"].items():
        path = Path(hf_hub_download(MODEL_NAME, filename.replace("\\", "/"),
                    revision=MODEL_COMMIT, cache_dir=str(cache), local_files_only=True))
        require(path.resolve() == (snapshot / filename).resolve() and sha256(path) == expected,
                "Model resolved file mismatch")
        resolved[filename] = {"path": str(path), "sha256": expected}
    require(ref.read_text().strip() == MODEL_COMMIT, "Cache ref changed during load")
    return {"actual_commit": transformer.auto_model.config._commit_hash,
            "model_name": provider.model_name, "provider_id": provider.provider_id,
            "device": str(transformer.auto_model.device), "files": resolved}


def child(job_path):
    started = time.perf_counter()
    job = read_json(job_path)
    out = Path(job_path).parent
    freeze, cases = load_inputs(job["freeze_path"])
    require(sha256(job["freeze_path"]) == job["freeze_sha256"], "Freeze changed")
    archive = Path(job["archive"]).resolve()
    verify_files(archive, job["archive_files"])
    # Keep installed dependencies, but never resolve project imports from the shared checkout.
    sys.path = [str(archive), str(archive / "src")] + [p for p in sys.path
                if p and not Path(p).resolve().is_relative_to(ROOT)]
    sys.path.append(str(ROOT / ".venv/Lib/site-packages"))
    def blocked(*args, **kwargs):
        raise RuntimeError("NETWORK_DISABLED")
    socket.socket.connect = blocked
    socket.create_connection = blocked
    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    from rag_design.embeddings import SentenceTransformerKoreanProvider
    from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig
    from rag_design.contracts import SourceType
    from rag_chatbot.graph.nodes.policy_search import search_policies
    from rag_chatbot.graph.policy_conditions import load_support_conditions
    timings = {"imports_and_archive_check_seconds": time.perf_counter() - started}
    t = time.perf_counter()
    provider = SentenceTransformerKoreanProvider(device="cpu", workers=1, local_files_only=True)
    proof = model_proof(provider, freeze)
    timings["model_load_and_proof_seconds"] = time.perf_counter() - t
    t = time.perf_counter()
    db = out / "vector_db"
    require(not db.exists() and db.resolve() != Path(freeze["index_path"]).resolve(), "Unsafe DB path")
    shutil.copytree(freeze["index_path"], db)
    verify_files(db, freeze["index_files_sha256"])
    timings["index_copy_and_hash_seconds"] = time.perf_counter() - t
    t = time.perf_counter()
    store = ChromaVectorStore(provider, VectorStoreConfig(persist_directory=db, collection_prefix="bokji_rag"))
    require(store.config.persist_directory.resolve() == db.resolve(), "Store path mismatch")
    fingerprint = store.collection_fingerprint(SourceType.SUBSIDY)
    timings["store_connect_seconds"] = time.perf_counter() - t
    t = time.perf_counter()
    sidecar = copy_sidecar(freeze["sidecar_path"], out / "support_conditions.json", freeze["sidecar_sha256"])
    conditions = load_support_conditions(sidecar)
    require(bool(conditions), "Support conditions unavailable")
    timings["sidecar_load_seconds"] = time.perf_counter() - t
    imports = verify_imports(archive, job["archive_files"])
    write_json(out / "runtime.json", {"model": proof, "imports": imports, "timings": timings,
               "db_path": str(db.resolve()), "collection_fingerprint": fingerprint,
               "sidecar_records": len(conditions), "sidecar_path": str(sidecar),
               "sidecar_sha256": sha256(sidecar), "torch_threads": torch.get_num_threads()})
    by_id = {c["question_id"]: c for c in cases}
    errors = 0
    with (out / "partial.jsonl").open("x", encoding="utf-8") as stream:
        for phase, ids in (("warmup", freeze["warmup_ids"]), ("measured", job["case_ids"])):
            for query_id in ids:
                state = copy.deepcopy(by_id[query_id]["n4_input"])
                state["as_of"] = date.fromisoformat(state["as_of"])
                row = {"phase": phase, "question_id": query_id, "policy_ids": [], "error": None}
                t = time.perf_counter()
                try:
                    result = search_policies(state, store, top_k=5, support_conditions=conditions)
                    elapsed = time.perf_counter() - t
                    row["policy_ids"] = [r.chunk.metadata["source_id"] for r in result["subsidy_chunks"]]
                    row["chunk_ids"] = [r.chunk.chunk_id for r in result["subsidy_chunks"]]
                except Exception as exc:
                    elapsed = time.perf_counter() - t
                    row["error"] = type(exc).__name__
                    errors += 1
                row["latency_ms"] = elapsed * 1000
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(phase, query_id, row["error"] or "ok", flush=True)
    verify_imports(archive, job["archive_files"])
    require((Path(freeze["model_path"]).parent.parent / "refs/main").read_text().strip() == MODEL_COMMIT,
            "Model ref changed after calls")
    provider.close()
    return int(bool(errors))


def run(args):
    out = args.output_dir.resolve()
    freeze, cases = load_inputs(args.manifest)
    require(out.is_relative_to(args.manifest.resolve().parent) and
            not out.is_relative_to(ROOT), "Outputs must be under the external report root")
    out.mkdir(parents=True, exist_ok=False)
    frozen_sha = sha256(args.manifest)
    manifest = {"command": [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]],
                "classification": "smoke_not_benchmark" if args.smoke else METRIC_LABEL,
                "freeze_path": str(args.manifest.resolve()), "freeze_sha256": frozen_sha,
                "freeze": freeze, "harness_sha256": sha256(__file__),
                "order": list("AB") if args.smoke else ORDER, "timeout_seconds_per_child": args.timeout_seconds,
                "quantiles": "type7: position=(n-1)*q", "warmup_overlap": True,
                "python": sys.version, "packages": {name: importlib.metadata.version(name) for name in
                    ("torch", "transformers", "sentence-transformers", "chromadb", "numpy")}}
    write_json(out / "manifest.json", manifest)
    verify_files(Path(freeze["index_path"]), freeze["index_files_sha256"])
    verify_files(Path(freeze["model_path"]), freeze["model_files_sha256"])
    archives = {}
    for label, revision in REVISIONS.items():
        archives[label] = archive_revision(revision, out / label)
    write_json(out / "archives.json", archives)
    measured_cases = cases[:1] if args.smoke else cases
    rounds = []
    halted = False
    for number, label in enumerate(manifest["order"], 1):
        round_dir = out / f"round-{number}-{label}"
        round_dir.mkdir()
        job = {"freeze_path": str(args.manifest.resolve()), "freeze_sha256": frozen_sha,
               "archive": str(out / label), "archive_files": archives[label]["files_sha256"],
               "case_ids": [c["question_id"] for c in measured_cases]}
        write_json(round_dir / "job.json", job)
        env = dict(os.environ)
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONPATH="",
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                   SENTENCE_TRANSFORMERS_HOME=str(Path(freeze["model_path"]).parents[2]),
                   HF_HOME=str(round_dir / "cache"), TORCH_HOME=str(round_dir / "torch"),
                   ANONYMIZED_TELEMETRY="False", TOKENIZERS_PARALLELISM="false", CUDA_VISIBLE_DEVICES="",
                   OMP_NUM_THREADS="4", MKL_NUM_THREADS="4", TEMP=str(round_dir / "tmp"), TMP=str(round_dir / "tmp"))
        (round_dir / "tmp").mkdir()
        rc, reason = 1, "not_started_after_failure"
        started = time.perf_counter()
        if not halted:
            with (round_dir / "run.log").open("x", encoding="utf-8") as log:
                try:
                    rc = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                           "--child", str(round_dir / "job.json")], cwd=out / label, env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout_seconds).returncode
                    reason = "not_completed_child_exit"
                except subprocess.TimeoutExpired:
                    rc, reason = 124, "not_completed_timeout"
        partial_path = round_dir / "partial.jsonl"
        partial, corrupt = read_partial(partial_path)
        if corrupt:
            rc, reason = 1, "corrupt_partial_jsonl"
        try:
            require(all(r["phase"] in ("warmup", "measured") for r in partial), "Invalid result phase")
            rows = complete_rows([r for r in partial if r["phase"] == "measured"], measured_cases, reason)
            warmups = [r for r in partial if r["phase"] == "warmup"]
        except (KeyError, TypeError, ValueError):
            rc, reason = 1, "invalid_result_stream"
            rows = complete_rows([], measured_cases, reason)
            warmups = []
        warmup_ok = ([r["question_id"] for r in warmups] == freeze["warmup_ids"] and
                     not any(r["error"] for r in warmups))
        success = rc == 0 and warmup_ok and not any(r["error"] for r in rows)
        record = {"round": number, "revision": label, "exit_code": rc, "complete": success,
                  "wall_seconds": time.perf_counter() - started, "warmup_rows": warmups, "rows": rows}
        write_json(round_dir / "results.json", record)
        rounds.append(record)
        halted = halted or not success
        print(f"round {number} {label}: success={success}, measured={len(rows)}", flush=True)
    summary = aggregate(rounds, measured_cases)
    summary.update(complete=all(r["complete"] for r in rounds), smoke=args.smoke,
                   benchmark_accepted=(not args.smoke and all(r["complete"] for r in rounds)),
                   required_measurements=2 if args.smoke else 570,
                   excluded_original_questions=5, unique_designated_policies=len({p for c in cases for p in c["expected_policy_ids"]}))
    try:
        verify_files(Path(freeze["index_path"]), freeze["index_files_sha256"])
        verify_files(Path(freeze["model_path"]), freeze["model_files_sha256"])
        require(sha256(args.manifest) == frozen_sha, "Freeze changed during run")
        load_inputs(args.manifest)
        summary["source_integrity_unchanged"] = True
    except ValueError:
        summary.update(source_integrity_unchanged=False, complete=False, benchmark_accepted=False)
    write_json(out / "summary.json", summary)
    return 0 if summary["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.child)
    if args.manifest is None or args.output_dir is None or args.timeout_seconds <= 0:
        parser.error("--manifest, --output-dir and a positive timeout are required")
    existed = args.output_dir.exists()
    try:
        return run(args)
    except Exception as exc:
        # A preflight failure must also leave explicit denominators, never a success-shaped empty run.
        if not existed and args.output_dir.is_dir():
            write_json(args.output_dir / "orchestration-error.json", {"complete": False,
                       "error": type(exc).__name__, "required_measurements": 2 if args.smoke else 570,
                       "note": "Preflight/orchestration failed; retained round files are authoritative."})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
