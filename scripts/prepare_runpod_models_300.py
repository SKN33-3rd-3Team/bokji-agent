"""Download the three fixed model names on the owned RunPod Ollama server."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request

ROOT = Path('/opt/bokji-ollama-300-20260916')
MODELS = {
    'qwen': 'qwen3.5:9b',
    'ax': 'hf.co/Ghiwook/A.X-4.0-Light-Q4_K_M-GGUF:Q4_K_M',
    'bllossom': 'hf.co/Bllossom/llama-3.2-Korean-Bllossom-3B-gguf-Q4_K_M:Q4_K_M',
}


def pull(item):
    key, model = item
    path = ROOT / f'pull_{key}.json'
    if path.exists():
        raise FileExistsError(path)
    record = dict(model=model, started_at=datetime.now(timezone.utc).isoformat())
    request = urllib.request.Request('http://127.0.0.1:11435/api/pull',
        data=json.dumps({'model':model,'stream':True}).encode(),
        headers={'Content-Type':'application/json'})
    last = {}
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            for line in response:
                event = json.loads(line)
                if 'error' in event:
                    raise RuntimeError(event['error'])
                stage = event.get('status')
                percent = 10 * int(10 * event.get('completed',0) / event['total']) if event.get('total') else None
                if stage not in last or last[stage] != percent:
                    print(json.dumps({'model':key,'stage':stage,'percent':percent}),flush=True)
                    last[stage] = percent
        record['last_event'] = event
        if event.get('status') != 'success':
            raise ValueError('Model pull did not report success')
        record['success'] = True
    except Exception as exc:
        record['success'] = False
        record['error'] = str(exc)
        raise
    finally:
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        with path.open('x') as handle:
            json.dump(record,handle,indent=2)


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(pull,MODELS.items()))
