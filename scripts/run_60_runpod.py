"""Run the frozen service on the preselected 60-case RunPod subset."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts.eval_300_ollama import *
from scripts import eval_300_ollama as frozen


def validate_manifest(output):
    manifest, mh, original = frozen.validate_manifest(output)
    selection = read(output / "selection.json")
    if selection["source_manifest_sha256"] != mh:
        raise ValueError("Selection source differs")
    for name, expected in selection["selected_sha256"].items():
        if digest((output / name).read_bytes()) != expected:
            raise ValueError("Selected inputs changed")
    subset = read(output / "selected_cases.json")
    cases = {c["id"]: c for c in subset}
    if len(cases) != 60 or any(original[i] != c for i, c in cases.items()):
        raise ValueError("Subset is not 60 unchanged original cases")
    order = [i for i in manifest["order"] if i in cases]
    if selection["ordered_ids"] != order:
        raise ValueError("Subset order differs")
    manifest = dict(manifest, order=order, case_count=60)
    return manifest, mh, cases


class GPUClient(OllamaClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.options["num_gpu"] = 99


OllamaClient = GPUClient
physical_gpu = lambda: None
_runtime = None
_pending_alert = None


def evaluate(case, client):
    global _pending_alert
    if _pending_alert:
        raise RuntimeError("Repetition symptom preserved; halt before next case")
    row = frozen.evaluate(case, client)
    row["runtime_profile"] = _runtime
    repeated = [{"track": track, "call": index} for track in ("n1", "detail")
                for index, call in enumerate(row[track]["calls"])
                if call.get("raw", "").count("Fleet") > 50]
    if repeated:
        row["runtime_alert"] = {"reason": "Known repeated-token symptom recurred", "calls": repeated}
        _pending_alert = case["id"]
    return row


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
                print(f"{model_key}: {len(rows)}/60 {case_id}", flush=True)
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
    print(json.dumps({"model": model_key, "completed": len(rows), "remaining": 60 - len(rows),
                      "transport_failures": sum(r["transport_failure"] for r in rows)}))


def main():
    global _runtime
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", choices=MODELS, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    manifest, mh, cases = validate_manifest(args.output)
    server_path = args.output / "server_runpod.json"
    server = read(server_path)
    if server["host"] != "127.0.0.1:11435" or server["backend"] != "cuda_v13":
        raise ValueError("Wrong owned GPU server")
    sh = digest((args.output / "selection.json").read_bytes())
    payload = Path(__file__).read_bytes()
    profile = dict(model=MODELS[args.model], backend=server["backend"],
        runtime_environment=server.get("runtime_environment", {}),
        reload_each_question=False, num_gpu=99, generation=manifest["generation"],
        disable_thinking=True, manifest_sha256=mh, selection_sha256=sh,
        wrapper_sha256=digest(payload), inference_location="RunPod",
        server_record_sha256=digest(server_path.read_bytes()))
    profile_path = args.output / f"{args.model}_runtime_profile.json"
    dump(profile_path, profile, exclusive=True)
    snapshot = args.output / "runtime_wrapper_snapshot.py"
    if snapshot.exists():
        if snapshot.read_bytes() != payload:
            raise ValueError("Wrapper snapshot differs")
    else:
        with snapshot.open("xb") as f:
            f.write(payload)
    _runtime = dict(profile_sha256=digest(profile_path.read_bytes()),
        server_record=str(server_path), server_record_sha256=digest(server_path.read_bytes()),
        selection_sha256=sh)
    run(args.model, args.output)
    if _pending_alert:
        raise RuntimeError("Final case repeated-token alert requires review")


if __name__ == "__main__":
    main()
