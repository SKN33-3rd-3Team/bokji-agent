"""Replay six frozen first-call prompts without changing any scored results."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts import eval_300_ollama as runner
from rag_chatbot.llm.ollama import OllamaClient


class Captured(BaseException):
    pass


class PromptProbe:
    def complete(self, prompt, *, system=None, max_tokens=None):
        self.request = (prompt, system, max_tokens)
        raise Captured()


def prompt_for(case, track, original):
    probe = PromptProbe()
    try:
        if track == "n1":
            runner.understand(case["question"], probe)
        else:
            runner.respond_to_policy_question(deepcopy(case["policy"]), case["question"], llm_client=probe)
    except Captured:
        pass
    prompt, system, maximum = probe.request
    if (runner.digest(prompt.encode()) != original["prompt_sha256"]
            or runner.digest((system or "").encode()) != original["system_sha256"]):
        raise ValueError("Reconstructed prompt differs from original")
    return prompt, system, maximum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-record", type=Path, required=True)
    parser.add_argument("--num-gpu", type=int, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Diagnostic output already exists")
    manifest, manifest_hash, cases = runner.validate_manifest(args.base)
    runner.local.BASE_URL = runner.BASE_URL
    model = manifest["models"]["ax"]
    if runner.api("/api/ps").get("models"):
        raise ValueError("Start with an unloaded diagnostic server")
    server = runner.read(args.server_record)
    if server["host"] != "127.0.0.1:11435":
        raise ValueError("Wrong diagnostic server")
    tags = runner.api("/api/tags")["models"]
    if not any(t["name"] == model and t["digest"] == manifest["expected"]["ax"]["digest"] for t in tags):
        raise ValueError("Model identity changed")
    original = {r["id"]: r for r in map(json.loads, (args.base / "runs/ax.jsonl").read_text(encoding="utf-8").splitlines())}
    second = manifest["order"][1]
    targets = [("S065-A", "n1"), ("S074-B", "detail"), ("S069-B", "detail"),
               ("S036-B", "n1"), ("S036-B", "detail"), (second, "detail")]
    requests = [(cid, track, prompt_for(cases[cid], track, original[cid][track]["calls"][0]))
                for cid, track in targets]
    options = {**manifest["generation"], "num_gpu": args.num_gpu}
    inner = OllamaClient(model=model, base_url=runner.BASE_URL, timeout_seconds=600,
                         disable_thinking=manifest["disable_thinking"], **manifest["generation"])
    inner.options["num_gpu"] = args.num_gpu
    client = runner.CaptureClient(inner)
    metadata = dict(started_at=runner.now(), model=model, manifest_sha256=manifest_hash,
                    diagnostic_script_sha256=runner.digest(Path(__file__).read_bytes()),
                    source_results_sha256=runner.digest((args.base / "runs/ax.jsonl").read_bytes()),
                    server_record=server, options=options,
                    purpose="Runtime diagnostic only; do not mix with scored 300-question results",
                    targets=[dict(id=cid, track=track) for cid, track in targets])
    runner.dump(args.output.with_suffix(".metadata.json"), metadata, exclusive=True)
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            for cid, track, (prompt, system, maximum) in requests:
                client.calls = []
                try:
                    client.complete(prompt, system=system, max_tokens=maximum)
                finally:
                    record = dict(id=cid, track=track, calls=client.calls, physical_gpu=runner.physical_gpu())
                    try:
                        record["running_models"] = runner.api("/api/ps").get("models")
                    except Exception as exc:
                        record["sampling_error"] = type(exc).__name__
                    runner.write_row(handle, record)
                call = client.calls[0]
                words = call.get("raw", "").split()
                most = Counter(words).most_common(1)
                print(json.dumps(dict(id=cid, track=track, strict_json=call.get("strict_json"),
                                      done_reason=call.get("done_reason"), tokens=call.get("eval_count"),
                                      seconds=call.get("elapsed_s"), repeated_word=most), ensure_ascii=True), flush=True)
    finally:
        finished = {"finished_at": runner.now()}
        try:
            runner.api("/api/generate", {"model": model, "keep_alive": 0})
            finished["models_after_unload"] = runner.api("/api/ps").get("models")
        except Exception as exc:
            finished["unload_error"] = type(exc).__name__
        runner.dump(args.output.with_suffix(".finished.json"), finished, exclusive=True)


if __name__ == "__main__":
    main()
