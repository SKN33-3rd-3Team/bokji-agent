"""Continue the approved GPU comparison once the active A.X run finishes."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'experiments/model_evaluation/local_300_20260916/comparison_runpod_60_20260916'


def main():
    plan = BASE / 'sequence_plan.json'
    with plan.open('x', encoding='utf-8') as f:
        json.dump({'order':['ax','qwen','bllossom'], 'cases_per_model':60,
                   'ax_already_running':True, 'cpu_comparison':False}, f)
    while (BASE / 'active_run.lock').exists():
        time.sleep(2)
    metadata = json.loads((BASE / 'runs/ax_session_001.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (BASE / 'runs/ax.jsonl').read_text(encoding='utf-8').splitlines()]
    if metadata.get('termination') or metadata['completed_after'] != 60 or len(rows) != 60:
        raise RuntimeError('A.X incomplete; do not start another model')
    if any(r.get('transport_failure') or r.get('runtime_alert') for r in rows):
        raise RuntimeError('A.X has a runtime failure; halt sequence')
    for model in ('qwen', 'bllossom'):
        print('STARTING ' + model, flush=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts/run_60_runpod.py'),
                        '--model', model, '--output', str(BASE)], cwd=ROOT, check=True)
    print('ALL THREE MODELS COMPLETE', flush=True)


if __name__ == '__main__':
    main()
