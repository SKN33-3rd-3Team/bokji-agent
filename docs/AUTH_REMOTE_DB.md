# 회원 DB 설정: SQLite / MySQL·MariaDB

로그인/회원가입은 기본적으로 로컬 **SQLite**(`.runtime/auth.db`)에 회원을
저장한다. 팀원 여러 명이 같은 회원 계정을 공유하거나 시연용으로 한 곳에
모으려면, `AUTH_DB_URL` 환경변수 하나로 **원격 MySQL/MariaDB** 로 바꿀 수
있다. 선택은 [repository.py](../src/rag_chatbot/auth/repository.py)의 `get_backend`, 원격 연결은 [_mysql.py](../src/rag_chatbot/auth/_mysql.py)가 담당한다. 연결에는 드라이버·계정 권한·네트워크 접근·암호화 키 설정이 필요하다.

원격 회원 DB의 구성 대상은 팀 서버 `skn33.iptime.org`의 MariaDB다. 아래 절차는 MariaDB 서버가 준비돼 있다는 전제이며 실제 접속 가능 여부는 연결 진단으로 확인한다. 사용자 확인에 따라 이관할 기존 운영 회원 DB는 없으므로 레거시 MySQL 마이그레이션은 이번 범위에 필요하지 않다. 새 DB에는 현재 스키마를 생성한다.

LLM 추론은 [RunPod/HuggingFace 안내](RUNPOD_SETUP_DRAFT.md), HTTP 인증·세션은 [백엔드 안내](../backend/README.md#인증세션-동작)를 따른다. 회원 DB를 원격으로 옮겨도 로그인·채팅 세션은 프로세스 메모리에 남는다.

---

## 0. 코드가 기대하는 것 (요약)

| 항목 | 값 |
| --- | --- |
| 환경변수 | `AUTH_DB_URL=mysql://<user>:<password>@<host>:<port>/<dbname>` |
| 드라이버 | 원격 DB 사용 시 `python -m pip install pymysql`. 기본/backend requirements에는 설치 항목으로 활성화돼 있지 않음 |
| 스킴 | `mysql://` `mariadb://` `mysql+pymysql://` 셋 다 허용 |
| 테이블 | 첫 실행 시 `users` 를 자동 생성(`init_schema`) — 수동 DDL 불필요 |
| 암호화 키 | `AUTH_ENC_KEY` 를 **팀이 같은 값으로 공유**. DB 와 분리 보관 |
| 연결 타임아웃 | `AUTH_DB_CONNECT_TIMEOUT`(초, 기본 10) |

DB 선택 순서는 명시적 `db_path`의 SQLite → `AUTH_DB_URL`의 MySQL/MariaDB → `AUTH_DB_PATH` 또는 기본 `.runtime/auth.db`의 SQLite다. 원격 연결 실패 후 SQLite로 자동 전환하거나 두 DB를 동기화하지 않는다. SQLite 장애 대체는 승인되지 않은 제안이다.

같은 DB에 연결하는 앱은 동일한 `AUTH_ENC_KEY`를 사용해야 표시이름·생년월일·관심조건·장애·보훈·소득·가구유형을 복호화할 수 있다. 비밀번호 해시는 이 키와 별개다. 키는 DB 및 저장소와 분리해 보관한다.

---

## 1. 원격 서버(skn33.iptime.org)에서 해야 하는 것

MariaDB 서비스·포트·접근 정책을 확인한 뒤 앱 전용 계정과 스키마를 준비한다.

### 1-1. 앱 전용 계정 생성

[setup_sing_up_db.sql](../scripts/setup_sing_up_db.sql)을 사용한다. 아래는 Bash와 `envsubst`·`mysql`이 준비된 환경의 예이며 비밀번호는 실행 시점에 주입한다.

```bash
DEV_DB_PASSWORD='<길고 무작위한 비밀번호>' envsubst < scripts/setup_sing_up_db.sql \
  | mysql -h skn33.iptime.org -P <port> -u root -p
```

이 스크립트는 `dev_account01` 계정을 만들고 `bokji_auth` 스키마 한정으로만
`ALL PRIVILEGES`(CREATE 포함 - 첫 실행 시 `users` 테이블 자동 생성에
필요)를 준다. 계정/권한을 나중에 다시 확인하려면:

```sql
SHOW GRANTS FOR 'dev_account01'@'%';
```

> 앱은 `bokji_auth` 밖의 스키마나 서버 전역 권한은 전혀 필요로 하지 않는다 -
> `ON bokji_auth.*` 범위를 벗어나지 않게만 한다.

> **보안 주의**: 이 서버의 3306 포트가 인터넷에 열려 있다면 반드시
> - 길고 무작위한 비밀번호(20자 이상 권장)를 쓰고,
> - 애플리케이션 계정은 `bokji_auth` DB 로만 권한을 제한하고,
> - `AUTH_DB_URL` 이 든 `.env` 는 커밋하지 않으며(이미 `.gitignore` 처리됨),
> - 서버의 방화벽/공유기 포트포워딩에서 꼭 필요한 곳만 3306 접근을
>   허용한다(가능하면 팀 IP 화이트리스트).

---

## 2. 로컬(개발/시연 PC) 설정

1. 드라이버 설치:
   ```
   python -m pip install pymysql
   ```
   [requirements-auth.txt](../requirements-auth.txt)의 `pymysql`은 주석 상태다. `backend/requirements-backend.txt` 설치만으로는 원격 드라이버가 설치되지 않는다.

2. `.env` 에 추가 (`.env.example` 참고):
   ```
   AUTH_DB_URL=mysql://dev_account01:<password>@skn33.iptime.org:<port>/bokji_auth
   AUTH_ENC_KEY=<팀이 공유하는 동일한 키>
   # 선택: 원격 DB 응답 없을 때 대기 시간(초)
   AUTH_DB_CONNECT_TIMEOUT=10
   ```
   `AUTH_ENC_KEY` 새 키 생성(최초 1회, 팀에 공유):
   ```
   python src/rag_chatbot/auth/__main__.py keygen
   ```

3. **연결 진단** — 앱을 띄우기 전에 이 스크립트로 포트→서버→로그인→DB→
   테이블을 단계별로 확인한다(어디서 막히는지 바로 나온다):
   ```
   python scripts/check_auth_db.py
   ```
   (`AUTH_DB_URL` 을 읽는다. `--url mysql://...` 로 직접 넘겨도 된다.)

4. FastAPI에서 회원가입·로그인 확인:
   ```
   python -m uvicorn backend.app.main:app --reload --port 8000
   ```
   `http://localhost:8000/docs`의 API-01로 테스트 계정을 생성하고 API-02/04로 로그인·프로필 조회를 확인한다. 새 DB의 `users` 테이블은 인증 저장소 초기화 시 자동 생성된다. HTTPS 운영 시 `COOKIE_SECURE=true`와 프론트의 명시적 CORS origin 설정도 필요하다. React 화면 통합 검증은 별도다.

   DB 연결 장애는 `503 AUTH_BACKEND_UNAVAILABLE`로 처리될 수 있다. 보호된 회원·채팅 요청도 매번 인증 DB를 확인하므로 기존 로그인 사용자도 영향을 받는다. 장애만으로 세션 토큰을 지우지는 않는다.

5. (선택) DBeaver / `mysql` CLI 로 확인:
   ```sql
   USE bokji_auth;
   SELECT id, username, created_at FROM users;   -- 비번 해시/암호문은 굳이 안 봄
   ```

---

## 3. 스키마와 프로필

현재 DDL은 [_mysql.py의 `_SCHEMA`](../src/rag_chatbot/auth/_mysql.py)에 있으며 SQLite 대응 스키마는 [repository.py](../src/rag_chatbot/auth/repository.py)에 있다. 복사한 DDL 대신 코드 정의를 기준으로 새 DB를 생성한다.

| 저장 대상 | 현재 형태 |
| --- | --- |
| 이름·생년월일·관심조건·장애·보훈·소득·가구유형 | `display_name_enc`, `birth_date_enc`, `interests_enc`, `disability_status_enc`, `veteran_status_enc`, `income_bracket_enc`, `household_types_enc` |
| 지역·성별 | `region`, `gender` 평문 |
| 마케팅 동의 | 백엔드 공개 API에서 제거. 기존 `marketing_opt_in` 컬럼·과거 값과 내부 인증 인터페이스는 유지하며 HTTP 가입은 클라이언트 값을 전달하지 않고 기본값 `false`를 사용 |
| 약관·개인정보 동의 | 가입 게이트에서 확인하며 동의 버전/이력을 프로필에 저장하지 않음 |
| 취업 상태 | 회원 테이블에 없음. 일반 상담은 수집할 수 있지만 API-14는 unknown을 유지하고 질문하지 않음 |
| 비밀번호·잠금·시간 | 해시, 실패 횟수·잠금 시각, ISO 형식 시간 문자열 |

회원가입·프로필 수정의 생년월일은 엄격한 `YYYY-MM-DD`와 한국 날짜 기준 만 120세 이하(121번째 생일 전까지)를 적용한다. 인증 토큰·잠금 시간의 UTC 처리는 유지한다. 이름은 정리 후 40자로 잘리고, 공개 응답의 `membership_grade="일반 회원"`은 저장 등급 체계가 아니다. [요청 검증](../backend/README.md#요청-검증)을 따른다.

비밀번호 변경 성공은 현재 로그인만 유지하고 다른 로그인 세션을 폐기한다. 회원별 잠금과 토큰 발급 재검증으로 동시 로그인·사전 인증 대기 요청을 처리하며 실패 시 기존 세션을 유지한다. 탈퇴의 토큰·상담 캐시 정리와 로그인 실패 기록 중 회원 삭제 경합은 [인증·수명 계약](../backend/README.md#상담-실패와-회원-탈퇴-처리)을 따른다. DB 장애 503과 계정 소멸 401은 구분한다.

[API-14](AUTO_RECOMMENDATION_API.md)는 클라이언트 프로필을 받지 않고 인증된 회원의 현재 DB 프로필을 회원 잠금 안에서 조회한다. 가입 선택값이 없으면 미상으로 유지하며 정상 0건과 실제 DB 장애를 구분한다. 로그인과 추천은 별도 요청이다.

MySQL의 `CREATE TABLE IF NOT EXISTS`는 과거 테이블을 자동 보정하는 마이그레이션이 아니다. 이번 범위는 새 DB 초기화이며 SQLite와 MySQL 간 데이터 이전·동기화는 수행하지 않는다.

---

## 4. 운영 메모

- **백업**: 주기적으로
  ```
  mysqldump -h skn33.iptime.org -P <port> -u root -p bokji_auth users > users_backup_$(date +%F).sql
  ```
  서버가 자체 관리 서버라도 실수로 `DROP` 하면 끝이므로 덤프를 따로 남긴다.
- **가용성**: 서버 재부팅·점검 시 인증과 보호된 API가 영향을 받는다. 점검 일정을 공유하고 실제 연결 상태를 확인한다.
- **탈퇴 시 잔재**: SQLite 백엔드는 `secure_delete` PRAGMA 로 삭제 페이지를
  덮지만, MySQL 은 평범한 `DELETE` 다. 저장소 수준 소거는 이 MariaDB 서버의
  운영 정책에 달려 있다.
- **연결 대상 변경**: `AUTH_DB_URL` 외에도 새 서버의 인증·권한·네트워크 및 스키마 호환성을 확인한다. URL 변경이 기존 데이터 이전을 수행하지는 않는다.

---

## 5. 문제 해결

| 증상 | 원인/조치 |
| --- | --- |
| 화면에 "회원 데이터베이스에 연결할 수 없습니다" | 서버가 꺼져 있거나 `AUTH_DB_URL` 호스트/포트 오타, 또는 방화벽/포트포워딩이 막혀 있음. `python scripts/check_auth_db.py`로 단계별 확인 |
| 화면에 "회원 DB 설정(AUTH_DB_URL)이 올바르지 않습니다" | URL 형식 오류(스킴/포트/`/dbname` 누락). `mysql://user:pass@host:port/dbname` 형태인지 확인 |
| 화면에 "회원 테이블(users)을 준비하지 못했습니다 … CREATE 권한" | 앱 계정에 `bokji_auth.*` 의 `CREATE` 권한이 없음(1-1 참고). 또는 root 로 테이블을 미리 만든다 |
| "AUTH_DB_URL 이 설정됐지만 pymysql 이 없습니다" | `pip install pymysql` |
| 로그인은 되는데 이름/관심조건이 빈칸 | `AUTH_ENC_KEY` 가 가입 때와 다른 값. 팀이 같은 키를 공유해야 함 |
| `Access denied for user` | 계정/비밀번호 오타 또는 `dev_account01` 권한 미부여(1-1 참고) |
| 한글 이름이 깨져 저장됨 | DB/테이블이 `utf8mb4` 인지 확인(`init_schema` 가 만들면 자동으로 맞음) |

---

## 6. 테스트

- `tests/test_auth_remote_db.py` — URL 파싱·백엔드 선택·연결 실패는 항상 실행,
  실제 DB 왕복(회원가입~탈퇴)은 `AUTH_TEST_DB_URL` 이 있을 때만:
  ```
  AUTH_TEST_DB_URL=mysql://dev_account01:<pw>@skn33.iptime.org:<port>/bokji_auth_test \
      python -m pytest tests/test_auth_remote_db.py -q
  ```
  (운영 DB 가 아니라 `bokji_test` 같은 별도 DB 를 쓴다 — 테스트가 행을
  만들었다 지운다.)
- SQLite 인증 검사는 `python -m pytest tests/test_auth.py -q`, HTTP 계약 검사는 `python -m pytest backend/tests/ -q`를 사용한다. 실제 DB 테스트가 skip되면 원격 왕복 검증은 미완료이며, 이 명령 목록 자체는 테스트 통과 기록이 아니다.
