"""Replay the original A.X prefix to check history-dependent runtime failures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts import eval_300_ollama as runner
from rag_chatbot.llm.ollama import OllamaClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-record", type=Path, required=True)
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--num-gpu", type=int, default=None)
    parser.add_argument("--reload-each-question", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest, mh, cases = runner.validate_manifest(args.base)
    runner.local.BASE_URL = runner.BASE_URL
    if runner.api("/api/ps").get("models"):
        raise ValueError("Diagnostic sequence requires unloaded server")
    model = manifest["models"]["ax"]
    inner = OllamaClient(model=model, base_url=runner.BASE_URL, timeout_seconds=600,
                         disable_thinking=True, **manifest["generation"])
    if args.num_gpu is not None:
        inner.options["num_gpu"] = args.num_gpu
    client = runner.CaptureClient(inner)
    runner.dump(args.output.with_suffix(".metadata.json"), dict(started_at=runner.now(),
                manifest_sha256=mh, server_record=runner.read(args.server_record),
                options=inner.options, diagnostic_script_sha256=runner.digest(Path(__file__).read_bytes()),
                reload_each_question=args.reload_each_question,
                purpose="Original execution-order diagnostic; excluded from model scores",
                order=manifest["order"][:args.count]), exclusive=True)
    finished = {}
    try:
        client.complete("준비되었다고 짧게 답하세요.", max_tokens=32)
        runner.dump(args.output.with_suffix(".warmup.json"), client.calls, exclusive=True)
        with args.output.open("x", encoding="utf-8") as handle:
            for index, cid in enumerate(manifest["order"][:args.count]):
                warmup = None
                if args.reload_each_question and index:
                    runner.api("/api/generate", {"model": model, "keep_alive": 0})
                    if runner.api("/api/ps").get("models"):
                        raise RuntimeError("Model did not unload between questions")
                    client.calls = []
                    client.complete("준비되었다고 짧게 답하세요.", max_tokens=32)
                    warmup = list(client.calls)
                row = runner.evaluate(cases[cid], client)
                if warmup is not None:
                    row["excluded_warmup"] = warmup
                row["running_models"] = runner.api("/api/ps").get("models")
                row["physical_gpu"] = runner.physical_gpu()
                runner.write_row(handle, row)
                calls = [c for t in ("n1", "detail") for c in row[t]["calls"]]
                print(json.dumps(dict(id=cid, calls=len(calls), length=sum(c.get("done_reason")=="length" for c in calls),
                     fleet=sum(c.get("raw", "").count("Fleet")>10 for c in calls),
                     errors=sum("error" in c for c in calls))), flush=True)
                if row["transport_failure"]:
                    raise RuntimeError("Diagnostic transport failure preserved")
    except BaseException as exc:
        finished["termination"] = type(exc).__name__
        raise
    finally:
        try:
            runner.api("/api/generate", {"model": model, "keep_alive": 0})
            finished["models_after_unload"] = runner.api("/api/ps").get("models")
        except Exception as exc:
            finished["unload_error"] = type(exc).__name__
        finished["finished_at"] = runner.now()
        runner.dump(args.output.with_suffix(".finished.json"), finished, exclusive=True)


if __name__ == "__main__":
    main()
