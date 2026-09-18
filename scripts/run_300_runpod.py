"""Use the frozen local service code with Ollama reached through an SSH tunnel."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_300_guarded as guarded


def main():
    # Keep all generation, scoring and frozen service code unchanged. The
    # runner's local nvidia-smi would measure the client PC, so omit that field
    # and preserve the separate remote GPU monitor instead.
    output = Path(sys.argv[sys.argv.index('--output') + 1])
    snapshot = output / 'runpod_adapter_snapshot.py'
    payload = Path(__file__).read_bytes()
    if snapshot.exists():
        if snapshot.read_bytes() != payload:
            raise ValueError('RunPod adapter changed')
    else:
        with snapshot.open('xb') as f:
            f.write(payload)
    record = dict(adapter_sha256=hashlib.sha256(payload).hexdigest(),
                  inference_location='RunPod', transport='SSH loopback tunnel',
                  physical_gpu_field=None, gpu_evidence='remote_gpu_monitor.csv',
                  service_code_location='original local Python environment')
    record_path = output / 'runpod_adapter_profile.json'
    if record_path.exists():
        if json.loads(record_path.read_text(encoding='utf-8')) != record:
            raise ValueError('RunPod adapter profile changed')
    else:
        with record_path.open('x', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.write('\n')
    guarded.runner.physical_gpu = lambda: None
    guarded.main()


if __name__ == '__main__':
    main()
