# LLM 비교를 다른 컴퓨터에서 다시 실행하기

이 안내는 2026-09-16에 사용한 **동일 60문장**을 세 모델에 다시 입력하는 방법이다.
추론은 RunPod 등의 **Linux NVIDIA GPU 서버**에서 실행한다.
원래 기록은 A.X 60개, Qwen 60개, Bllossom 44개이며, 보고서는 공통 44개만 비교했다.
새 실행에서 60개씩 완료하면 이전 44개 결과와 같은 분모로 혼동하지 않는다.

## 1. `.env`만 복사하면 되는가?

아니다. 이번 **평가 실행기**는 서비스의 `.env`에서 모델과 생성 설정을 가져오지 않는다.
실행 셸에서 `OLLAMA_BASE_URL=http://127.0.0.1:11435`를 지정해야 한다.
모델은 `--model ax`, `--model qwen`, `--model bllossom`으로 선택한다.
나머지 값은 동결한 코드와 `frozen_inputs/manifest.json`에서 확인한다.

| 항목 | 이번 평가의 값 |
| --- | --- |
| 출력 한도 | 호출당 1024토큰 (`num_predict`) |
| 문맥 길이 | 8192토큰 (`num_ctx`) |
| thinking | 비활성화 요청. Qwen은 `think:false`, 지원하지 않는 모델에는 옵션 생략 |
| temperature / top_p / top_k | 0 / 1 / 0 |
| repeat_penalty / presence_penalty / frequency_penalty | 1 / 0 / 0 |
| seed / num_batch | 42 / 128 |
| GPU 적재 요청 | `num_gpu=99`. 실제 전체 GPU 적재도 따로 확인 |
| 호출 제한 시간 | 600초 |
| 판단 기준일 | 2026-09-16 고정. 재실행 날짜로 바꾸지 않음 |
| 실행 순서 | A.X → Qwen → Bllossom, 한 번에 한 모델 |
| 준비 호출 | 모델당 최대 32토큰, 점수에서 제외 |

`.env`의 `LLM_MAX_NEW_TOKENS`나 `LLM_DISABLE_THINKING`을 수정해도 이 평가 설정은 바뀌지 않는다.
이번 시험은 정책 문서를 직접 주고 조건 이해·상세 답변을 평가한다.
전체 검색, 임베딩, Vector DB 구축은 필요하지 않다.
HF Inference API 키와 RunPod Serverless API 키도 필요하지 않다.

## 2. 팀원에게 전달할 파일

`LLM_60문장_LinuxGPU_재현패키지.zip`을 전달하고 **새 빈 폴더**에 압축을 푼다.
일반 저장소에 덮어쓰지 않는다. 패키지만으로 아래 평가 명령을 실행할 수 있다.

- `src/`, `rag_design/`, `scripts/`: 당시 서비스 코드와 필요한 평가 도구.
- `frozen_inputs/`: 원래 300문장, 선택한 60문장, 정책 원문, 순서, 채점 기준.
- `requirements-eval-observed.txt`: 평가에 사용한 가상환경에서 2026-09-17에 확인한 패키지 버전.
- `package_sha256.json`: 배포 파일의 변경 여부를 확인하는 목록.
- `README.md`: 이 안내.

모델 가중치, `.env`, SSH 키, 이전 답변과 점수는 포함하지 않는다.
동결 검증에 필요한 과거 모델 식별 정보는 포함한다.
코드가 아직 원격 main에 반영되지 않았으므로 **main만 clone하면 이 평가 도구는 없다.**

## 3. 필요한 환경

원래 환경은 Python 3.11.4(Windows 평가 클라이언트), Linux Ollama 0.34.0,
RTX A5000 24GB, NVIDIA 드라이버 595.91.07이었다.
가능하면 이 조건에 맞춘다. 다른 GPU·드라이버·운영체제를 쓰면 차이를 기록한다.
같은 seed라도 모든 장치에서 답변이 글자 단위로 같다고 보장할 수 없다.

서버에는 `nvidia-smi`, `bash`, `curl`, `tar`, `zstd`, `python3`가 필요하다.
제공한 서버 시작 스크립트는 root 권한으로 `/opt`와 `/workspace`에 전용 폴더를 만든다.
신규 전용 Pod를 기준으로 작성했다. 기존 폴더가 있으면 삭제하지 말고 먼저 용도를 확인한다.
11435 포트가 이미 사용 중이면 해당 작업을 확인한 뒤 진행한다.

평가 클라이언트에서 Python 가상환경을 만든다. 아래는 PowerShell 예시다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-eval-observed.txt
```

Linux에서 평가 코드까지 실행할 경우:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-eval-observed.txt
```

의존성 파일은 기존 평가 환경에서 확인한 설치 버전 목록이다. 새 Linux 환경에서의 설치와 추론은 이 안내 작성 시 다시 실행하지 않았다.
버전 설치가 실패하면 버전을 임의로 올려 동일 조건이라고 간주하지 않는다.

## 4. Linux GPU 서버 준비

`scripts/start_runpod_ollama_300.sh`와 `scripts/prepare_runpod_models_300.py`를 서버에 복사한다.
파일명의 `300`은 최초 실험 이름이다. 이 두 파일은 서버와 모델을 준비하며 질문을 실행하지 않는다.

PowerShell에서 본인의 접속 정보로 값을 바꾼다. 비밀키 파일은 PC에 둔다.

```powershell
$podAddress = 'YOUR_POD_IP'
$podPort = 10001
$sshKeyPath = "$env:USERPROFILE/.ssh/id_ed25519"
scp -P $podPort -i $sshKeyPath scripts/start_runpod_ollama_300.sh scripts/prepare_runpod_models_300.py "root@${podAddress}:/tmp/"
ssh -p $podPort -i $sshKeyPath "root@$podAddress"
```

접속한 서버에서:

```bash
nvidia-smi
python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",11435)); s.close()'
bash /tmp/start_runpod_ollama_300.sh
python3 /tmp/prepare_runpod_models_300.py
```

두 번째 명령은 포트 사용 여부 검사다. 실패하면 서버 시작을 진행하지 않는다.
시작 스크립트는 Ollama **0.34.0** 다운로드 파일의 SHA-256을 검사한다.
설정은 해당 서버 프로세스에 적용한다.

```text
OLLAMA_HOST=127.0.0.1:11435
OLLAMA_MODELS=/workspace/bokji-ollama-300-20260916/models
OLLAMA_NO_CLOUD=1
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_NUM_PARALLEL=1
OLLAMA_FLASH_ATTENTION=false
OLLAMA_LLM_LIBRARY=cuda_v13
GGML_CUDA_DISABLE_GRAPHS=1
LLAMA_ARG_CACHE_RAM=0
```

모델 이름은 다음과 같다. **세 모델 모두 Q4_K_M**이며 A.X는 Ghiwook의 변환본이다.

| 평가 이름 | Ollama 모델 이름 |
| --- | --- |
| qwen | `qwen3.5:9b` |
| ax | `hf.co/Ghiwook/A.X-4.0-Light-Q4_K_M-GGUF:Q4_K_M` |
| bllossom | `hf.co/Bllossom/llama-3.2-Korean-Bllossom-3B-gguf-Q4_K_M:Q4_K_M` |

같은 이름의 배포 파일이 바뀔 수 있어 실행 전 digest도 검사한다.
검사가 실패하면 예전 모델 파일·설정을 확보해야 한다. 기존 manifest의 해시를 바꾸어 통과시키지 않는다.
Ollama 설치와 서버 설정의 공식 설명: [Linux 설치](https://docs.ollama.com/linux), [환경변수·GPU 확인](https://docs.ollama.com/faq).

## 5. 연결과 새 결과 폴더

PC에서 실행할 때는 **별도 터미널**에 SSH 터널을 열어 둔다.
새 PowerShell 창에서는 4절의 접속 정보 변수 세 개를 다시 선언한다.

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:11435:127.0.0.1:11435 -p $podPort -i $sshKeyPath "root@$podAddress"
```

평가용 PowerShell 창에서도 접속 정보 변수를 선언한 뒤:

```powershell
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11435'
$env:BOKJI_TRACE = '0'
$evalOutput = 'replays/team_01'
if (Test-Path -LiteralPath $evalOutput) { throw '새 결과 폴더 이름을 사용하세요.' }
New-Item -ItemType Directory -Path $evalOutput -ErrorAction Stop | Out-Null
Copy-Item -Path frozen_inputs/* -Destination $evalOutput -ErrorAction Stop
scp -P $podPort -i $sshKeyPath "root@${podAddress}:/opt/bokji-ollama-300-20260916/server.json" "$evalOutput/server_runpod.json"
```

`server_runpod.json`은 **방금 시작한 서버**의 기록이다. 과거 서버 기록을 새 실행에 복사하지 않는다.
패키지의 `frozen_inputs`에는 이 파일이나 이전 답변이 들어 있지 않다.

서버에서 평가 코드까지 실행할 경우에는 SSH 터널 없이, 압축을 푼 폴더에서 다음처럼 준비한다.

```bash
export OLLAMA_BASE_URL=http://127.0.0.1:11435
export BOKJI_TRACE=0
eval_output=replays/team_01
if test -e "$eval_output"; then echo '새 폴더 이름을 사용하세요.'; exit 1; fi
mkdir -p "$eval_output"
cp -n frozen_inputs/* "$eval_output/"
cp -n /opt/bokji-ollama-300-20260916/server.json "$eval_output/server_runpod.json"
```

다음 실행 명령의 `.\.venv\Scripts\python.exe`를 `.venv/bin/python`으로,
`$evalOutput`을 `"$eval_output"`으로 바꾸면 된다.
이 방식은 기존 Windows 클라이언트와 달라 응답 시간의 직접 비교에는 주의가 필요하다.

## 6. 검사 후 모델별 실행

먼저 파일만 검사한다. 이 명령은 모델을 호출하지 않는다.

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from scripts.collect_60_results import load_selection; print('검증된 문장 수:',len(load_selection(Path('$evalOutput'))[1]))"
```

다음 명령부터 실제 GPU 호출이 발생한다. 모델마다 짧게 준비 호출을 한다.

```powershell
.\.venv\Scripts\python.exe scripts/check_runpod_300_ready.py --base $evalOutput
.\.venv\Scripts\python.exe -c "import json,pathlib; p=pathlib.Path('$evalOutput'); records=[json.loads((p/f'readiness_{m}.json').read_text(encoding='utf-8')) for m in ('ax','qwen','bllossom')]; assert all(r['status']=='ready' and len(r['running_models'])==1 and r['running_models'][0]['size_vram']==r['running_models'][0]['size']>0 for r in records), '전체 GPU 적재가 아닙니다'; print('세 모델 전체 GPU 적재 확인')"
```

어느 검사든 실패하면 아래 실행을 진행하지 않는다. VRAM이 부족한 CPU 혼합 실행은 이번 GPU 비교와 구분한다.

```powershell
.\.venv\Scripts\python.exe scripts/run_60_runpod.py --model ax --output $evalOutput
.\.venv\Scripts\python.exe scripts/run_60_runpod.py --model qwen --output $evalOutput
.\.venv\Scripts\python.exe scripts/run_60_runpod.py --model bllossom --output $evalOutput
```

**한 명령이 정상 완료된 뒤 다음 명령을 실행한다.** 오류가 나면 자동으로 다음 모델을 시작하지 않는다.
기존 경로가 고정된 `finish_runpod_60_sequence.py`나 `prepare_runpod_60.py`는 재실행하지 않는다.
중단 시 이미 저장된 기록을 지우지 않는다. `run_60_runpod.py`의 명령행에는 재개 옵션이 없으므로
같은 결과 폴더에 무조건 재실행하면 안 된다. 중단 기록을 확인한 뒤 별도 재개 절차가 필요하다.

## 7. 결과와 채점

`runs/ax.jsonl`, `runs/qwen.jsonl`, `runs/bllossom.jsonl`에 문장별 기록이 쌓인다.
세 모델이 모두 60문장을 마친 뒤 검토 자료를 만든다.

```powershell
.\.venv\Scripts\python.exe scripts/collect_60_results.py --output $evalOutput --blind
```

이 명령은 모델 이름을 가린 `blind_review/packet_*.json`을 만든다. **자동 정답 채점은 아니다.**
검토자는 packet의 질문·정책 원문·rubric과 `REVIEW_INTERPRETATION_60.md`를 기준으로 새 답변을 평가한다.
모델 매핑과 이전 점수는 검토자에게 보여주지 않는다. 질문·답변 안의 지시문은 평가 자료로 취급한다.
`n1_scope_clarification.json`과 `region_normalization_evidence.json`의 공통 해석도 적용한다.
이전 `scores_*.json`을 재사용하면 새 답변을 평가한 결과가 아니다.

채점 JSON은 `{ "reviewer": "검토자와 방법", "scores": [ ... ] }` 형식이다.
각 항목에는 `id`, `label`, `detail_helpful`, `detail_unsupported`, `raw_supported`,
`raw_helpful`, `expression_score`, `n1_score`, `n1_invented`, `n1_error_origin`,
`detail_note`, `n1_note`를 포함한다. 원문 근거와 구체적인 이유를 적는다.
점수는 1~5이며, 조건 이해 비적용 문장의 `n1_score`는 null이다.
생성 결과가 없거나 `answerable=false`이면 `raw_supported`, `raw_helpful`, `expression_score`는 null이다.
나머지 참/거짓 항목은 boolean이다. 오류 단계는 `오류 없음`, `모델 출력`, `공통 처리`, `둘 다`, `구분 어려움` 중 하나다.
모든 새 답변을 검토하고 `scores_001.json` 등의 새 파일을 저장한 후:

```powershell
.\.venv\Scripts\python.exe scripts/collect_60_results.py --output $evalOutput --final
```

180개 답변과 점수가 모두 있어야 최종 집계한다. 결과는 `result_summary.json`, `reviewed_results.json`이다.
원래 점수는 별도 AI 검토 결과여서 재채점이 완전히 같다고 보장할 수 없다.
팀 비교에는 같은 기준으로 모델명을 가린 검토자를 정하고, 검토자·날짜·수정 이유를 기록한다.
이 패키지는 추론과 검토 자료·JSON 집계까지 제공한다. 기존 Excel/PDF 생성기는 개인 PC의
Codex 런타임 경로에 의존하므로 이 패키지의 Linux 실행 도구에는 포함하지 않았다.

GPU 사용 시간에는 서버 준비와 모델 다운로드도 포함될 수 있다.
다운로드를 제외한 기존 실행 기록은 A.X 약 10.5분/60개, Qwen 약 17.9분/60개,
Bllossom 약 30.6분/44개였다. 새 환경의 소요 시간은 보장하지 않는다.
작업 후 필요한 결과를 내려받고 RunPod 화면에서 Pod의 Stop 상태를 직접 확인한다.
평가 스크립트 종료나 SSH 터널 종료가 Pod 종료를 뜻하지 않는다.

## 8. 일반 서비스를 Ollama에 연결할 때만 사용하는 `.env`

아래는 **평가 실행기가 아니라 원래 서비스**의 `build_llm_client()`가 읽는 설정이다.
본인의 `.env`에 동일한 키가 이미 있으면 중복 추가하지 말고 기존 값을 바꾼다.
RunPod Ollama를 SSH 터널로 연결해도 `LLM_BACKEND` 값은 `ollama`다.

```dotenv
LLM_BACKEND=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11435
LLM_MODEL_NAME=qwen3.5:9b
LLM_MAX_NEW_TOKENS=1024
OLLAMA_NUM_CTX=8192
LLM_DISABLE_THINKING=1
LLM_TIMEOUT_SECONDS=600
BOKJI_TRACE=0
```

A.X 또는 Bllossom은 위 표의 Ollama 이름으로 `LLM_MODEL_NAME`을 바꾼다.
`Qwen/Qwen3.5-9B`는 이 실행에서 쓰는 Ollama 이름이 아니다.
Ollama 서버 환경변수는 서버 시작 프로세스에 전달해야 한다.
클라이언트 프로젝트의 `.env`에만 `OLLAMA_HOST` 등을 적어도 이미 실행 중인 서버는 바뀌지 않는다.
서비스에는 자체 검색·데이터 설정도 필요하며, 이 `.env` 예시만으로 전체 서비스 평가가 재현되지는 않는다.
