"""Resume frozen Qwen questions with a documented loading-only GPU adjustment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts import eval_300_ollama as runner
from rag_chatbot.llm.ollama import OllamaClient


class PartialGpuClient(OllamaClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.model != runner.MODELS["qwen"]:
            raise ValueError("This runtime adjustment is for Qwen only")
        self.options["num_gpu"] = 30


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-record", type=Path, required=True)
    args = parser.parse_args()
    manifest, manifest_hash, _ = runner.validate_manifest(args.output)
    server = runner.read(args.server_record)
    if server["backend"] != "cuda_v13" or server["host"] != "127.0.0.1:11435":
        raise ValueError("Expected the owned CUDA 13 server record")
    profile = {"model": runner.MODELS["qwen"], "num_gpu": 30,
               "generation": manifest["generation"], "backend": "cuda_v13",
               "manifest_sha256": manifest_hash,
               "adapter_sha256": runner.digest(Path(__file__).read_bytes()),
               "reason": "Initial full GPU placement failed with illegal memory access before any completed scored case. Keep generation fixed; offload 30 layers and leave projector on CPU.",
               "source": "https://github.com/ollama/ollama/blob/v0.34.0/llm/llama_server.go"}
    path = args.output / "qwen_gpu30_profile.json"
    if path.exists():
        if runner.read(path) != profile:
            raise ValueError("Existing runtime profile differs; refusing overwrite")
    else:
        runner.dump(path, profile, exclusive=True)
    profile_hash = runner.digest(path.read_bytes())
    destination = args.output / "runs/qwen.jsonl"
    for line in destination.read_text(encoding="utf-8").splitlines():
        if json.loads(line).get("runtime_profile", {}).get("profile_sha256") != profile_hash:
            raise ValueError("Completed Qwen rows used a different runtime; refusing mixed results")
    evaluate = runner.evaluate
    def annotated(case, client):
        row = evaluate(case, client)
        row["runtime_profile"] = {"profile_sha256": profile_hash,
                                  "server_record": str(args.server_record),
                                  "server_record_sha256": runner.digest(args.server_record.read_bytes())}
        return row
    runner.OllamaClient = PartialGpuClient
    runner.evaluate = annotated
    runner.run("qwen", args.output, resume=True)


if __name__ == "__main__":
    main()
