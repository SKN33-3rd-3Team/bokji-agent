# LLM 실행 설정: RunPod Pod / HuggingFace / Serverless / 명시적 Ollama

이 문서는 현재 [service.py의 `build_llm_client()`](../src/rag_chatbot/service.py)와 [LLM 클라이언트](../src/rag_chatbot/llm/client.py)의 선택 규칙을 설명한다. 원본 `PROJECT_STRUCTURE.md` 2.4절과 사용자 결정에 따라 RunPod Pod 우선 및 호출 실패 시 HuggingFace로 전환하는 방향이 승인됐다. 실제 Pod 서버 종류·주소·응답 호환성·종료 정책은 배포 환경에서 확인해야 하며 이 문서는 실서비스 연결 완료 기록이 아니다.

회원 DB는 LLM 서버와 별개이며 [회원 DB 설정](AUTH_REMOTE_DB.md)을 따른다. FastAPI 어댑터는 기존 서비스의 LLM 선택을 재사용하고, React가 LLM 제공자를 직접 선택하지 않는다.

## 선택 순서

| 조건 | 현재 실행 경로 |
| --- | --- |
| `RUNPOD_POD_ID` 있음 | `LLM_BACKEND`와 무관하게 `RunPodPodClient` 우선 |
| Pod ID + `HF_TOKEN` 또는 `HUGGINGFACE_TOKEN` 있음 | Pod 호출의 `LLMCallError` 발생 시 남은 노드 시간 안에서 HF로 재시도 |
| Pod ID만 있음 | Pod 단독 사용. HF 폴백 없음 |
| Pod ID 없음, `LLM_BACKEND=ollama` | 명시한 로컬 Ollama 모델 사용. HF 토큰 불필요, 자동 제공자 폴백 없음 |
| Pod ID 없음, `LLM_BACKEND=runpod` | `RUNPOD_ENDPOINT_ID`와 `RUNPOD_API_KEY`가 있으면 Serverless, 없으면 LLM 클라이언트 없음 |
| Pod ID 없음, `LLM_BACKEND=hf` 또는 `huggingface` | HF 토큰이 있으면 HF. 미설정 backend의 기본값은 `hf` |
| Pod ID 없음, 그 밖의 `LLM_BACKEND` | 설정 오류 (`ValueError`) |

“로컬(기존 경로)”은 앱에서 HF Inference API를 호출한다는 의미이며 로컬 모델 추론을 뜻하지 않는다. 클라이언트를 구성하지 못해 `None`인 경우 코어의 규칙·템플릿 경로를 사용하고 정책 문의는 안내 응답으로 제한될 수 있다. 임베딩·벡터 데이터 준비는 별도로 필요하다.

PR65의 개발용 로컬 추론은 Pod ID가 없을 때 **`LLM_BACKEND=ollama`로 명시적으로 선택**한다. `LLM_MODEL_NAME`은 필수이고 `OLLAMA_BASE_URL`은 loopback HTTP(S) 주소만 허용한다(기본 `http://localhost:11434`, 프록시·redirect 미사용). 기본 `OLLAMA_NUM_CTX=4096`, `LLM_MAX_NEW_TOKENS=1024`, `LLM_TIMEOUT_SECONDS=120`이며 `LLM_DISABLE_THINKING=1`은 `/api/show`에 thinking capability가 있을 때만 적용한다. Pod와 Ollama를 함께 설정해도 Pod가 우선이고, Pod 실패 시 설정된 HF로만 전환한다. 기본 production 경로를 Ollama로 바꾸거나 자동 로컬 폴백을 추가하지 않는다.

## Pod 설정과 현재 폴백 범위

| 변수 | 용도 |
| --- | --- |
| `RUNPOD_POD_ID` | Pod 경로 선택 |
| `RUNPOD_POD_PORT` | 기본 `8000`, 노출된 추론 서버 포트 |
| `RUNPOD_POD_API_KEY` | 추론 서버 자체 인증이 필요할 때 사용 |
| `LLM_MODEL_NAME` | Pod 모델 이름. HF도 우선 사용하며, HF만의 다음 선택은 `LLM_HF_MODEL` 및 코드 기본 모델 |
| `LLM_TIMEOUT_SECONDS` | Pod/Serverless 전송 timeout 기본 `120`초. 그래프에서는 아래 노드의 남은 시간으로 줄여 전달하며 노드 총 한도를 늘리지 않음 |
| `HF_TOKEN` / `HUGGINGFACE_TOKEN` | HF 기본 경로 또는 Pod 실패 시 HF 전환에 필요한 자격 증명 |
| `LLM_MAX_NEW_TOKENS` | HF 클라이언트의 기본 생성 토큰 상한 `8192` |

현재 Pod 클라이언트는 `https://{pod_id}-{port}.proxy.runpod.net/v1/chat/completions`로 요청하고 `choices[0].message.content`를 읽는다. `RUNPOD_POD_API_KEY`는 Serverless용 RunPod 계정 키와 별개다. 실제 서버의 URL·모델명·OpenAI 호환 요청/응답 형식이 이 코드와 맞는지 확인해야 한다.

현재 구현은 연결/HTTP 오류, 응답 파싱 오류, 빈 값·문자열이 아닌 출력, `finish_reason=length`를 `LLMCallError`로 처리한다. HF 자격 증명이 있으면 이 예외에서 폴백하는 기존 정책을 유지한다. 그래프 노드의 시간 한도를 이미 소진했다면 HF로 전환하지 않는다. 서버/인증 실패 로그는 HF 성공 여부와 무관하게 남긴다.

Pod와 HF가 모두 실패하면 오류를 호출 코어로 전달한다. 일반 상담의 비시간초과 오류는 기존 재시도·규칙/안내·보류 경로를 유지한다. API-14 자동 추천의 최종 제공자 실패와 그래프 노드 한도 소진은 `500 GRAPH_EXECUTION_ERROR`이며 정상 0건·안내로 숨기지 않는다. API-12의 직접 LLM 호출·재시도 후 guidance 계약은 이번에 변경하지 않았고 D9/D10은 PM 미결이다.

## 노드 총 실행 한도

[deadline.py](../src/rag_chatbot/deadline.py)와 [그래프 배선](../src/rag_chatbot/graph/builder.py)은 LLM이 구성된 **N1/N5/N9/N10/N10a/N13의 노드 1회 실행 전체를 90초**로 제한한다. 하나의 절대 마감 시간을 RunPod·HF·모든 재시도·여러 호출·병렬 자식 호출이 공유한다. 다음 노드/다음 실행은 새 한도이며 상담 전체가 90초라는 뜻은 아니다. HF 단독 노드에도 적용한다. 비LLM 노드와 API-12 직접 호출에는 새 노드 한도를 적용하지 않는다.

명시적으로 선택한 [Ollama](../src/rag_chatbot/llm/ollama.py)도 같은 노드 한도를 사용한다. `/api/show`와 `/api/chat`, 여러 호출이 남은 예산을 공유하며 만료 후 capability·응답 통계 쓰기를 차단한다. 일반 전송 오류와 노드 만료를 구분하고, API-14의 실제 제공자 실패를 정상 0건으로 바꾸지 않는다. urllib 전송 역시 실행 중인 스레드의 강제 종료를 보장하지 않는다. 그래프 밖의 직접 평가 호출과 API-12는 기존 전송 timeout을 사용한다.

호출·실행 슬롯 대기부터 같은 예산을 사용한다. 제공자 timeout은 남은 시간으로 줄이며 공유 클라이언트 설정을 요청마다 수정하지 않는다. HF의 기본 전송 timeout은 60초이고 Pod/Serverless의 서비스 기본값은 120초지만, 그래프 노드 안에서는 모두 남은 총 예산의 제한을 받는다. `LLM_TIMEOUT_SECONDS`를 늘려 노드 90초를 재설정할 수 없으며 별도 노드 한도 환경변수도 없다.

소진 시 전용 예외를 일반 폴백에서 삼키지 않고 기존 `500 GRAPH_EXECUTION_ERROR`와 재시도 안내로 반환한다. 입력 상태를 복사하고 ContextVar 문맥을 전달해 interrupt/resume를 유지하며, 만료 후 공유 캐시·호출 통계 쓰기는 잠금 안에서 차단한다. 늦게 끝난 노드의 반환값을 체크포인트에 반영하지 않는다. 실패한 최초 상담은 실제 실행 그래프의 상태를 정리한다. API-11의 실패 체크포인트는 같은 ID로 재시도할 수 없어 **API-10 새 세션**이 필요하고, API-14 초기 실패는 새 API-14 요청으로 재시도한다.

실행 풀과 입장 대기는 제한된 용량을 사용한다. **실행 중인 동기 SDK 스레드는 강제 종료하지 못한다.** 늦은 전송·HF 제공자 탐색은 백그라운드에서 계속되어 고정 실행 슬롯을 점유할 수 있고 프로세스 종료가 지연될 수 있다. Requests/HTTPX 전송 timeout 자체는 전체 경과 시간 보장이 아니므로 호출자 대기 한도와 만료 후 쓰기 차단을 함께 사용한다. 실제 제공자에서 90초 동작·취소가 검증됐다고 주장하지 않는다.

HF는 제공자 탐색 이후 `_inner_post` 내부 훅에서 남은 시간을 다시 확인한다. [requirements-graph.txt](../requirements-graph.txt)의 최소 버전은 기존 provider/extra_body 호출과 이 훅을 지원하는 `huggingface_hub>=0.29.0`으로 정정했다. 0.26/0.27은 provider/훅, 0.28은 chat extra_body 호환이 부족했다. 0.29·0.35.3·1.0·1.31 태그의 소스 호환성을 검토했으며 **실행 검증은 설치된 1.31.0만** 했다. 0.29 런타임이나 임의 미래 SDK의 내부 훅 호환성을 보장하지 않는다. SDK 탐색 자체의 강제 중단도 보장하지 않는다.

## Serverless 기존 경로

Pod ID가 없고 `LLM_BACKEND=runpod`이면 [RunPodServerlessClient](../src/rag_chatbot/llm/client.py)를 사용한다. 설정은 `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`, `RUNPOD_MODEL_NAME`이며 `/v2/{endpoint_id}/runsync`에 `input.model`과 `input.messages`를 보낸다. Pod의 `/v1/chat/completions`와 다른 경로다. 실제 worker의 응답 형식이 현재 파서와 맞는지는 별도 확인이 필요하다.

## 진단 필드와 운영 미결 사항

- `llm_status.model`은 폴백 구성 시 `runpod-pod:…→fallback:huggingface:…` 형태의 설정 체인이다. API-10 원본의 설명처럼 실제로 응답한 제공자를 알려주는 필드는 아니다.
- `calls`와 `successes`는 기록 래퍼 관점의 호출 결과다. 내부 Pod 실패 후 HF가 성공하면 래퍼에는 성공으로 기록된다. `failures`는 실패 호출 수가 아니라 중복 제거된 오류 메시지 수다.
- `llm_status.total_seconds`, `slowest_seconds`, `avg_seconds`도 반환하지만 원본 API 정의서에는 열거되지 않았다. 원본과의 차이는 [백엔드 안내](../backend/README.md#원본-문서와-남은-계약-차이)에 정리한다.
- 실제 Pod 서버 종류, 모델 서빙 방식, 자동 종료·운영 정책은 별도 배포 확인이 필요하다. 현재 로그인·채팅 저장소는 단일 프로세스 메모리이므로 여러 API worker 운영을 지원하지 않는다.
- D12 결정은 현재 데모 진단 노출 유지다. 새 승자 provider·관리자 권한 기능은 추가하지 않는다. D11 실패 로그는 직접/폴백 시도의 HTTP 401/403을 `auth_failure`, 그 밖을 `server_failure`로 구분하고 상태·짧은 code/type만 기록한다. 오류 메시지·토큰·쿠키·프로필·프롬프트·원시 모델 출력은 남기지 않는다. [PII 로깅 규칙](PII_LOGGING.md)을 따른다.

## 검증

프로젝트 의존성이 준비된 환경에서 저장소 루트 기준으로 실행한다.

```bash
python -m pytest tests/test_llm_pod.py tests/test_ollama_client.py tests/test_node_deadline.py backend/tests/test_node_deadline.py -q
```

이 검사는 선택 순서·응답 처리·폴백·축소 시간의 노드 한도 및 HTTP 어댑터의 회귀 확인용이다. 실제 Pod/HF/Ollama 호출 성공, 모델 출력 품질, 전체 벡터 검색·React 통합을 증명하지 않는다. 배포 환경의 연결·응답 형식 확인과 Gate 5 통합 검증은 별도로 기록한다.
