# 회원 DB 를 원격 MySQL/MariaDB 로 (로그인·회원가입 원격화)

로그인/회원가입은 기본적으로 로컬 **SQLite**(`.runtime/auth.db`)에 회원을
저장한다. 팀원 여러 명이 같은 회원 계정을 공유하거나 시연용으로 한 곳에
모으려면, `AUTH_DB_URL` 환경변수 하나로 **원격 MySQL/MariaDB** 로 바꿀 수
있다. 코드는 이미 그 분기를 지원한다(`src/rag_chatbot/auth/repository.py` 의
`get_backend`, `src/rag_chatbot/auth/_mysql.py`) — 어떤 호스트에 떠 있는
MySQL/MariaDB 든 `AUTH_DB_URL` 하나만 맞으면 그대로 붙는다(특정 호스팅
업체에 종속되지 않음).

> **2026-09-16 변경**: 회원 DB 는 RunPod 이 아니라 팀이 자체 운영하는
> `skn33.iptime.org` 서버의 MariaDB 를 쓴다. 이 서버는 이미 MariaDB 가 떠
> 있다고 가정하고, 계정만 새로 만들면 된다(아래 1장) — RunPod Pod 를 직접
> 배포하던 예전 절차(Network Volume, Container Image 선택 등)는 더 이상
> 필요 없다. `scripts/runpod_mariadb_bootstrap.sh` 는 RunPod Pod 전용이라
> 이 서버에는 해당하지 않는다(더 이상 쓰지 않음).

> RunPod 의 LLM Serverless/Pod 연동(N1/N5/N9/N10/N13)은 완전히 별개다. 그건
> `docs/RUNPOD_SETUP_DRAFT.md` 를 본다. 이 문서는 **회원 DB** 얘기만 다룬다.

---

## 0. 코드가 기대하는 것 (요약)

| 항목 | 값 |
| --- | --- |
| 환경변수 | `AUTH_DB_URL=mysql://<user>:<password>@<host>:<port>/<dbname>` |
| 드라이버 | `pymysql` (`pip install pymysql`, 순수 파이썬 — 빌드 도구 불필요) |
| 스킴 | `mysql://` `mariadb://` `mysql+pymysql://` 셋 다 허용 |
| 테이블 | 첫 실행 시 `users` 를 자동 생성(`init_schema`) — 수동 DDL 불필요 |
| 암호화 키 | `AUTH_ENC_KEY` 를 **팀이 같은 값으로 공유**. DB 와 분리 보관 |
| 연결 타임아웃 | `AUTH_DB_CONNECT_TIMEOUT`(초, 기본 10) |

`AUTH_DB_URL` 이 비어 있으면 기존 SQLite 동작 그대로다(기본값 불변). 테스트
코드처럼 `db_path=` 를 명시적으로 넘기면 `AUTH_DB_URL` 과 무관하게 SQLite 를
쓴다.

> **왜 `AUTH_ENC_KEY` 공유가 필수인가**: 표시이름·관심조건은 이 키로 암호화돼
> DB 에 들어간다. 팀원마다 키가 다르면 서로가 만든 행의 이름/관심조건을
> 복호화하지 못한다(비밀번호 해시는 키와 무관하므로 로그인 자체는 됨).

---

## 1. 원격 서버(skn33.iptime.org)에서 해야 하는 것

이 서버는 MariaDB 가 이미 떠 있다고 가정한다(RunPod Pod 처럼 이미지를
새로 배포하거나 Network Volume 을 마운트하는 절차가 필요 없다) — 필요한
건 앱 전용 계정 하나뿐이다.

### 1-1. 앱 전용 계정 생성

`scripts/setup_sing_up_db.sql` 을 쓴다(레포에 이미 있음 - 비밀번호를 파일에
평문으로 남기지 않고 실행 시점에 주입하는 방식):

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
   pip install pymysql
   ```
   (또는 `requirements-auth.txt` 의 `pymysql` 줄 주석을 풀고
   `pip install -r requirements-auth.txt`)

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

4. 스키마 생성 확인 — 아무 회원가입 한 번이면 `users` 테이블이 자동으로
   만들어진다:
   ```
   streamlit run app.py
   ```
   → 로그인 화면 → 회원가입 → 다시 로그인. 성공하면 완료다.

   DB 가 꺼져 있거나 주소가 틀리면 화면에 "회원 데이터베이스에 연결할 수
   없습니다" 안내가 뜬다(앱이 죽지는 않는다 —
   `AuthBackendUnavailableError`).

5. (선택) DBeaver / `mysql` CLI 로 확인:
   ```sql
   USE bokji_auth;
   SELECT id, username, created_at FROM users;   -- 비번 해시/암호문은 굳이 안 봄
   ```

---

## 3. 스키마 (참고 — 코드가 자동 생성한다)

`src/rag_chatbot/auth/_mysql.py` 의 `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS users (
    id                  BIGINT       NOT NULL AUTO_INCREMENT,
    username            VARCHAR(254) NOT NULL,          -- 이메일. utf8mb4_unicode_ci → 대소문자 무시
    password_hash       VARCHAR(255) NOT NULL,          -- bcrypt / pbkdf2 문자열
    display_name_enc    TEXT         NULL,              -- Fernet 암호문
    region              VARCHAR(64)  NULL,              -- 시/도 평문
    interests_enc       TEXT         NULL,              -- 관심조건 JSON, Fernet 암호문
    marketing_opt_in    TINYINT      NOT NULL DEFAULT 0,
    created_at          VARCHAR(32)  NOT NULL,          -- ISO8601 문자열(백엔드 독립)
    updated_at          VARCHAR(32)  NOT NULL,
    password_changed_at VARCHAR(32)  NULL,
    failed_login_count  INT          NOT NULL DEFAULT 0,
    locked_until        VARCHAR(32)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

SQLite 스키마(`repository.py` 의 `_SCHEMA`)와 컬럼 의미가 1:1 로 같다. 시간
값을 `DATETIME` 이 아니라 문자열로 저장하는 이유는 `service._parse_ts` 가
문자열을 그대로 파싱하기 때문 — 백엔드를 바꿔도 시간 처리 코드는 손대지
않는다.

---

## 4. 운영 메모

- **백업**: 주기적으로
  ```
  mysqldump -h skn33.iptime.org -P <port> -u root -p bokji_auth users > users_backup_$(date +%F).sql
  ```
  서버가 자체 관리 서버라도 실수로 `DROP` 하면 끝이므로 덤프를 따로 남긴다.
- **가용성**: RunPod Pod 와 달리 상시 운영되는 서버이므로 Start/Stop 과금
  같은 건 없다. 다만 서버 재부팅/점검 시 DB 가 일시적으로 끊길 수 있으니
  팀 내에서 점검 일정은 공유한다.
- **탈퇴 시 잔재**: SQLite 백엔드는 `secure_delete` PRAGMA 로 삭제 페이지를
  덮지만, MySQL 은 평범한 `DELETE` 다. 저장소 수준 소거는 이 MariaDB 서버의
  운영 정책에 달려 있다.
- **다른 호스팅으로 다시 옮길 때**: 코드는 `AUTH_DB_URL` 만 보므로, 이후
  다른 서버/관리형 MySQL(RunPod, Railway, PlanetScale, Aiven 등 무엇이든)로
  옮기더라도 URL 만 바꾸면 그대로 붙는다 — 이 문서의 1장만 그 호스팅
  환경에 맞게 다시 쓰면 된다.

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
- 기존 `tests/test_auth.py` / `tests/test_streamlit_auth_integration.py` 는
  SQLite 그대로라 영향 없다.
