"""Recover a stalled public release download using independently checked ranges."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import time
import urllib.request

ROOT = Path('/opt/bokji-ollama-300-20260916')
TARGET = ROOT / 'ollama-linux-amd64.tar.zst'
START, TOTAL = 1272877029, 1433537033
SHA = 'cf95886728959aa09910bb34de5cca1cc5a8f68003b5597197d3f2c2d57c0804'
URL = 'https://github.com/ollama/ollama/releases/download/v0.34.0/ollama-linux-amd64.tar.zst?download=1'


def fetch(bounds):
    start, end = bounds
    path = ROOT / f'runtime_range_{start}_{end}.bin'
    if path.exists():
        if path.stat().st_size != end-start+1:
            raise ValueError('Incomplete existing range')
        return path
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL, headers={'Range':f'bytes={start}-{end}'})
            started = time.monotonic()
            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status != 206 or response.headers['Content-Range'] != f'bytes {start}-{end}/{TOTAL}':
                    raise ValueError('Unexpected server range')
                chunks = []
                while block := response.read1(65536):
                    chunks.append(block)
                    if time.monotonic()-started > 60:
                        raise TimeoutError('Range time limit reached')
                data = b''.join(chunks)
            if len(data) != end-start+1:
                raise ValueError('Incomplete range')
            with path.open('xb') as f:
                f.write(data)
            print(f'Range complete: {start}-{end}',flush=True)
            return path
        except (OSError, ValueError):
            if attempt == 2:
                raise


if __name__ == '__main__':
    if TARGET.stat().st_size != START:
        raise ValueError('Partial download size changed')
    chunk = 5*1024*1024
    bounds = []
    for start in range(START,TOTAL,chunk):
        end = min(start+chunk-1,TOTAL-1)
        if (ROOT / f'runtime_range_{start}_{end}.bin').exists():
            bounds.append((start,end))
        else:
            bounds.extend((part,min(part+1024*1024-1,end)) for part in range(start,end+1,1024*1024))
    with ThreadPoolExecutor(max_workers=16) as pool:
        paths = list(pool.map(fetch,bounds))
    digest = hashlib.sha256()
    with TARGET.open('rb') as f:
        while data := f.read(8*1024*1024):
            digest.update(data)
    for path in paths:
        digest.update(path.read_bytes())
    if digest.hexdigest() != SHA:
        raise ValueError('Combined release SHA256 differs; partial records preserved')
    with TARGET.open('ab') as f:
        for path in paths:
            f.write(path.read_bytes())
    print('Verified full public release SHA256: '+SHA,flush=True)
