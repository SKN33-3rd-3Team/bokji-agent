# RunPod Serverless 연동 준비 (draft, 2026-08-31 기준)

N9/N10이 호출하는 `RunPodServerlessClient`(`src/rag_chatbot/llm/client.py`)는
아직 실제 RunPod 엔드포인트 없이 코드만 준비된 상태다. 이 문서는 나중에
엔드포인트를 실제로 띄울 때 GPU 서버 쪽에서 해줘야 하는 것과, 지금 코드가
그것에 대해 어떤 가정을 깔고 있는지를 정리한다. RunPod 계정/콘솔에 직접
접근해서 확인한 내용이 아니라, `RunPodServerlessClient`가 이미 구현하고
있는 API 호출 방식(`POST /v2/{endpoint_id}/runsync`, Bearer 토큰, vLLM
계열 serverless worker의 흔한 응답 관례)과 RunPod의 일반적인 Serverless
사용 패턴을 근거로 정리한 것이므로, 실제 배포 시점에 세부사항이 다를 수
있다 — 특히 "미확인" 표시한 항목은 팀이 RunPod 콘솔에서 직접 확인해야
한다.

## 1. 지금 코드가 이미 정해놓은 것 (바꾸려면 코드도 같이 바꿔야 함)

- **호출 방식**: RunPod Serverless의 `runsync`(동기) 엔드포인트를 호출한다.
  `run`(비동기, 폴링 필요) 방식이 아니다. 워커 콜드스타트가 오래 걸리는
  모델을 쓰면 `runsync`가 타임아웃(기본 `timeout_seconds=60.0`)에 걸릴 수
  있다 — 실제 모델로 테스트해보고 필요하면 타임아웃을 늘리거나 `run` +
  폴링 방식으로 바꿔야 할 수도 있다.
- **요청 payload**: `{"input": {"model": <RUNPOD_MODEL_NAME>, "messages": [...]}}`
  형태(OpenAI 채팅 메시지 관례)로 보낸다.
- **응답 파싱**: `_parse_output()`이 vLLM 기반 워커에서 흔한 두 형태
  (`output[0]["choices"][0]["message"]["content"]` 또는
  `output["choices"][0]["message"]["content"]` 또는 `output["text"]`)만
  시도한다. 실제 handler가 이 형태와 다르게 응답하면 `LLMCallError`가
  나므로, 엔드포인트를 처음 띄우고 나서 실제 응답 JSON을 한 번 찍어보고
  `_parse_output()`을 거기 맞게 고쳐야 한다.
- **인증/설정은 전부 환경변수**: `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`,
  `RUNPOD_MODEL_NAME` (`.env.example`에 자리만 잡아둠, 값은 비어있음). 이
  값들이 비어 있으면 N9/N10은 `llm_client=None`으로 동작해서 LLM 없이도
  규칙 기반 로직만으로 정상 동작한다 — 즉 RunPod가 아직 없어도 지금 당장
  코드가 깨지지는 않는다.

## 2. GPU 서버(RunPod) 쪽에서 실제로 해줘야 하는 것 (미확인 — 팀이 RunPod 콘솔에서 확인 필요)

이 부분은 RunPod 서비스 자체의 설정이라 이 저장소 코드만으로는 검증할 수
없다. 일반적인 RunPod Serverless 사용 흐름 기준으로 필요할 것으로 보이는
단계를 순서대로 적는다.

1. **모델을 vLLM 등으로 서빙 가능한 형태로 HuggingFace Hub에 올린다.**
   후보 3개(skt/A.X-4.0-Light, Qwen/Qwen3.5-9B,
   Bllossom/llama-3.2-Korean-Bllossom-3B) 중 fine-tuning한 checkpoint를
   올릴 예정이라고 했으니, private repo로 올릴 경우 RunPod worker가 그
   repo를 받아올 수 있는 `HUGGING_FACE_HUB_TOKEN`(또는 RunPod가 요구하는
   동등한 환경변수)을 RunPod Serverless 엔드포인트 설정의 환경변수로
   등록해야 한다.
2. **Serverless 엔드포인트를 만들고 GPU 타입/개수를 고른다.**
   모델 크기별로 필요한 VRAM 감(대략치, 실측 아님 — fine-tuning 방식·양자화
   여부에 따라 크게 달라짐):
   | 후보 모델 | 대략 파라미터 수 | fp16/bf16 추론 시 대략 VRAM | 비고 |
   | --- | --- | --- | --- |
   | Bllossom/llama-3.2-Korean-Bllossom-3B | 약 3B | 약 6~8GB | 셋 중 가장 가볍다 — 소형 GPU(예: RTX 4090 24GB 1장)로도 여유 |
   | skt/A.X-4.0-Light | "Light"라는 이름 외 정확한 파라미터 수 미확인 | 미확인 | HuggingFace 모델 카드에서 파라미터 수·권장 VRAM을 직접 확인 필요 |
   | Qwen/Qwen3.5-9B | 약 9B | 약 18~20GB | A100 40GB 1장 등 좀 더 큰 GPU 필요할 수 있음 |

   양자화(4bit/8bit)를 쓰면 필요 VRAM이 크게 줄어드는데, 그럴 경우 vLLM
   worker 쪽 양자화 옵션 설정이 추가로 필요하다 — 이 저장소 코드는 양자화
   여부를 모르므로 관여하지 않는다.
3. **워커 템플릿을 고른다.** RunPod가 제공하는 vLLM 기반 Serverless worker
   템플릿(OpenAI 호환 출력)을 쓰면 `_parse_output()`이 이미 기대하는 형태와
   맞을 가능성이 높다. 커스텀 `handler.py`를 직접 짜는 경우에는 위 1번
   응답 형태를 그대로 맞추거나, `_parse_output()`을 그 handler에 맞게
   고쳐야 한다.
4. **엔드포인트가 뜨면 `endpoint_id`와 API 키를 발급받아 `.env`에 채운다.**
   ```
   RUNPOD_ENDPOINT_ID=<콘솔에서 발급된 엔드포인트 ID>
   RUNPOD_API_KEY=<RunPod 계정 API 키>
   RUNPOD_MODEL_NAME=<vLLM worker에 등록된 모델 이름>
   ```
5. **첫 실호출 검증.** `RunPodServerlessClient(...).complete("테스트", system=None)`을
   직접 한 번 호출해보고, `_parse_output()`이 실제 응답을 파싱하는지
   확인한다. 실패하면 `LLMCallError` 메시지에 원본 응답(`raw output`)이
   그대로 찍히도록 만들어뒀으니 그걸 보고 `_parse_output()`을 조정하면 된다.

## 3. 지금 시점에 확실히 필요 없는 것

- 이 프로젝트 저장소(사인박스/개발 환경) 쪽에는 GPU가 전혀 필요 없다 —
  추론은 전부 RunPod 쪽에서 일어나고, 이쪽은 HTTP 요청만 보낸다.
- fine-tuning은 로컬(팀 컴퓨터)에서 진행하기로 했으므로, RunPod
  엔드포인트를 만들기 전까지는 이 문서의 2번 항목을 미리 준비할 필요는
  없다 — checkpoint가 HuggingFace Hub에 올라간 뒤에 진행하면 된다.

## 4. 요약

지금 상태(RunPod 미배포)에서는 아무것도 안 해도 N9/N10이 정상 동작한다
(`llm_client=None`). RunPod를 실제로 띄우는 시점에는 위 2번 순서대로
진행하고, 응답 형태가 `_parse_output()` 가정과 다르면 그 함수만 고치면
된다 — 판정/계산 로직(N9/N10의 핵심 규칙)은 LLM 클라이언트 구현과
분리돼 있어 영향받지 않는다.
