# bokji-agent backend (FastAPI)

기존 그래프·RAG를 호출하는 REST API 계층이다. 이 문서는 2026-09-23 확인한 main 커밋 `72b5db730120b12dcfb650c3b9662e7144593927`의 구현 코드를 기준으로 한다. React 화면과 API 연동 코드는 현재 main에 포함되어 있다.

API 정의서는 이 커밋의 구현과 같은 기준으로 관리한다. 과거 API·요구사항·구조 문서의 조정 이력과 현재 동작을 구분하며, 현재 계약은 아래의 코드 근거를 따른다. 프로젝트 협업 규칙은 [프로젝트 준수 기준](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/PROJECT_COMPLIANCE.md)을 참고한다.

## 설치

레포 루트에서:

```bash
python -m pip install -r backend/requirements-backend.txt
```

## 실행

레포 루트에서 (import 경로 때문에 반드시 레포 루트에서 실행):

```bash
python -m uvicorn backend.app.main:app --reload --port 8000
```

루트 [`.env.example`](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/.env.example)을 `.env`로 복사해 설정하며 실제 값은 커밋하지 않는다. 설정 이름과 기본 동작은 [config.py](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/core/config.py)를 참고한다.

| 설정 | 현재 동작 |
| --- | --- |
| `CORS_ORIGINS` | 기본 `http://localhost:5173`. 쉼표로 허용 origin을 지정하며 브라우저 요청에 credentials를 포함한다 |
| `COOKIE_SECURE` | 개발 기본값 `false`. HTTPS 운영에서는 반드시 `true` |
| `AUTH_SESSION_TTL_DAYS` | 로그인 세션 유효기간, 기본 7일 |
| `BOKJI_LOG_DIR` | 로그 디렉터리. 미설정 시 `<root_dir>/logs`. `backend.log`에 기록하며 파일당 5,000,000바이트, 백업 3개로 로테이션한다. 콘솔 로그도 함께 출력한다 |
| `AUTH_DB_URL` / `AUTH_ENC_KEY` | [회원 DB 설정](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/AUTH_REMOTE_DB.md). 원격 MySQL/MariaDB용 `pymysql`은 백엔드 의존성에 포함 |
| `RUNPOD_POD_ID` / `HF_TOKEN` 등 | [LLM 선택 및 폴백](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/RUNPOD_SETUP_DRAFT.md) |

`/healthz`는 프로세스 응답 확인용이고 DB·LLM 준비 완료를 보장하지 않는다. 실제 벡터 데이터·임베딩 설정이 없으면 그래프 워밍업 실패 후에도 서버는 뜨지만, 그래프가 필요한 요청에서 `503 VECTOR_STORE_UNAVAILABLE`이 발생할 수 있다. 회원·옵션 API는 벡터 DB에 의존하지 않는다.

## EC2 Docker 실행

실제 DB 접속정보는 저장소 밖
`/home/ubuntu/.config/bokji-agent/backend.env`에 보관한다.
전용 디렉터리 권한은 `700`, 파일 권한은 `600`으로 설정하고
Compose의 `backend.env_file`에서 참조한다.

회원 DB와 TLS 설정은 [원격 회원 DB 안내](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/AUTH_REMOTE_DB.md)를 따른다.

프로젝트 루트에서 실행한다.

```bash
sudo docker compose config -q &&
sudo docker compose build backend &&
sudo docker compose up -d --force-recreate --wait --wait-timeout 180 backend nginx
```

백엔드와 Nginx를 함께 재생성하여 새 백엔드 컨테이너 주소를 반영한다.
기존 런타임 볼륨과 data 마운트는 유지한다.

```bash
sudo docker compose ps
curl -fsS http://127.0.0.1/healthz
```

health 응답은 HTTP 서버 상태를 확인한다.
회원 DB의 TLS 연결과 회원가입·로그인·프로필 저장은 별도로 검증한다.

## 구성과 API 범위

| 경로 | 역할 |
| --- | --- |
| [app/main.py](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/main.py) | 앱·CORS·예외 처리 등록, 그래프 워밍업 |
| [app/api/v1](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/api/v1) | 인증·사용자·옵션·채팅 라우터 |
| [app/schemas](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/schemas) | HTTP 요청·응답 모델 |
| [app/services](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/services) | 기존 인증/상담 호출, 오류 매핑, 구비서류 배열 추가 |
| [app/session_store](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/session_store) | 로그인 토큰 및 채팅 소유권·마지막 응답의 메모리 저장 |

API-01~14와 진행률·준비 상태·프로세스 상태 경로를 제공한다. 아래 경로 중 `/healthz`만 `/api/v1` 접두사를 사용하지 않는다.

| ID | 메서드·경로 (`/api/v1` 기준) | 기능 |
| --- | --- | --- |
| API-01 | `POST /auth/signup` | 약관·개인정보 동의 각각 확인, 회원가입 및 세션 발급 |
| API-02 | `POST /auth/login` | 로그인, 실패·잠금 처리 |
| API-03 | `POST /auth/logout` | 멱등 로그아웃 |
| API-04 | `GET /users/me` | 프로필 조회 |
| API-05 | `PATCH /users/me` | 프로필 수정: 필드 생략은 유지, null/빈 값은 지움 |
| API-06 | `POST /users/me/password` | 비밀번호 변경, 현재 로그인 유지·다른 로그인 폐기 |
| API-07 | `DELETE /users/me` | 비밀번호 확인 후 탈퇴 |
| API-08 | `GET /users/me/chat-defaults` | `known_*` 프리필, `employment_status_available=false` |
| API-09 | `GET /config/search-options` | 가입·프로필·검색 폼 옵션 |
| API-10 | `POST /chat/messages` | 새 상담, 서버가 채팅 ID 발급 |
| API-11 | `POST /chat/sessions/{session_id}/followup` | 중단 질문 응답 또는 정상 완료 상담의 새 턴. 계산 되묻기 답변(`calc_answers`)은 항목별 "모름"을 `unknown_slots`/`unknown_choices`로 받는다 |
| API-12 | `POST /chat/sessions/{session_id}/policies/{policy_id}/questions` | 마지막 응답의 특정 정책에 대한 문의 |
| API-13 | `DELETE /chat/sessions/{session_id}` | 소유한 채팅 세션·체크포인트 삭제 |
| API-14 | `POST /chat/recommendations` | 인증된 회원의 DB 저장 프로필로 질문 없는 자동 추천 |
| (부가) | `GET /chat/progress/{token}` | 진행 중인 상담의 현재 단계·진행률. 요청 시 `X-Progress-Token` 헤더로 보낸 값으로 조회하며, 본인 요청만 보인다 |
| (부가) | `GET /config/status` | 서버 구동 워밍업(임베딩 모델 로드) 상태. 인증 불필요 |
| (운영) | `GET /healthz` (접두사 없음) | HTTP 프로세스 응답 확인. 인증 불필요 |

`GET /api/v1/chat/progress/{token}`은 API-10/11/14가 도는 동안 현재 단계를 조회한다. 유효한 로그인과 본인 소유 기록이 필요하며, 없거나 타인 기록은 `200 status="unknown"`이다. 응답은 `Cache-Control: no-store`이고 회원 전체 잠금을 사용하지 않는다.

`GET /api/v1/config/status`는 인증 없이 `status`, `message`, `seconds`를 HTTP 200으로 반환한다. `status`는 `pending/running/ready/failed`, `message`는 설명 문자열, `seconds`는 소요 시간(초) 또는 null이다. 워밍업 실패도 HTTP 200의 `failed` 상태로 표시한다. `main.lifespan`이 별도 스레드에서 그래프·임베딩 모델·검색 저장소를 준비하며, 이 응답은 회원 DB·LLM 제공자의 정상 동작을 보장하지 않는다.

`GET /healthz`는 인증 없이 `200 {"status":"ok"}`를 반환하며 HTTP 프로세스 응답만 확인한다.

S-01/02/09는 인증·프로필 API, S-03/04/05는 상담·옵션 API, S-06/07/10은 동일한 `policies` 응답을 재사용한다. 상세·비교 전용 API는 없다. S-08 문의는 `kind`, `text`, `evidence_quotes`, `reason`, `reason_message`, `llm_status`를 반환하며 `policies` 배열은 반환하지 않는다. `guidance`의 `reason`은 현재 `no_llm/no_context/llm_failed/not_answerable/quote_not_found/inconsistent`이며 `reason_message`로 한국어 원인을 제공한다. `answer`에서는 두 reason 필드가 null이다. 문의 이력은 서버에 저장하지 않고 화면에서 다이얼로그가 열린 동안만 관리하는 요구사항이다.

## 인증·세션 동작

- 로그인 쿠키 `session_id`는 서버 메모리에 사용자 ID를 매핑한 불투명 랜덤 토큰이다. 서명 토큰/JWT가 아니며 채팅 응답의 `session_id`와 별개다. 쿠키는 `HttpOnly`, `SameSite=Lax`이고 `Secure`는 위 설정을 따른다.
- 로그인 세션은 기본 7일 후 만료한다. 생성·조회 시 만료 항목을 최대 분당 한 번 정리한다. 채팅 세션에는 별도 TTL이 없다. 둘 다 메모리 저장이며 재시작 시 사라진다. 공유 저장소가 없어 현재 실행은 단일 프로세스를 전제로 하며 여러 worker를 지원하지 않는다.
- API-04~08/10~14는 매 요청마다 인증 DB에서 사용자 ID를 확인하고 그 ID로 작업한다. 삭제된 계정 또는 같은 이메일로 재가입한 다른 계정에 기존 토큰을 사용할 수 없다. API-04/05/07 원본의 계정 없음 `404 USER_NOT_FOUND`와 달리 현재는 `401 UNAUTHORIZED`다.
- DB 연결 장애는 회원가입·로그인·보호된 API에서 `503 AUTH_BACKEND_UNAVAILABLE`이 될 수 있다. API-02/04~08의 기존 503 처리에 더해 보호된 채팅 요청도 영향을 받는다. 장애만으로 토큰을 폐기하지 않으며 SQLite로 자동 전환하지 않는다.
- API-03은 토큰이 없거나 만료돼도 200으로 쿠키를 지운다. 로그아웃은 채팅 상태를 지우지 않는다. 채팅 삭제가 필요하면 인증이 유효할 때 API-13을 먼저 호출한다. API-13은 유효한 로그인 상태에서 없거나 다른 사용자의 채팅 ID에도 200을 반환하고 다른 사용자의 데이터는 건드리지 않는다.
- 비밀번호 변경이 DB에서 성공하면 현재 로그인 세션과 기존 만료 시각을 유지하고 다른 로그인 세션을 폐기한다. 비밀번호 확인·정책 검증·DB 처리 실패 시에는 세션을 폐기하지 않는다. 로그인 인증과 토큰 발급도 같은 회원 잠금 안에서 재확인하므로 변경 전 인증 결과로 늦게 토큰을 발급할 수 없다. `user_operation`의 회원 잠금을 기다리던 요청은 잠금 획득 후 폐기된 토큰을 `401 UNAUTHORIZED`로 거절한다. 회원 잠금을 사용하지 않는 상담에는 같은 대기·재검증 순서를 보장하지 않는다. 탈퇴 시 로그인 토큰 폐기와 상담 정리 범위는 [아래 설명](#상담-실패와-회원-탈퇴-처리)을 따른다. API-04/05/08의 프로필 응답은 `Cache-Control: no-store`다.
- API-01/02/04/05의 사용자 프로필은 `membership_grade="일반 회원"`을 반환한다. 현재 등급은 고정값이며 DB에 저장하는 등급 체계는 없다.
- 마케팅 동의는 공개 API에서 제거됐다. 가입·프로필 요청과 응답 및 OpenAPI에 `marketing_opt_in`을 정의하지 않으며, 기존 클라이언트가 보내도 일반적인 미정의 필드 무시 정책에 따라 수집·전달하지 않는다. 약관·개인정보 동의는 각각 필수다. 기존 DB 컬럼·과거 값과 Streamlit 등 내부 호출부용 인증 인터페이스는 유지한다.
- API-09는 [streamlit_ui/constants.py](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/streamlit_ui/constants.py)의 순수 상수를 import한다. Streamlit 런타임 설치가 필요한 것은 아니지만, React 전환만으로 해당 폴더를 바로 삭제할 수는 없다.

## 상담 실패와 회원 탈퇴 처리

### 최초 상담 실패

- API-10/14는 그래프 실행부터 응답 구성·검증·소유권 등록·캐싱까지 실패 처리를 적용한다. 실패하면 실제 실행한 그래프의 해당 `session_id` 체크포인트와 부분 등록된 소유권·응답 캐시를 각각 정리한다. 다른 상담과 정상 응답(`answered`/`needs_input`)의 상태는 보존한다.
- 정리 중 오류는 경고 로그로 남기고 원래 오류 응답을 유지한다. 정리 자체가 실패한 저장소에는 상태가 남을 수 있다. 공개 삭제 API의 소유권 검사는 유지하며, 소유권 등록 전 실패한 상담을 정리하기 위해 API-13을 호출하지 않는다.
- 내부적으로 [service.ask()](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/service.py)에 선택적 `_on_graph_ready=None` 콜백을 추가했다. 백엔드가 실제 실행한 그래프를 전달받아 정리하며, 정리하려고 그래프를 다시 초기화하지 않는다. 기존 호출부는 콜백을 생략할 수 있다. 실패 응답과 세션 동작은 아래의 현재 코드 계약 요약을 참고한다.

### 회원·상담 세션별 실행 순서와 탈퇴

- API-05·06·07·08·12·13은 `user_operation`으로 같은 회원 ID의 잠금을 공유한다. 잠금 획득 후 토큰 활성 여부와 DB 회원 ID를 다시 확인한다. API-04는 회원 전체 잠금을 사용하지 않는다.
- API-10·11·14는 `chat_operation`으로 토큰과 DB 프로필을 확인하고 회원 전체 잠금 없이 실행한다. 같은 회원의 자동 추천과 일반 상담은 서로 기다리지 않는다. API-11과 등록된 상담 삭제는 상담 세션별 잠금을 사용하므로 같은 세션의 실행·삭제는 순차 처리한다. API-12는 이 세션 잠금을 사용하지 않아 API-11과의 직렬 실행을 보장하지 않는다. 잠금·저장소의 범위는 단일 프로세스다.
- 탈퇴는 실행 중인 모든 상담의 완료를 기다리지 않는다. 정리 대상으로 이미 등록된 상담은 세션 잠금을 기다릴 수 있지만, API-10/14는 실행 후 소유권을 등록하므로 탈퇴 시점에 등록되지 않은 실행까지 대기하거나 탈퇴 후 늦은 등록을 차단한다고 보장할 수 없다. 이후 새 인증 확인에서는 삭제된 계정의 토큰을 거절한다.
- 회원 삭제 성공 후 로그인 토큰을 모두 폐기하고 응답 쿠키를 지운다. 이어 정리 시점에 해당 회원으로 등록된 상담의 체크포인트와 소유권·프로필·정책 응답 캐시를 정리한다. 다른 회원의 상담은 유지한다. 비밀번호 오류나 DB 장애로 회원 삭제가 실패하면 기존 로그인·상담 상태를 유지한다.
- 그래프 조회 또는 체크포인트 삭제가 실패해도 경고 로그를 남기고 백엔드 상담 캐시는 제거하며, 완료된 탈퇴는 성공으로 응답한다. 따라서 탈퇴 성공 응답이 모든 그래프 데이터의 삭제 성공까지 보장하지는 않는다. 남은 메모리 체크포인트는 프로세스 재시작 시 해제된다.

### 로그인 실패 기록 중 탈퇴

- API-02에서 잘못된 비밀번호를 확인한 뒤 실패 횟수 기록 전 또는 재조회 전에 회원이 삭제돼도 `401 INVALID_CREDENTIALS`로 종료하며 로그인 세션을 발급하지 않는다.
- SQLite·MySQL의 `record_failed_login()`은 회원 행이 없으면 기존 반환형인 `(0, None)`을 반환한다. MySQL은 조기 반환 전에 트랜잭션을 종료한다. 실제 DB 장애는 기존 `503 AUTH_BACKEND_UNAVAILABLE` 처리로 전달한다.

## 요청 검증

- API-10의 `known_region`은 API-09의 시/도 17개, 성별·장애·보훈·소득·가구 유형은 API-09의 공개 코드만 허용한다. 그래프 내부 값인 `unknown`이나 목록에 없는 값은 `400 VALIDATION_ERROR`로 거부하며 그래프 호출·채팅 세션 저장 전에 검사한다. 선택 문자열은 생략 또는 `null`, 가구 유형은 생략 또는 빈 배열로 미입력을 표현한다.
- API-01/05의 `birth_date`와 API-10의 `known_birth_date`는 ASCII 숫자의 `YYYY-MM-DD`만 허용한다. `20000101`, `2000-W01-1`과 앞뒤 공백은 `400 VALIDATION_ERROR`다. 실제 달력 날짜를 검증하고 미래 날짜는 거절한다. 회원가입·수정·API-10·그래프 `as_of`·프리필·슬롯 파서는 **Asia/Seoul 오늘 기준 만 120세 이하**를 사용한다. 120번째 생일부터 121번째 생일 전까지 포함하며, 인증 토큰의 UTC 만료 기준은 바꾸지 않는다. React는 오류를 입력 안내로 표시해야 한다. 가입 시 생략·빈 문자열은 미입력이고, 프로필 PATCH는 생략하면 유지하고 `null`·빈 문자열이면 지운다.
- API-01의 `name`과 API-05의 `display_name`은 [인증 서비스](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/auth/service.py)의 기존 규칙으로 공백·제어문자를 정리한 뒤 앞 40자만 저장·반환한다. 40자를 넘는 이름을 오류로 거부하지 않는다. 가입 시 정리 후 빈 이름은 `400 VALIDATION_ERROR`다.

## 상담 턴과 계산 입력

API-11은 현재 그래프가 중단돼 있으면 `Command(resume=...)`로 재개하고, 정상 완료됐으면 같은 채팅 ID에서 새 일반 턴을 시작한다. 신원과 적절한 기존 프로필은 보존하고 최초 질문·검색 결과·계산 결과·질문 횟수·선택지 등 턴 상태를 초기화한다. 생년월일의 나이는 새 한국 기준일로 다시 계산한다. 자동 추천에서 이어 온 새 턴도 일반 모드로 전환한다. MemorySaver 체크포인트를 전체 메시지 대화 이력으로 보장하지 않는다.

API-11 요청은 non-null `message` 또는 non-null `calc_answers` 중 정확히 하나다. 기존 자유 텍스트와 계산 중 `message="모름"` 건너뛰기를 유지한다. 구조화 답변은 현재 계산 질문에만 적용하며 다음 공용 필드를 사용한다.

선택 요청 필드 `top_k`는 1~20의 정수 또는 null이고, `extra_interests`는 문자열 배열 또는 null이다. 일반 프로필 되묻기·새 턴에서는 전달한 `top_k`와 비어 있지 않은 `extra_interests`를 반영한다. `top_k` 생략/null, `extra_interests` 생략/null/[]는 옵션을 변경하지 않는다. 계산 interrupt 재개에서는 두 옵션을 변경하지 않는다. 정상 완료 후 새 턴은 이전 질문의 관심사·파생 나이를 초기화하고 새로 전달한 관심사를 적용한다.

| 응답 필드 | React 입력 규칙 |
| --- | --- |
| `interrupt_id` | 가장 최근 질문 ID를 답변에 포함. 로그인/채팅 ID와 별개이며 권한 증명이 아님 |
| `calc_missing_slots` / `calc_slot_inputs` | 현재 슬롯·한글 라벨·select/number·옵션·정수 경계. select는 `value` 전송, `label` 표시 |
| `calc_missing_choices` | 현재 `policy_id`·`policy_title`·정확한 원문 `labels` 연결 |

숫자는 `children_count` 0..20, `household_size` 1..30의 엄격한 정수만 받는다. bool·소수·숫자 문자열과 나이 직접 입력은 거절한다. 나이는 생년월일에서 파생한다. 카테고리의 내부 `unknown`, 다른 슬롯·정책·라벨, 오래된 질문 ID, 빈 답변은 `400 VALIDATION_ERROR`이며 잘못된 혼합 답변은 그래프 호출 전에 전체 거절한다. 일부 답변 후 다시 질문받으면 **새 ID와 남은 입력 목록**을 사용한다. 필드 전체와 예시는 [공용 D5/API-11 계약](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/AUTO_RECOMMENDATION_API.md#api-11-검증부분-응답재개)을 따른다.

그래프 실행에 실패한 API-11 체크포인트는 같은 ID로 재시도할 수 없다. 해당 체크포인트에 다시 요청하면 `500 GRAPH_EXECUTION_ERROR` 형식을 유지하면서 “이 상담을 계속할 수 없습니다. 새 상담을 시작해 주세요.”로 안내한다. **API-10 새 세션**으로 다시 시작할 수 있으며 API-13 삭제는 필수가 아니다. 오류 응답이 상담을 자동 삭제하거나 새 세션을 만들지는 않는다. 일반 실행 오류의 재시도 안내는 유지한다. 입력 검증 400은 그래프 상태를 바꾸지 않는다. API-10/14 초기 실행 실패는 등록 전 상태를 정리하고 새 요청으로 다시 시작한다.

## 로그인 후 자동 추천과 React 인계

[API-14 v1.0 계약](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/AUTO_RECOMMENDATION_API.md)은 로그인 성공 후 React가 기존 쿠키로 별도 POST를 호출하는 흐름이다. 로그인 응답 안에서 실행하지 않는다. 본문·쿼리를 받지 않으며 `{}`도 `400 VALIDATION_ERROR`다. 본문을 읽는 중 연결이 종료되면 `499 CLIENT_DISCONNECTED`로 처리한다. 서버는 `chat_operation`으로 인증 상태와 DB의 현재 프로필을 확인하며 회원 전체 잠금을 잡지 않는다. 클라이언트 프로필·질문·세션 ID를 입력받지 않는다. 성공·오류 모두 `Cache-Control: no-store`다.

가입 계정 필수값은 6개이고 선택 프로필은 8개다. 관심사 `interests`는 `extra_interests` 검색 힌트이며 단독 자격 확정 근거가 아니다. 필수 수집 슬롯은 `region/birth_date/gender/income_bracket/disability_status/employment_status`다. `household_types`는 선택 슬롯이고 `veteran_status`는 필수 수집·검색 필터 대상이 아니다. 보훈 등록 값은 `국가유공자/보훈` 검색 힌트와 자동 모드의 프로필 표시용 값으로 반영한다. 그래프 필수 6개 중 5개만 가입에서 **선택적으로** 수집하며 취업 상태는 수집하지 않는다. 자동 모드는 누락을 미상으로 유지하고 초기·계산·충돌 질문을 하지 않는다. 지역이 없으면 `region_scope="national"`과 `region_names=["전국"]`이 확인된 문서만 후보로 사용하고, 지역이 있으면 기존 지역 규칙을 적용한다.

자동 추천은 자격 `충족`만 남기되 `verification_unchecked`가 있다는 이유로 충족 카드를 제거하지 않는다. **전역 `answer_status=abstained`는 개별 충족보다 우선하여 모든 카드를 제외한다.** 일반 상담은 보류 카드와 원래 판정을 유지한다. 일반 보류 카드의 화면 라벨은 **자격 미확인**으로 표시하도록 React에 인계하며, 백엔드 `badge`를 일괄 변경했다는 의미는 아니다.

금액을 계산하지 못해도 충족 카드는 유지하고 계산 가능한 금액·범위가 전혀 없으면 금액 필드를 `null`로 내려 화면에서 금액 영역을 생략한다. 알려진 0·단가·범위·유효 총액은 유지한다. 필터 후 순위·요약·안내·인용·출력 형식·중복 참조·세션 정책 캐시도 같은 카드 집합을 사용한다. 정상 0건은 빈 목록·요약 0과 **현재 정보로 추천할 정책이 없습니다**이며, DB·제공자·노드 한도 오류를 0건으로 바꾸지 않는다. 빈 결과도 새 채팅 ID를 발급한다.

현재 main의 [React 자동 추천](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/frontend/src/features/chat/useAutoRecommendations.ts)은 회원 ID별 쿼리 키와 결과 캐시를 사용한다. 캐시가 있으면 화면 재진입·포커스·재연결만으로 다시 호출하지 않는다. 네트워크 오류(status=0)·500·503은 최대 2회 자동 재시도하며 인증·검증 오류는 즉시 표시한다. [API 호출부](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/frontend/src/api/chatApi.ts)는 본문 없이 쿠키와 진행률 헤더를 전송한다. 로그인·가입·홈·상담·마이페이지 화면과 상담 진행률·계산 입력·정책 상세·비교 UI가 main에 존재한다. 이번 문서 갱신에서 브라우저·실환경 통합 테스트를 실행한 것은 아니다.

## 구비서류 응답

S07-06/S10-01과 API-10/11 원본의 보류 사항은 사용자 옵션 ② 결정으로 해소됐다. API 응답의 `policies[].detail`은 아래 원문과 파생 배열을 함께 제공한다.

| 원문 (`string` 또는 `null`) | 파생 배열 (`string[]` 또는 `null`) |
| --- | --- |
| `required_documents` | `required_documents_items` |
| `required_documents_official` | `required_documents_official_items` |
| `required_documents_self` | `required_documents_self_items` |

[파서](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/core/document_parsing.py)는 여러 줄이면 빈 줄을 제외하고 줄 단위로 분리하며 `-`, `○`, `•` 글머리표를 제거한다. 한 줄이면 괄호 밖 쉼표만 분리한다. 구분이 없으면 원문을 한 항목으로 유지하고, 빈 값·`해당없음`·`해당 없음`·`-`는 배열에서 `null`로 처리한다. 원문 문자열은 보존하며 서류명 의미 추론이나 임의의 가운뎃점 분리는 하지 않는다. 프론트는 새 배열을 칩 표시에 사용할 수 있다.

## 현재 코드 계약 요약

아래 표는 기준 main 구현의 계약이다. 과거 문서와의 D9/D10 조정 이력을 현재 미결 계약으로 취급하지 않는다. 테스트 파일 링크는 검사 위치를 가리키며 이번 문서 갱신에서 해당 테스트를 실행했다는 뜻이 아니다.

| 항목 | 현재 동작 | 코드·검사 위치 |
| --- | --- | --- |
| 인증·소유권 | 삭제 계정·무효 로그인은 401 UNAUTHORIZED. API-11/12 없는·타인 상담은 404 SESSION_NOT_FOUND. API-13은 없는·타인 ID에도 멱등 200 | [인증](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/api/deps.py), [상담](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/services/chat_adapter.py) |
| 비밀번호 변경 | 현재 로그인과 만료 시각 유지, 다른 로그인 폐기. 회원 잠금을 사용하는 요청은 잠금 획득 후 다시 검증 | [사용자 API](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/api/v1/users.py), [회귀 위치](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/tests/test_password_session_revocation.py) |
| 생년월일 | 엄격한 YYYY-MM-DD, 한국 오늘 기준 미래 금지·만 120세 전체 허용 | [슬롯 검증](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/graph/slot_schema.py), [날짜 검사](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/tests/test_birth_date_contract.py) |
| 상담 새 턴 | 중단은 resume, 정상 완료는 같은 ID의 새 일반 턴. 실패한 API-11 체크포인트는 API-10 새 세션 필요 | [그래프](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/graph/builder.py), [턴 검사](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/tests/test_chat_turns.py) |
| 계산 답변 | message XOR calc_answers. 현재 질문 ID·슬롯·정책·라벨·정수 경계 검증. 부분 답변 후 새 질문 ID | [스키마](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/schemas/chat.py), [계산 검사](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/tests/test_calc_followup.py) |
| 보류·검증 범위 | 일반 상담 카드·원래 상태 유지. 자동 추천은 전역 abstained면 전부 제외. verification_checked/unchecked는 한글 라벨 | [응답 생성](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/service.py), [자격 판정](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/graph/nodes/eligibility_verdict.py) |
| 중복수급 | 제한 metadata·원문의 다른 정책명만으로 자동 불가를 단정하지 않고 조건부·근거·사용자 사실을 보존 | [중복 판정](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/graph/nodes/duplicate_benefit.py) |
| API-12 실패 | 처리된 LLM 호출·파싱·검증 실패는 200 kind=guidance. 미처리 예외는 500 INTERNAL_ERROR. 초기화 SystemExit는 503 VECTOR_STORE_UNAVAILABLE | [문의 어댑터](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/services/followup_adapter.py), [문의 검증](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/light_followup.py) |
| API-12 컨텍스트 | 마지막 answered에 캐시된 정책 제목·detail·eligibility_reasons와 사용자 프로필 사용. final_answer 미전달. 그래프를 다시 실행하지 않음 | [문의 어댑터](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/services/followup_adapter.py) |
| 실행 한도 | LLM 그래프 노드 1회 총 90초를 제공자·재시도·병렬 호출이 공유. 소진은 500 GRAPH_EXECUTION_ERROR. 요청 전체/API-12 일괄 한도가 아님 | [deadline](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/deadline.py), [한도 검사](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/tests/test_node_deadline.py) |
| LLM 진단 | model은 설정 체인. failures=calls-successes. messages만 중복 제거하며 길이를 실패 횟수로 사용하지 않음. 기록 미지원 주입 client는 호출 수가 null | [RecordingLLMClient.summary](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/llm/client.py), [직렬화](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/service.py) |
| 시간 진단 | timing.phases에 name/count/total_s/avg_s/share, node_path에 node/title/seconds. 측정 구간이 겹칠 수 있음 | [타이머](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/timing.py) |
| 자동 추천 | 별도 POST, 서버 DB 프로필만 사용, 본문·쿼리·질문 없음. 지역 누락 시 전국 정책만. 자격 충족만·전역 abstained 우선. 0건·오류 구분 | [라우트](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/api/v1/chat.py), [응답 생성](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/service.py) |
| 공개 입력·회원 응답 | 공개 옵션과 날짜 검증, 마케팅 공개 필드 제외, 등급 일반 회원, 이름 정리 후 최대 40자. 가입·수정 관심사는 19종과 레거시 값 허용 | [인증 스키마](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/schemas/auth.py), [인증 서비스](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/src/rag_chatbot/auth/service.py) |
| 구비서류 | 원문 3개와 파생 배열 3개를 함께 제공. 명시된 구분만 분리하고 의미를 추론하지 않음 | [파서](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/core/document_parsing.py) |
| 응답 스키마 | 총액 범위·LLM 시간 진단 포함. answer_status는 자유 문자열로 complete/partial/abstained를 현재 사용 | [공용 스키마](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/backend/app/schemas/chat.py) |

이번 갱신은 코드와 문서의 정적 대조다. 원격 회원 DB·실제 벡터 데이터/LLM·React 브라우저 통합·실제 제공자의 90초 동작은 이번 작업에서 재검증하지 않았다. 메모리 세션과 단일 프로세스 운영, 탈퇴 정리의 제한은 위 설명을 따른다. [PII 로깅 규칙](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/PII_LOGGING.md)을 참고한다.

## 테스트

```bash
python scripts/run_api_tests.py
```

설치·Windows PowerShell 실행·API-01~14 검사 범위·JUnit 보고서·CI 안내는 [API 테스트 설명서](https://github.com/SKN33-3rd-3Team/bokji-agent/blob/72b5db730120b12dcfb650c3b9662e7144593927/docs/API_TESTING.md)를 따른다. 이 실행기는 실제 자격증명과 dotenv를 배제하고 임시 SQLite 및 네트워크 차단을 적용한다.

이 명령은 HTTP 계약 중심 검사다. 실제 MariaDB 왕복, 벡터 데이터·LLM 연결, React 브라우저 통합은 별도 환경에서 확인해야 한다. 테스트 결과에는 실행한 명령·리비전·통과/실패/skip을 기록하고 이전 실행의 통과 수를 현재 검증 결과로 사용하지 않는다.
