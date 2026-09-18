#!/usr/bin/env bash
set -euo pipefail
task_root=/opt/bokji-ollama-300-20260916
task_cache=/workspace/bokji-ollama-300-20260916
test ! -e "$task_root/server.json"
mkdir -p "$task_root"
cd "$task_root"
if test ! -f ollama-linux-amd64.tar.zst && test -f "$task_cache/ollama-linux-amd64.verified.tar.zst"; then
    cp "$task_cache/ollama-linux-amd64.verified.tar.zst" ollama-linux-amd64.tar.zst
fi
if ! printf '%s\n' 'cf95886728959aa09910bb34de5cca1cc5a8f68003b5597197d3f2c2d57c0804  ollama-linux-amd64.tar.zst' | sha256sum -c --status; then
    curl -fsSL --retry 2 --retry-all-errors --connect-timeout 20 --speed-time 30 --speed-limit 1048576 --max-time 600 --continue-at - -o ollama-linux-amd64.tar.zst https://github.com/ollama/ollama/releases/download/v0.34.0/ollama-linux-amd64.tar.zst
fi
printf '%s\n' 'cf95886728959aa09910bb34de5cca1cc5a8f68003b5597197d3f2c2d57c0804  ollama-linux-amd64.tar.zst' | sha256sum -c -
mkdir runtime
mkdir -p "$task_cache/models"
tar --zstd -xf ollama-linux-amd64.tar.zst -C runtime
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_MODELS="$task_cache/models"
export OLLAMA_NO_CLOUD=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_FLASH_ATTENTION=false
export OLLAMA_LLM_LIBRARY=cuda_v13
export GGML_CUDA_DISABLE_GRAPHS=1
export LLAMA_ARG_CACHE_RAM=0
nohup "$task_root/runtime/bin/ollama" serve >server.stdout.log 2>server.stderr.log &
task_server_pid=$!
printf '%s\n' "$task_server_pid" > server.pid
python3 - "$task_root" "$task_server_pid" <<'PY'
import datetime, json, pathlib, subprocess, sys, time, urllib.request
root=pathlib.Path(sys.argv[1])
for _ in range(60):
    try:
        version=json.load(urllib.request.urlopen('http://127.0.0.1:11435/api/version',timeout=2))
        break
    except OSError:
        time.sleep(1)
else:
    raise RuntimeError('Ollama failed to start')
if version != {'version':'0.34.0'}:
    raise ValueError(version)
record=dict(pid=int(sys.argv[2]), backend='cuda_v13',host='127.0.0.1:11435',
    started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    executable=str(root/'runtime/bin/ollama'),ollama=version,
    models_path='/workspace/bokji-ollama-300-20260916/models',
    location='RunPod via an SSH loopback tunnel',gpu=subprocess.check_output([
        'nvidia-smi','--query-gpu=name,memory.total,memory.used,driver_version','--format=csv,noheader'],text=True).strip(),
    runtime_environment={'LLAMA_ARG_CACHE_RAM':'0'},flash_attention=False,cuda_graphs=False,
    max_loaded_models=1,parallel=1,cloud=False)
with (root/'server.json').open('x') as f: json.dump(record,f,indent=2)
print(json.dumps(record))
PY
nohup nvidia-smi --query-gpu=timestamp,name,memory.total,memory.used,utilization.gpu,temperature.gpu --format=csv,noheader -l 10 >gpu_monitor.csv 2>gpu_monitor.stderr.log &
printf '%s\n' "$!" > gpu_monitor.pid
