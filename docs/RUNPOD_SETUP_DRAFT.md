# LLM 실행 설정: RunPod Pod / HuggingFace / Serverless

이 문서는 현재 [service.py의 `build_llm_client()`](../src/rag_chatbot/service.py)와 [LLM 클라이언트](../src/rag_chatbot/llm/client.py)의 선택 규칙을 설명한다. 원본 `PROJECT_STRUCTURE.md` 2.4절과 사용자 결정에 따라 RunPod Pod 우선 및 호출 실패 시 HuggingFace로 전환하는 방향이 승인됐다. 실제 Pod 서버 종류·주소·응답 호환성·종료 정책은 배포 환경에서 확인해야 하며 이 문서는 실서비스 연결 완료 기록이 아니다.

회원 DB는 LLM 서버와 별개이며 [회원 DB 설정](AUTH_REMOTE_DB.md)을 따른다. FastAPI 어댑터는 기존 서비스의 LLM 선택을 재사용하고, React가 LLM 제공자를 직접 선택하지 않는다.

## 선택 순서

| 조건 | 현재 실행 경로 |
| --- | --- |
| `RUNPOD_POD_ID` 있음 | `LLM_BACKEND`와 무관하게 `RunPodPodClient` 우선 |
| Pod ID + `HF_TOKEN` 또는 `HUGGINGFACE_TOKEN` 있음 | Pod 호출의 `LLMCallError` 발생 시 같은 요청을 HF로 재시도 |
| Pod ID만 있음 | Pod 단독 사용. HF 폴백 없음 |
| Pod ID 없음, `LLM_BACKEND=runpod` | `RUNPOD_ENDPOINT_ID`와 `RUNPOD_API_KEY`가 있으면 Serverless, 없으면 LLM 클라이언트 없음 |
| Pod ID 없음, `LLM_BACKEND=hf` 또는 `huggingface` | HF 토큰이 있으면 HF. 미설정 backend의 기본값은 `hf` |
| Pod ID 없음, 그 밖의 `LLM_BACKEND` | 설정 오류 (`ValueError`) |

“로컬(기존 경로)”은 앱에서 HF Inference API를 호출한다는 의미이며 로컬 모델 추론을 뜻하지 않는다. 클라이언트를 구성하지 못해 `None`인 경우 코어의 규칙·템플릿 경로를 사용하고 정책 문의는 안내 응답으로 제한될 수 있다. 임베딩·벡터 데이터 준비는 별도로 필요하다.

## Pod 설정과 현재 폴백 범위

| 변수 | 용도 |
| --- | --- |
| `RUNPOD_POD_ID` | Pod 경로 선택 |
| `RUNPOD_POD_PORT` | 기본 `8000`, 노출된 추론 서버 포트 |
| `RUNPOD_POD_API_KEY` | 추론 서버 자체 인증이 필요할 때 사용 |
| `LLM_MODEL_NAME` | Pod 모델 이름. HF도 우선 사용하며, HF만의 다음 선택은 `LLM_HF_MODEL` 및 코드 기본 모델 |
| `LLM_TIMEOUT_SECONDS` | 서비스에서 Pod/Serverless 클라이언트를 만들 때 기본 `120`초 |
| `HF_TOKEN` / `HUGGINGFACE_TOKEN` | HF 기본 경로 또는 Pod 실패 시 HF 전환에 필요한 자격 증명 |
| `LLM_MAX_NEW_TOKENS` | HF 클라이언트의 기본 생성 토큰 상한 `8192` |

현재 Pod 클라이언트는 `https://{pod_id}-{port}.proxy.runpod.net/v1/chat/completions`로 요청하고 `choices[0].message.content`를 읽는다. `RUNPOD_POD_API_KEY`는 Serverless용 RunPod 계정 키와 별개다. 실제 서버의 URL·모델명·OpenAI 호환 요청/응답 형식이 이 코드와 맞는지 확인해야 한다.

현재 구현은 연결/HTTP 오류, 응답 파싱 오류, 빈 값·문자열이 아닌 출력, `finish_reason=length`를 `LLMCallError`로 처리한다. HF 자격 증명이 있으면 이 예외에서 폴백한다. **Pod→HF 전환 방향의 승인이 모든 HTTP 상태에 대한 전환 정책 확정을 뜻하지는 않는다.** 상태별 허용 범위는 미결이며, 현재 코드의 예외 처리 범위를 설명한 것이다.

Pod와 HF가 모두 실패하면 오류를 호출 코어로 전달한다. 이후 재시도·규칙/안내·보류 처리는 각 호출 경로를 따르므로, 모든 요청의 동일 품질·즉시 응답을 보장하지 않는다.

## Serverless 기존 경로

Pod ID가 없고 `LLM_BACKEND=runpod`이면 [RunPodServerlessClient](../src/rag_chatbot/llm/client.py)를 사용한다. 설정은 `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`, `RUNPOD_MODEL_NAME`이며 `/v2/{endpoint_id}/runsync`에 `input.model`과 `input.messages`를 보낸다. Pod의 `/v1/chat/completions`와 다른 경로다. 실제 worker의 응답 형식이 현재 파서와 맞는지는 별도 확인이 필요하다.

## 진단 필드와 운영 미결 사항

- `llm_status.model`은 폴백 구성 시 `runpod-pod:…→fallback:huggingface:…` 형태의 설정 체인이다. API-10 원본의 설명처럼 실제로 응답한 제공자를 알려주는 필드는 아니다.
- `calls`와 `successes`는 기록 래퍼 관점의 호출 결과다. 내부 Pod 실패 후 HF가 성공하면 래퍼에는 성공으로 기록된다. `failures`는 실패 호출 수가 아니라 중복 제거된 오류 메시지 수다.
- `llm_status.total_seconds`, `slowest_seconds`, `avg_seconds`도 반환하지만 원본 API 정의서에는 열거되지 않았다. 원본과의 차이는 [백엔드 안내](../backend/README.md#원본-문서와-남은-계약-차이)에 정리한다.
- 실제 Pod 서버 종류, 모델 서빙 방식, 자동 종료·운영 정책과 실패 상태별 폴백 정책은 미정이다. 현재 로그인·채팅 저장소는 단일 프로세스 메모리이므로 여러 API worker 운영을 지원하지 않는다.
- 일반 사용자/관리자 디버그 노출 범위도 미정이다. 연결 진단에서 나온 자격 증명·사용자 입력·모델 원문을 저장소나 공개 로그에 남기지 않는다.

## 검증

프로젝트 의존성이 준비된 환경에서 저장소 루트 기준으로 실행한다.

```bash
python -m pytest tests/test_llm_pod.py backend/tests/test_chat_api.py -q
```

이 검사는 응답 처리·폴백 및 HTTP 어댑터의 회귀 확인용이다. 실제 Pod/HF 호출 성공, 모델 출력 품질, 전체 벡터 검색·React 통합을 증명하지 않는다. 배포 환경의 연결·응답 형식 확인과 Gate 5 통합 검증은 별도로 기록한다.
