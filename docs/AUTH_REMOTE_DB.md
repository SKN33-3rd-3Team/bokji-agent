# 회원 DB 를 RunPod 의 MySQL/MariaDB 로 (로그인·회원가입 원격화)

로그인/회원가입은 기본적으로 로컬 **SQLite**(`.runtime/auth.db`)에 회원을
저장한다. 팀원 여러 명이 같은 회원 계정을 공유하거나 시연용으로 한 곳에
모으려면, `AUTH_DB_URL` 환경변수 하나로 **원격 MySQL/MariaDB** 로 바꿀 수
있다. 코드는 이미 그 분기를 지원한다(`src/rag_chatbot/auth/repository.py` 의
`get_backend`, `src/rag_chatbot/auth/_mysql.py`).

이 문서는 그 원격 DB 를 **RunPod Pod** 에 MariaDB 로 띄우는 절차다. RunPod
콘솔 화면은 수시로 바뀌므로 메뉴 이름이 조금 다를 수 있다 — 개념 순서대로
따라가면 된다.

> RunPod 의 LLM Serverless 연동(N1/N5/N9/N10/N13)은 별개다. 그건
> `docs/RUNPOD_SETUP_DRAFT.md` 를 본다. 회원 DB 는 **Serverless 가 아니라
> 상시 실행되는 Pod** 여야 한다(요청이 없을 때도 DB 가 살아 있어야 하므로).

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

## 1. RunPod 에서 해야 하는 것

### 1-1. Network Volume 먼저 만든다 (필수)

Pod 의 컨테이너 디스크는 **Pod 를 Stop/삭제하면 사라진다.** 회원 데이터를
보존하려면 Network Volume(영속 스토리지)을 붙이고 MariaDB 데이터 디렉터리를
거기로 둬야 한다.

1. RunPod 콘솔 → **Storage → Network Volumes → New**
2. 리전(데이터센터) 선택, 크기 예: **5 GB** (회원 수천 명이면 충분)
3. 이름 예: `bokji-auth-db`

Network Volume 은 만든 리전에 묶인다 — 다음 단계 Pod 도 **같은 리전**에서
띄운다.

### 1-2. MariaDB Pod 배포

1. RunPod 콘솔 → **Pods → Deploy**
2. GPU 는 필요 없다. **CPU Pod** 중 가장 작은 것(또는 가장 싼 GPU 없는 옵션).
3. **Container Image** 에 공식 이미지 지정: `mariadb:11`
4. **Volume** : 1-1 에서 만든 Network Volume 을 선택하고
   **Mount Path 를 `/var/lib/mysql`** 로 지정.
5. **Environment Variables** :
   | 이름 | 값 |
   | --- | --- |
   | `MARIADB_ROOT_PASSWORD` | 길고 무작위한 문자열 |
   | `MARIADB_DATABASE` | `bokji` |
   | `MARIADB_USER` | `bokji_app` |
   | `MARIADB_PASSWORD` | 길고 무작위한 문자열(위 root 와 다르게) |
6. **Expose TCP Port** 에 `3306` 추가. (HTTP 가 아니라 **TCP** 포트여야 한다.)
7. Deploy.

### 1-3. 공인 접속 주소 확인

Pod 가 Running 이 되면 상세 화면의 **Connect → TCP Port Mappings** 에
`3306` 에 대응하는 공인 주소가 나온다. 예:

```
213.xxx.xxx.xxx : 40123   ->  내부 3306
```

이 `호스트:포트` 가 `AUTH_DB_URL` 에 들어갈 값이다. (RunPod 이 매핑하는
외부 포트는 3306 이 아닐 수 있다 — 표시된 값을 그대로 쓴다.)

### 1-4. 앱 전용 계정 점검 (선택이지만 권장)

`mariadb:11` 이미지는 위 env 로 `bokji_app` 을 만들고 `bokji` DB 에 대해
`ALL PRIVILEGES` 를 준다. 첫 실행 때 앱이 `users` 테이블을 자동 생성하므로
(`init_schema`), **이 계정에는 `CREATE` 권한이 반드시 있어야 한다.** 계정을
직접 다시 잡을 때도 `bokji` 스키마 한정으로 넓게 준다. 로컬에서 `mysql`
클라이언트나 DBeaver 로 root 접속 후:

```sql
-- bokji_app 이 bokji.* 에만 권한이 있는지 확인
SHOW GRANTS FOR 'bokji_app'@'%';

-- 없거나 스키마 범위가 틀리면 다시 잡는다
CREATE DATABASE IF NOT EXISTS bokji
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'bokji_app'@'%' IDENTIFIED BY '<password>';
-- bokji 스키마 한정 전체 권한 (CREATE 포함 — 첫 실행 시 테이블 자동 생성에 필요).
-- 테이블을 root 로 미리 만들어 뒀다면 DML 만 줘도 된다:
--   GRANT SELECT, INSERT, UPDATE, DELETE ON bokji.* TO 'bokji_app'@'%';
GRANT ALL PRIVILEGES ON bokji.* TO 'bokji_app'@'%';
FLUSH PRIVILEGES;
```

> 앱은 `bokji` 밖의 스키마나 서버 전역 권한은 전혀 필요로 하지 않는다 —
> `ON bokji.*` 범위를 벗어나지 않게만 한다.

> **보안 주의**: RunPod 의 공인 TCP 포트는 인터넷에 열려 있다. 반드시
> - 길고 무작위한 비밀번호(20자 이상 권장)를 쓰고,
> - 애플리케이션 계정은 `bokji` DB 로만 권한을 제한하고,
> - `AUTH_DB_URL` 이 든 `.env` 는 커밋하지 않는다(이미 `.gitignore` 처리됨).
> RunPod 콘솔에 IP 화이트리스트 기능이 있으면 팀 IP 만 허용한다.

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
   AUTH_DB_URL=mysql://bokji_app:<password>@<runpod-host>:<runpod-port>/bokji
   AUTH_ENC_KEY=<팀이 공유하는 동일한 키>
   # 선택: 원격 DB 응답 없을 때 대기 시간(초)
   AUTH_DB_CONNECT_TIMEOUT=10
   ```
   `AUTH_ENC_KEY` 새 키 생성(최초 1회, 팀에 공유):
   ```
   python src/rag_chatbot/auth/__main__.py keygen
   ```

3. 연결·스키마 생성 확인 — 아무 회원가입 한 번이면 `users` 테이블이
   자동으로 만들어진다:
   ```
   streamlit run app.py
   ```
   → 로그인 화면 → 회원가입 → 다시 로그인. 성공하면 완료다.

   DB 가 꺼져 있거나 주소가 틀리면 화면에 "회원 데이터베이스에 연결할 수
   없습니다" 안내가 뜬다(앱이 죽지는 않는다 —
   `AuthBackendUnavailableError`).

4. (선택) DBeaver / `mysql` CLI 로 확인:
   ```sql
   USE bokji;
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
  mysqldump -h <host> -P <port> -u root -p bokji users > users_backup_$(date +%F).sql
  ```
  Network Volume 이 있어도 실수로 `DROP` 하면 끝이므로 덤프를 따로 남긴다.
- **비용**: Pod 는 **켜 있는 내내 과금**된다(Serverless 와 다름). 시연 기간에만
  Start 하고 평소엔 Stop 해도 된다 — 데이터는 Network Volume 에 남는다.
  (Stop 후 첫 Start 때 MariaDB 가 다시 뜨는 몇십 초는 감안.)
- **탈퇴 시 잔재**: SQLite 백엔드는 `secure_delete` PRAGMA 로 삭제 페이지를
  덮지만, MySQL 은 평범한 `DELETE` 다. 저장소 수준 소거는 RunPod/MariaDB
  운영 정책에 달려 있다.
- **RunPod 말고**: 코드는 `AUTH_DB_URL` 만 보므로, 상시 과금이 부담되면
  무료 관리형 MySQL(예: Railway / PlanetScale / Aiven 무료 티어)로 URL 만
  바꿔 그대로 붙일 수 있다. RunPod 은 관리형 DB 를 제공하지 않아 Pod +
  직접 운영이 된다는 점만 다르다.

---

## 5. 문제 해결

| 증상 | 원인/조치 |
| --- | --- |
| 화면에 "회원 데이터베이스에 연결할 수 없습니다" | Pod 가 Stop 상태이거나 `AUTH_DB_URL` 호스트/포트 오타. TCP Port Mappings 값을 다시 확인 |
| 화면에 "회원 DB 설정(AUTH_DB_URL)이 올바르지 않습니다" | URL 형식 오류(스킴/포트/`/dbname` 누락). `mysql://user:pass@host:port/dbname` 형태인지 확인 |
| 화면에 "회원 테이블(users)을 준비하지 못했습니다 … CREATE 권한" | 앱 계정에 `bokji.*` 의 `CREATE` 권한이 없음(1-4 참고). 또는 root 로 테이블을 미리 만든다 |
| "AUTH_DB_URL 이 설정됐지만 pymysql 이 없습니다" | `pip install pymysql` |
| 로그인은 되는데 이름/관심조건이 빈칸 | `AUTH_ENC_KEY` 가 가입 때와 다른 값. 팀이 같은 키를 공유해야 함 |
| `Access denied for user` | 계정/비밀번호 오타 또는 `bokji_app` 권한 미부여(1-4 참고) |
| 한글 이름이 깨져 저장됨 | DB/테이블이 `utf8mb4` 인지 확인(`init_schema` 가 만들면 자동으로 맞음) |

---

## 6. 테스트

- `tests/test_auth_remote_db.py` — URL 파싱·백엔드 선택·연결 실패는 항상 실행,
  실제 DB 왕복(회원가입~탈퇴)은 `AUTH_TEST_DB_URL` 이 있을 때만:
  ```
  AUTH_TEST_DB_URL=mysql://bokji_app:<pw>@<host>:<port>/bokji_test \
      python -m pytest tests/test_auth_remote_db.py -q
  ```
  (운영 DB 가 아니라 `bokji_test` 같은 별도 DB 를 쓴다 — 테스트가 행을
  만들었다 지운다.)
- 기존 `tests/test_auth.py` / `tests/test_streamlit_auth_integration.py` 는
  SQLite 그대로라 영향 없다.
