"""Run the frozen experiment with runtime provenance and a recurrence stop."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts import eval_300_ollama as runner
from rag_chatbot.llm.ollama import OllamaClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=runner.MODELS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-record", type=Path, required=True)
    parser.add_argument("--num-gpu", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reload-each-question", action="store_true")
    args = parser.parse_args()
    manifest, mh, _ = runner.validate_manifest(args.output)
    server = runner.read(args.server_record)
    if server["host"] != "127.0.0.1:11435":
        raise ValueError("Wrong owned server")
    profile = dict(model=runner.MODELS[args.model], backend=server["backend"],
                   runtime_environment=server.get("runtime_environment", {}),
                   reload_each_question=args.reload_each_question,
                   num_gpu=args.num_gpu, generation=manifest["generation"],
                   disable_thinking=manifest["disable_thinking"], manifest_sha256=mh,
                   wrapper_sha256=runner.digest(Path(__file__).read_bytes()),
                   recurrence_stop="Preserve completed row; halt before next question if a response repeats Fleet more than 50 times",
                   purpose="Fresh comparison run after isolated CPU/GPU and original-sequence diagnostics")
    profile_path = args.output / f"{args.model}_runtime_profile.json"
    if profile_path.exists():
        if runner.read(profile_path) != profile:
            raise ValueError("Runtime profile changed; do not mix settings")
    else:
        runner.dump(profile_path, profile, exclusive=True)
    profile_hash = runner.digest(profile_path.read_bytes())
    snapshot = args.output / "runtime_wrapper_snapshot.py"
    if snapshot.exists():
        if runner.digest(snapshot.read_bytes()) != profile["wrapper_sha256"]:
            raise ValueError("Runtime wrapper snapshot differs")
    else:
        with snapshot.open("xb") as handle:
            handle.write(Path(__file__).read_bytes())
    destination = args.output / "runs" / f"{args.model}.jsonl"
    preparation_path = args.output / "runs" / f"{args.model}_preparation.jsonl"
    if not args.resume and preparation_path.exists():
        raise FileExistsError("Preparation records already exist")
    if destination.exists():
        for line in destination.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("runtime_alert") or row.get("runtime_profile", {}).get("profile_sha256") != profile_hash:
                raise ValueError("Prior rows have a runtime alert or different profile")

    class RuntimeClient(OllamaClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            if args.num_gpu is not None:
                self.options["num_gpu"] = args.num_gpu

    evaluate = runner.evaluate
    pending_alert = None
    session_questions = 0

    def guarded(case, client):
        nonlocal pending_alert, session_questions
        if pending_alert:
            raise RuntimeError(f"Repeated runtime symptom preserved at {pending_alert}; halted for investigation")
        preparation = None
        if args.reload_each_question and session_questions:
            started = time.perf_counter()
            preparation = dict(for_case=case["id"], scored=False, started_at=runner.now(),
                               profile_sha256=profile_hash, calls=[])
            try:
                runner.running(runner.MODELS[args.model])
                runner.api("/api/generate", {"model": runner.MODELS[args.model], "keep_alive": 0})
                if runner.api("/api/ps").get("models"):
                    raise RuntimeError("Model did not unload between questions")
                client.calls = []
                try:
                    answer = client.complete("준비되었다고 짧게 답하세요.", max_tokens=32)
                    if not answer.strip():
                        raise ValueError("Empty preparation response")
                finally:
                    preparation["calls"] = list(client.calls)
            except Exception as exc:
                preparation["error"] = type(exc).__name__
                raise
            finally:
                preparation["elapsed_s"] = time.perf_counter() - started
                with preparation_path.open("a", encoding="utf-8") as handle:
                    runner.write_row(handle, preparation)
        row = evaluate(case, client)
        session_questions += 1
        if preparation is not None:
            row["excluded_preparation_seconds"] = preparation["elapsed_s"]
        row["runtime_profile"] = dict(profile_sha256=profile_hash,
                    server_record=str(args.server_record), server_record_sha256=runner.digest(args.server_record.read_bytes()))
        repeated = [{"track": track, "call": index} for track in ("n1", "detail")
                    for index, call in enumerate(row[track]["calls"])
                    if call.get("raw", "").count("Fleet") > 50]
        if repeated:
            row["runtime_alert"] = {"reason": "Known repeated-token symptom recurred", "calls": repeated}
            pending_alert = case["id"]
        return row

    runner.OllamaClient = RuntimeClient
    runner.evaluate = guarded
    runner.run(args.model, args.output, resume=args.resume)
    if pending_alert:
        raise RuntimeError(f"Runtime alert in final question {pending_alert}; results require investigation")


if __name__ == "__main__":
    main()
