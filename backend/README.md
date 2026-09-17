# bokji-agent backend (FastAPI)

기존 그래프·RAG를 호출하는 REST API 계층이다. 최종 화면은 React이며 별도 브랜치에서 개발 중이다. 인증 서비스·SQLite/MySQL 저장소와 LLM 클라이언트/선택 로직에도 변경이 있으므로 기존 코어 전체가 무수정인 것은 아니다.

기준 자료는 `API_정의서.xlsx`, `요구사항_정의서.xlsx`(2026-09-16), `PROJECT_STRUCTURE.md`(2026-09-16 rev.2)다. 원본은 팀 공유 참고자료로 관리하며 `docs/`에 포함하지 않는다. 사용자 확정 사항과 미결 범위는 [프로젝트 준수 기준](../docs/PROJECT_COMPLIANCE.md#서비스-전환-기준)을 따른다.

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

루트 [`.env.example`](../.env.example)을 `.env`로 복사해 설정하며 실제 값은 커밋하지 않는다. 설정 이름과 기본 동작은 [config.py](app/core/config.py)를 참고한다.

| 설정 | 현재 동작 |
| --- | --- |
| `CORS_ORIGINS` | 기본 `http://localhost:5173`. 쉼표로 허용 origin을 지정하며 브라우저 요청에 credentials를 포함한다 |
| `COOKIE_SECURE` | 개발 기본값 `false`. HTTPS 운영에서는 반드시 `true` |
| `AUTH_SESSION_TTL_DAYS` | 로그인 세션 유효기간, 기본 7일 |
| `AUTH_DB_URL` / `AUTH_ENC_KEY` | [회원 DB 설정](../docs/AUTH_REMOTE_DB.md). 원격 MySQL/MariaDB는 `pymysql` 별도 설치 필요 |
| `RUNPOD_POD_ID` / `HF_TOKEN` 등 | [LLM 선택 및 폴백](../docs/RUNPOD_SETUP_DRAFT.md) |

`/healthz`는 프로세스 응답 확인용이고 DB·LLM 준비 완료를 보장하지 않는다. 실제 벡터 데이터·임베딩 설정이 없으면 그래프 워밍업 실패 후에도 서버는 뜨지만, 그래프가 필요한 요청에서 `503 VECTOR_STORE_UNAVAILABLE`이 발생할 수 있다. 회원·옵션 API는 벡터 DB에 의존하지 않는다.

## 구성과 API 범위

| 경로 | 역할 |
| --- | --- |
| [app/main.py](app/main.py) | 앱·CORS·예외 처리 등록, 그래프 워밍업 |
| [app/api/v1](app/api/v1) | 인증·사용자·옵션·채팅 라우터 |
| [app/schemas](app/schemas) | HTTP 요청·응답 모델 |
| [app/services](app/services) | 기존 인증/상담 호출, 오류 매핑, 구비서류 배열 추가 |
| [app/session_store](app/session_store) | 로그인 토큰 및 채팅 소유권·마지막 응답의 메모리 저장 |

다음 13개 엔드포인트가 존재한다. 이는 엔드포인트 수이며 계약 완료율이 아니다.

| ID | 메서드·경로 (`/api/v1` 기준) | 기능 |
| --- | --- | --- |
| API-01 | `POST /auth/signup` | 약관·개인정보 동의 각각 확인, 회원가입 및 세션 발급 |
| API-02 | `POST /auth/login` | 로그인, 실패·잠금 처리 |
| API-03 | `POST /auth/logout` | 멱등 로그아웃 |
| API-04 | `GET /users/me` | 프로필 조회 |
| API-05 | `PATCH /users/me` | 프로필 수정: 필드 생략은 유지, null/빈 값은 지움 |
| API-06 | `POST /users/me/password` | 비밀번호 변경 |
| API-07 | `DELETE /users/me` | 비밀번호 확인 후 탈퇴 |
| API-08 | `GET /users/me/chat-defaults` | `known_*` 프리필, `employment_status_available=false` |
| API-09 | `GET /config/search-options` | 가입·프로필·검색 폼 옵션 |
| API-10 | `POST /chat/messages` | 새 상담, 서버가 채팅 ID 발급 |
| API-11 | `POST /chat/sessions/{session_id}/followup` | 일시 중단된 상담의 되묻기 응답 제출 |
| API-12 | `POST /chat/sessions/{session_id}/policies/{policy_id}/questions` | 마지막 응답의 특정 정책에 대한 문의 |
| API-13 | `DELETE /chat/sessions/{session_id}` | 소유한 채팅 세션·체크포인트 삭제 |

S-01/02/09는 인증·프로필 API, S-03/04/05는 상담·옵션 API, S-06/07/10은 동일한 `policies` 응답을 재사용한다. 상세·비교 전용 API는 없다. S-08 문의는 `kind`, `text`, `evidence_quotes`를 반환하며 `policies` 배열은 반환하지 않는다. 문의 이력은 서버에 저장하지 않고 화면에서 다이얼로그가 열린 동안만 관리하는 요구사항이다.

## 인증·세션 동작

- 로그인 쿠키 `session_id`는 서버 메모리에 사용자 ID를 매핑한 불투명 랜덤 토큰이다. 서명 토큰/JWT가 아니며 채팅 응답의 `session_id`와 별개다. 쿠키는 `HttpOnly`, `SameSite=Lax`이고 `Secure`는 위 설정을 따른다.
- 로그인 세션은 기본 7일 후 만료한다. 생성·조회 시 만료 항목을 최대 분당 한 번 정리한다. 채팅 세션에는 별도 TTL이 없다. 둘 다 메모리 저장이며 재시작 시 사라진다. 공유 저장소가 없어 현재 실행은 단일 프로세스를 전제로 하며 여러 worker를 지원하지 않는다.
- API-04~08/10~13은 매 요청마다 인증 DB에서 사용자 ID를 확인하고 그 ID로 작업한다. 삭제된 계정 또는 같은 이메일로 재가입한 다른 계정에 기존 토큰을 사용할 수 없다. API-04/05/07 원본의 계정 없음 `404 USER_NOT_FOUND`와 달리 현재는 `401 UNAUTHORIZED`다.
- DB 연결 장애는 회원가입·로그인·보호된 API에서 `503 AUTH_BACKEND_UNAVAILABLE`이 될 수 있다. API-02/04~08의 기존 503 처리에 더해 보호된 채팅 요청도 영향을 받는다. 장애만으로 토큰을 폐기하지 않으며 SQLite로 자동 전환하지 않는다.
- API-03은 토큰이 없거나 만료돼도 200으로 쿠키를 지운다. 로그아웃은 채팅 상태를 지우지 않는다. 채팅 삭제가 필요하면 인증이 유효할 때 API-13을 먼저 호출한다. API-13은 유효한 로그인 상태에서 없거나 다른 사용자의 채팅 ID에도 200을 반환하고 다른 사용자의 데이터는 건드리지 않는다. 되묻기와 삭제는 같은 채팅 세션에서 직렬화한다.
- 비밀번호 변경은 현재 세션을 포함해 기존 로그인 세션을 유지한다. 탈퇴는 해당 사용자 로그인 토큰을 모두 폐기한다. API-04/05/08의 프로필 응답은 `Cache-Control: no-store`다.
- API-01/02/04/05의 사용자 프로필은 `membership_grade="일반 회원"`을 반환한다. 현재 등급은 고정값이며 DB에 저장하는 등급 체계는 없다.
- 마케팅 동의는 공개 API에서 제거됐다. 가입·프로필 요청과 응답 및 OpenAPI에 `marketing_opt_in`을 정의하지 않으며, 기존 클라이언트가 보내도 일반적인 미정의 필드 무시 정책에 따라 수집·전달하지 않는다. 약관·개인정보 동의는 각각 필수다. 기존 DB 컬럼·과거 값과 Streamlit 등 내부 호출부용 인증 인터페이스는 유지한다.
- API-09는 [streamlit_ui/constants.py](../streamlit_ui/constants.py)의 순수 상수를 import한다. Streamlit 런타임 설치가 필요한 것은 아니지만, React 전환만으로 해당 폴더를 바로 삭제할 수는 없다.

## 요청 검증

- API-10의 `known_region`은 API-09의 시/도 17개, 성별·장애·보훈·소득·가구 유형은 API-09의 공개 코드만 허용한다. 그래프 내부 값인 `unknown`이나 목록에 없는 값은 `400 VALIDATION_ERROR`로 거부하며 그래프 호출·채팅 세션 저장 전에 검사한다. 선택 문자열은 생략 또는 `null`, 가구 유형은 생략 또는 빈 배열로 미입력을 표현한다.
- API-01/05의 `birth_date`와 API-10의 `known_birth_date`는 ASCII 숫자의 `YYYY-MM-DD`만 허용한다. `20000101`, `2000-W01-1`과 앞뒤 공백은 `400 VALIDATION_ERROR`다. 실제 날짜·미래·나이 범위는 각 기존 인증/그래프 검증을 유지한다. 가입 시 생략·빈 문자열은 미입력이고, 프로필 PATCH는 생략하면 유지하고 `null`·빈 문자열이면 지운다.
- API-01의 `name`과 API-05의 `display_name`은 [인증 서비스](../src/rag_chatbot/auth/service.py)의 기존 규칙으로 공백·제어문자를 정리한 뒤 앞 40자만 저장·반환한다. 40자를 넘는 이름을 오류로 거부하지 않는다. 가입 시 정리 후 빈 이름은 `400 VALIDATION_ERROR`다.

## 구비서류 응답

S07-06/S10-01과 API-10/11 원본의 보류 사항은 사용자 옵션 ② 결정으로 해소됐다. API 응답의 `policies[].detail`은 아래 원문과 파생 배열을 함께 제공한다.

| 원문 (`string` 또는 `null`) | 파생 배열 (`string[]` 또는 `null`) |
| --- | --- |
| `required_documents` | `required_documents_items` |
| `required_documents_official` | `required_documents_official_items` |
| `required_documents_self` | `required_documents_self_items` |

[파서](app/core/document_parsing.py)는 여러 줄이면 빈 줄을 제외하고 줄 단위로 분리하며 `-`, `○`, `•` 글머리표를 제거한다. 한 줄이면 괄호 밖 쉼표만 분리한다. 구분이 없으면 원문을 한 항목으로 유지하고, 빈 값·`해당없음`·`해당 없음`·`-`는 배열에서 `null`로 처리한다. 원문 문자열은 보존하며 서류명 의미 추론이나 임의의 가운뎃점 분리는 하지 않는다. 프론트는 새 배열을 칩 표시에 사용할 수 있다.

## 원본 문서와 남은 계약 차이

아래는 수정·결정이 필요한 차이다. 현재 구현을 설명하는 것으로, 원본 요구사항을 폐기하거나 새 동작을 승인한 것이 아니다.

| 항목·원본 위치 | 현재 구현과 남은 확인 |
| --- | --- |
| 생년월일: 요구사항 `S-02!D7/E7`, API `API-01!D14` | 인증은 UTC 오늘의 정확히 120년 전 날짜를 하한으로, 그래프는 서버 로컬 날짜 기준 만 나이 120세를 상한으로 사용한다. 원본도 날짜 하한과 그래프 동일 경계를 함께 요구하므로 나이 경계·시간대의 단일 기준 결정이 필요하다 |
| 완료된 상담의 새 일반 질문: 요구사항 `S-03!D11`, API `API-10!A50`, `INDEX!E15` | 세션 ID만으로 API-11을 선택하라는 설명과 interrupt 재개 계약이 충돌한다. 완료 후 API-11은 새 일반 턴을 실행하지 않고 이전 답변을 반환할 수 있다. 처리 API/전환 규칙은 미정이다 |
| N10a 계산 추가 질문과 S-05 위젯 | 기존 코어의 자유 텍스트 질문·재개 경로는 있지만 HTTP 응답에 `calc_missing_slots`/`calc_missing_choices`가 없어 계산 전용 위젯 구성 정보가 부족하다 |
| 검증 상태 칩: 요구사항 `S-07!D7` | `verification_checked`/`verification_unchecked`에는 슬롯 코드가 아닌 `연령`, `성별` 등 한글 라벨이 온다. 코드→라벨 매핑 전제를 정리해야 한다 |
| 검증 완료 배지: 요구사항 `S-07!E6` | `answer_status=abstained`여도 `policies`가 남을 수 있다. 카드 존재만으로 “원문 대조 검증완료”를 고정 표시할 수 없으며 상태·인용·검증 범위를 반영할 표시 계약이 필요하다 |
| 중복수급 판정: 요구사항 `S-07!E8` | 기존 N11은 재검색 근거의 상호배타 metadata 또는 제한 원문에 명시된 이번 응답의 다른 정책을 근거로 `불가`를 반환할 수 있다. “불가로 자동 격상하지 않음” 설명과 충돌하며 판정 기준 조정은 미결이다 |
| API-12 오류: `API-12!C31` | 코어는 재시도 후 LLM 호출·검증 실패를 `200 kind=guidance`로 처리한다. 코어가 처리하지 못한 일반 예외는 어댑터에서 500으로 매핑한다. 원본의 “LLM 호출/검증 중 예외=500” 범위와 조정이 필요하다 |
| API-12 컨텍스트: `INDEX!E16`, `API-12!A39` | 실제 입력은 정책 제목·상세·자격 근거와 캐시된 프로필이다. INDEX의 `final_answer`는 전달하지 않고, 시트의 “정책 항목만”보다 프로필까지 사용한다 |
| 진단 필드: 요구사항 S05-04, API `API-10!D35/A54` | 폴백 구성의 `llm_status.model`은 설정된 체인이며 실제 응답한 제공자를 구분하지 않는다. `failures`는 실패 호출 수가 아니라 중복 제거한 오류 메시지 수다 |
| 응답 스키마: API `API-10!C30/D33:D36` | `total_amount_min/max`, `llm_status.total_seconds/slowest_seconds/avg_seconds`가 추가로 반환된다. `answer_status`는 현재 Pydantic에서 자유 문자열이다. 원본과 타입·필드 정합성을 맞춰야 한다 |

원본에 없는 503 및 401 변경은 위 인증 설명을 기준으로 프론트와 맞춰야 한다. Pod 실패 상태별 폴백 정책·서버 종류·자동 종료, 여러 worker를 위한 저장소, React Context/Redux·query client·타입 생성·폰트 제공 방식은 미정이다. S05-04의 일반 사용자/관리자 디버그 노출 범위도 아직 결정되지 않았다. 개인정보·세션 토큰·원문 로그 취급은 [PII 로깅 규칙](../docs/PII_LOGGING.md)을 따른다.

## 테스트

```bash
python -m pytest backend/tests/
```

이 명령은 HTTP 계약 중심 검사다. 실제 MariaDB 왕복, 벡터 데이터·LLM 연결, React 브라우저 통합은 별도 환경에서 확인해야 한다. 테스트 결과에는 실행한 명령·리비전·통과/실패/skip을 기록하고 이전 실행의 통과 수를 현재 검증 결과로 사용하지 않는다.
