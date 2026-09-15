"""원격 회원 DB(AUTH_DB_URL)가 실제로 붙는지, 안 되면 **어디서** 막히는지 본다.

실행:
    pip install pymysql
    python scripts/check_auth_db.py
    python scripts/check_auth_db.py --url mysql://user:pass@host:port/bokji
    python scripts/check_auth_db.py --url mysql://user:pass@host:port/bokji_test

왜 필요한가: "RunPod 포트에 MariaDB가 제대로 올라갔나?" 를 한눈에 확인한다.
RunPod 의 TCP 포트 매핑은 뒤에 아무것도 없어도 "열린 것처럼" 보일 수 있어서,
포트 도달 → 서버 응답 → 로그인 → DB 선택 → users 테이블을 단계별로 나눠 찍는다.

확인 순서
---------
1. AUTH_DB_URL(또는 --url / AUTH_TEST_DB_URL)이 있고 형식이 맞는지
2. pymysql 설치 여부
3. host:port 로 TCP 가 열리는지 (여기서 막히면 Pod 가 꺼졌거나 포트 매핑 문제)
4. 그 포트 뒤에 진짜 MySQL/MariaDB 가 있는지 (SELECT VERSION())
5. 계정으로 로그인되고 대상 DB 가 존재하는지
6. users 테이블이 있는지 / 행 수
7. 실패 단계별 조치 안내
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:  # dotenv 없어도 환경변수만으로 동작
    pass

from rag_chatbot.auth.repository import parse_db_url  # noqa: E402

_OK = "[OK]"
_FAIL = "[실패]"
_INFO = "[안내]"
_DOC = "docs/AUTH_REMOTE_DB.md"


def _mask_url(dsn: dict) -> str:
    return (
        f"mysql://{dsn['user']}:***@{dsn['host']}:{dsn['port']}/{dsn['database']}"
    )


def _resolve_url(cli_url: str | None) -> str | None:
    if cli_url:
        return cli_url
    for name in ("AUTH_DB_URL", "AUTH_TEST_DB_URL"):
        value = os.environ.get(name, "").strip()
        if value:
            print(f"{_INFO} {name} 사용")
            return value
    return None


def _check_tcp(host: str, port: int, timeout: float) -> bool:
    print(f"\n{_INFO} TCP 연결 확인 - {host}:{port} (timeout {timeout:.0f}s)")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        print(f"{_FAIL} 포트에 붙지 못했습니다: {exc}")
        print("       -> RunPod Pod 가 Running 상태인지 확인하세요.")
        print("       -> Connect > TCP Port Mappings 의 '외부 IP:포트' 를 그대로")
        print(f"          썼는지 확인하세요(내부 3306 아님). {_DOC} 1-3 참고.")
        return False
    print(f"{_OK} 포트 열림 (무언가가 응답 대기 중)")
    return True


def _check_mysql(dsn: dict, timeout: float) -> int:
    try:
        import pymysql
        from pymysql.err import MySQLError, OperationalError
    except ImportError:
        print(f"{_FAIL} pymysql 미설치 -> pip install pymysql")
        return 1

    print(f"{_OK} pymysql 설치됨 (버전 {pymysql.__version__})")

    # 1) 먼저 DB 미지정으로 붙어서 '서버 자체'가 살아 있는지부터 본다.
    print(f"\n{_INFO} 서버 응답 확인 - 로그인 후 SELECT VERSION()")
    try:
        conn = pymysql.connect(
            host=dsn["host"],
            port=dsn["port"],
            user=dsn["user"],
            password=dsn["password"],
            connect_timeout=timeout,
            read_timeout=timeout,
        )
    except OperationalError as exc:
        code = exc.args[0] if exc.args else None
        print(f"{_FAIL} 로그인 실패 (errno {code}): {exc.args[-1] if exc.args else exc}")
        if code == 1045:
            print("       -> 계정/비밀번호가 틀립니다. 비밀번호에 @ / : # % 가 있으면")
            print("          URL 에서 percent-encoding 하세요(@ -> %40).")
        elif code in (2003, 2002):
            print("       -> 포트는 열렸지만 MySQL 핸드셰이크가 안 됩니다. 그 포트 뒤에")
            print("          MariaDB 가 아니라 다른 게 떠 있을 수 있습니다.")
        else:
            print(f"       -> {_DOC} 5. 문제 해결 표 참고.")
        return 1
    except MySQLError as exc:
        print(f"{_FAIL} 서버 연결 실패: {exc}")
        return 1

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute("SELECT CURRENT_USER()")
            whoami = cur.fetchone()[0]
        print(f"{_OK} MariaDB/MySQL 살아 있음 - 버전 {version}, 접속 계정 {whoami}")

        # 2) 대상 DB 존재?
        with conn.cursor() as cur:
            cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME = %s",
                (dsn["database"],),
            )
            has_db = cur.fetchone() is not None
        if not has_db:
            print(f"{_FAIL} DB '{dsn['database']}' 가 없습니다.")
            print(f"       -> root 로:  CREATE DATABASE {dsn['database']} "
                  "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
            print(f"       -> 그리고:   GRANT ALL PRIVILEGES ON {dsn['database']}.* "
                  f"TO '{dsn['user']}'@'%'; FLUSH PRIVILEGES;")
            return 1
        print(f"{_OK} DB '{dsn['database']}' 존재")

        # 3) users 테이블 / 행 수 (없어도 정상 - 첫 회원가입 때 자동 생성됨)
        with conn.cursor() as cur:
            cur.execute("USE `%s`" % dsn["database"].replace("`", ""))
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users'",
                (dsn["database"],),
            )
            has_users = cur.fetchone()[0] > 0
            if has_users:
                cur.execute("SELECT COUNT(*) FROM users")
                n = cur.fetchone()[0]
                print(f"{_OK} users 테이블 있음 - 현재 {n}행")
            else:
                print(f"{_INFO} users 테이블은 아직 없음 (정상). 첫 회원가입 때 "
                      "init_schema 가 만듭니다.")
                # 쓰기 권한(=CREATE) 있는지 가볍게 확인
                try:
                    with conn.cursor() as c2:
                        c2.execute(
                            "CREATE TABLE IF NOT EXISTS _auth_conn_probe "
                            "(x INT) ENGINE=InnoDB"
                        )
                        c2.execute("DROP TABLE _auth_conn_probe")
                    print(f"{_OK} 이 계정에 CREATE 권한 있음 (자동 스키마 생성 가능)")
                except MySQLError as exc:
                    print(f"{_FAIL} CREATE 권한이 없습니다: {exc}")
                    print(f"       -> GRANT ALL PRIVILEGES ON {dsn['database']}.* "
                          f"TO '{dsn['user']}'@'%'; ({_DOC} 1-4)")
                    return 1
    finally:
        conn.close()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="원격 회원 DB(AUTH_DB_URL) 연결 진단")
    parser.add_argument("--url", default=None,
                        help="mysql://user:pass@host:port/dbname (생략 시 "
                             "AUTH_DB_URL -> AUTH_TEST_DB_URL 순서로 읽음)")
    parser.add_argument("--timeout", type=float, default=8.0,
                        help="각 단계 타임아웃(초, 기본 8)")
    args = parser.parse_args()

    print("=" * 68)
    print("원격 회원 DB 연결 진단")
    print("=" * 68)

    url = _resolve_url(args.url)
    if not url:
        print(f"{_FAIL} AUTH_DB_URL 이 없습니다.")
        print("       -> .env 에 AUTH_DB_URL=mysql://user:pass@host:port/bokji")
        print("       -> 또는 --url 로 직접 넘기세요.")
        return 1

    try:
        dsn = parse_db_url(url)
    except ValueError as exc:
        print(f"{_FAIL} URL 형식 오류: {exc}")
        print("       -> mysql://user:pass@host:port/dbname 형태여야 합니다.")
        return 1
    print(f"{_OK} URL 파싱됨: {_mask_url(dsn)}")

    if not _check_tcp(dsn["host"], dsn["port"], args.timeout):
        print("\n결론: 포트에 도달하지 못했습니다. DB 이전에 네트워크/Pod 문제입니다.")
        return 1

    rc = _check_mysql(dsn, args.timeout)
    if rc == 0:
        print("\n결론: 원격 회원 DB 정상. AUTH_DB_URL 을 .env 에 넣으면 로그인/"
              "회원가입이 이 DB 를 씁니다.")
    else:
        print(f"\n결론: 위 [실패] 단계를 해결하세요. 자세한 절차는 {_DOC}.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
