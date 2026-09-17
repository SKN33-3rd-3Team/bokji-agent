"""Check identities and one short warmup per model, without running the dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from scripts import eval_300_ollama as runner
from rag_chatbot.llm.ollama import OllamaClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--model', choices=runner.MODELS)
    args = parser.parse_args()
    manifest, manifest_hash, _ = runner.validate_manifest(args.base)
    runner.local.BASE_URL = runner.BASE_URL
    if runner.api('/api/ps').get('models'):
        raise RuntimeError('Another model is loaded')
    version = runner.api('/api/version')
    tags = {t['name']: t for t in runner.api('/api/tags')['models']}
    selected = {args.model: runner.MODELS[args.model]} if args.model else runner.MODELS
    for key, model in selected.items():
        path = args.base / f'readiness_{key}.json'
        if path.exists():
            raise FileExistsError(path)
        show = runner.api('/api/show', {'model': model})
        expected = manifest['expected'][key]
        if (tags.get(model, {}).get('digest') != expected['digest'] or
                show.get('details') != expected['details'] or version['version'] != expected['version']):
            raise ValueError(f'{key}: model or runtime identity mismatch')
        record = dict(model=model, model_digest=tags[model]['digest'], details=show['details'],
                      manifest_sha256=manifest_hash, ollama=version, started_at=runner.now(),
                      scored=False, dataset_questions_run=0, num_gpu=99, warmup_max_tokens=32,
                      disable_thinking=True, calls=[])
        client = OllamaClient(model=model, base_url=runner.BASE_URL, timeout_seconds=600,
                              disable_thinking=True, **manifest['generation'])
        client.options['num_gpu'] = 99
        capture = runner.CaptureClient(client)
        started = time.perf_counter()
        try:
            answer = capture.complete('준비되었다고 짧게 답하세요.', max_tokens=32)
            if not answer.strip():
                raise ValueError('Empty warmup')
            record['running_models'] = runner.running(model)
            if len(record['running_models']) != 1 or record['running_models'][0]['size_vram'] <= 0:
                raise ValueError('GPU model loading was not confirmed')
            record['status'] = 'ready'
        except Exception as exc:
            record['status'] = 'failed'
            record['error_type'] = type(exc).__name__
            raise
        finally:
            record['calls'] = list(capture.calls)
            record['elapsed_s'] = time.perf_counter() - started
            try:
                runner.api('/api/generate', {'model': model, 'keep_alive': 0})
                record['running_models_after_unload'] = runner.api('/api/ps').get('models', [])
            finally:
                runner.dump(path, record, exclusive=True)
        print(json.dumps({'model':key,'status':record['status'],'warmup_seconds':round(record['elapsed_s'],2)},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
