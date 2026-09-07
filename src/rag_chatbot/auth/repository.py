"""회원 ``users`` 테이블 — 스키마와 저수준 CRUD.

기본은 **SQLite**(``.runtime/auth.db``)이고, ``AUTH_DB_URL`` 환경변수를 주면
**원격 MySQL/MariaDB**(예: RunPod Pod)로 붙는다. 두 경우 모두 같은
:class:`Backend` 인터페이스를 통해 ``service`` 가 호출한다 — ``service`` 는
어느 백엔드인지 알 필요가 없다.

이 모듈은 암호화/해싱을 하지 않는다. 호출자(``service``)가 이미 해시·암호문을
만들어 넘긴다. 중복 아이디는 :class:`DuplicateUsername` 로 통일해서 올려보내고
(백엔드별 예외를 감춘다), 사용자용 예외 변환은 ``service`` 가 한다.

경로/주소 결정
--------------
- ``AUTH_DB_URL`` 이 있으면 그 URL 로 MySQL/MariaDB 에 붙는다
  (``mysql://user:pass@host:port/dbname``). ``docs/AUTH_REMOTE_DB.md`` 참고.
- 없으면 SQLite: ``AUTH_DB_PATH`` -> 없으면 ``.runtime/auth.db``.
- ``service`` 함수에 ``db_path`` 를 명시하면 (테스트 등) ``AUTH_DB_URL`` 과
  무관하게 그 SQLite 파일을 쓴다.

컬럼
----
- ``display_name_enc`` : 표시 이름, Fernet 암호문 (없으면 NULL)
- ``region``           : 시/도, 평문 (민감정보 아님)
- ``interests_enc``    : 관심 지원조건 JSON 배열, Fernet 암호문 (장애·보훈·
                         기초수급 등 민감 범주가 섞일 수 있어 암호화한다)
- ``marketing_opt_in`` : 0/1
- ``failed_login_count``: 연속 로그인 실패 횟수 (성공 시 0으로 초기화)
- ``locked_until``     : 계정 잠금 해제 시각, ISO8601 UTC (없으면 NULL)

타임스탬프는 MySQL 에서도 ``DATETIME`` 이 아니라 ISO8601 **문자열** 컬럼으로
저장한다 — ``service`` 의 ``_parse_ts`` 가 문자열을 그대로 파싱하므로 백엔드가
바뀌어도 시간 처리 코드는 손댈 필요가 없다.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = _ROOT / ".runtime" / "auth.db"
_ENV_DB = "AUTH_DB_PATH"
_ENV_DB_URL = "AUTH_DB_URL"

# 신규 DB는 이 스키마로 바로 만들어진다.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash       TEXT NOT NULL,
    display_name_enc    TEXT,
    region              TEXT,
    interests_enc       TEXT,
    marketing_opt_in    INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    password_changed_at TEXT,
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    locked_until        TEXT
);
"""

# 예전 버전 DB에 없을 수 있는 컬럼 — 있으면 건너뛰고 없으면 ADD COLUMN.
# (SQLite 전용. 원격 MySQL 은 항상 최신 스키마로 새로 만든다.)
_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("region", "region TEXT"),
    ("interests_enc", "interests_enc TEXT"),
    ("marketing_opt_in", "marketing_opt_in INTEGER NOT NULL DEFAULT 0"),
    ("failed_login_count", "failed_login_count INTEGER NOT NULL DEFAULT 0"),
    ("locked_until", "locked_until TEXT"),
)

# "이 컬럼은 건드리지 마라"(_UNSET)와 "NULL 로 지워라"(None)를 구분하는 센티넬.
_UNSET = object()


class DuplicateUsername(Exception):
    """이미 존재하는 아이디로 INSERT 시도. 백엔드별 무결성 예외를 통일한 것."""


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------
def resolve_db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    env_value = os.environ.get(_ENV_DB, "").strip()
    return Path(env_value) if env_value else DEFAULT_DB_PATH


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_db_url(url: str) -> dict[str, Any]:
    """``mysql://user:pass@host:port/dbname`` -> 연결 파라미터 딕셔너리.

    ``mysql`` / ``mysql+pymysql`` / ``mariadb`` 스킴만 받는다. 포트 기본값은
    3306, 경로(``/dbname``)는 필수. user/password 는 percent-decode 한다.
    다른 스킴이면 :class:`ValueError`.
    """

    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"mysql", "mysql+pymysql", "mariadb"}:
        raise ValueError(
            f"지원하지 않는 AUTH_DB_URL 스킴입니다: {parsed.scheme!r} "
            f"(mysql:// 또는 mariadb:// 를 쓰세요)"
        )
    database = (parsed.path or "").lstrip("/")
    if not database:
        raise ValueError("AUTH_DB_URL 에 데이터베이스 이름(/dbname)이 없습니다.")
    if not parsed.hostname:
        raise ValueError("AUTH_DB_URL 에 호스트가 없습니다.")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else "",
        "password": unquote(parsed.password) if parsed.password else "",
        "database": database,
    }


# ---------------------------------------------------------------------------
# SQLite 저수준 CRUD (= SqliteBackend 의 구현)
# ---------------------------------------------------------------------------
def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    have = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    for name, ddl in _COLUMN_MIGRATIONS:
        if name not in have:
            conn.execute(f"ALTER TABLE users ADD COLUMN {ddl}")
    conn.commit()


def insert_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password_hash: str,
    display_name_enc: str | None,
    region: str | None = None,
    interests_enc: str | None = None,
    marketing_opt_in: bool = False,
) -> tuple[int, str]:
    """``(user_id, created_at)`` 를 돌려준다. 중복이면 :class:`DuplicateUsername`."""

    now = _utcnow()
    try:
        cur = conn.execute(
            "INSERT INTO users "
            "(username, password_hash, display_name_enc, region, interests_enc, "
            "marketing_opt_in, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                username,
                password_hash,
                display_name_enc,
                region,
                interests_enc,
                1 if marketing_opt_in else 0,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateUsername(str(exc)) from exc
    conn.commit()
    return int(cur.lastrowid), now


def get_user_by_username(
    conn: sqlite3.Connection, username: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()


def set_password_hash(
    conn: sqlite3.Connection, user_id: int, password_hash: str
) -> None:
    now = _utcnow()
    conn.execute(
        "UPDATE users SET password_hash = ?, updated_at = ?, "
        "password_changed_at = ? WHERE id = ?",
        (password_hash, now, now, user_id),
    )
    conn.commit()


def set_login_security(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    failed_login_count: int,
    locked_until: str | None,
) -> None:
    """연속 로그인 실패 횟수와 잠금 해제 시각을 갱신한다.

    ``locked_until`` 은 ISO8601 UTC 문자열이거나 ``None``(잠금 없음)이다.
    ``updated_at`` 은 건드리지 않는다(로그인 시도는 프로필 변경이 아니다).
    """

    conn.execute(
        "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
        (int(failed_login_count), locked_until, user_id),
    )
    conn.commit()


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    """회원 행과 그 내용을 삭제한다 (탈퇴).

    - ``PRAGMA secure_delete=ON`` : 삭제되는 페이지 내용(이메일·비밀번호 해시·
      암호문)을 0으로 덮어쓴다. 기본값(OFF)이면 free page 에 바이트가 남는다.
    - ``wal_checkpoint(TRUNCATE)`` : 변경을 메인 DB 로 flush 하고 ``-wal`` 파일을
      잘라, WAL 에도 잔재가 남지 않게 한다.

    파일 크기 축소(``VACUUM``)나 디스크 물리 소거까지는 하지 않는다.
    """

    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        # 다른 연결이 붙어 있거나 WAL 모드가 아니면 조용히 넘어간다.
        pass


def update_profile_fields(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    display_name_enc: object = _UNSET,
    region: object = _UNSET,
    interests_enc: object = _UNSET,
) -> None:
    """전달된 컬럼만 UPDATE 한다. ``_UNSET`` 인자는 손대지 않는다."""

    sets: list[str] = []
    params: list[object] = []
    for column, value in (
        ("display_name_enc", display_name_enc),
        ("region", region),
        ("interests_enc", interests_enc),
    ):
        if value is not _UNSET:
            sets.append(f"{column} = ?")
            params.append(value)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(_utcnow())
    params.append(user_id)
    conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


# ---------------------------------------------------------------------------
# 백엔드 추상화
# ---------------------------------------------------------------------------
class SqliteBackend:
    """기본 백엔드 — 위의 모듈 함수들에 그대로 위임한다(동작 불변)."""

    kind = "sqlite"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def init_schema(self, conn: sqlite3.Connection) -> None:
        init_schema(conn)

    def insert_user(self, conn: sqlite3.Connection, **kw: Any) -> tuple[int, str]:
        return insert_user(conn, **kw)

    def get_user_by_username(
        self, conn: sqlite3.Connection, username: str
    ) -> Mapping[str, Any] | None:
        return get_user_by_username(conn, username)

    def set_password_hash(
        self, conn: sqlite3.Connection, user_id: int, password_hash: str
    ) -> None:
        set_password_hash(conn, user_id, password_hash)

    def set_login_security(
        self, conn: sqlite3.Connection, user_id: int, **kw: Any
    ) -> None:
        set_login_security(conn, user_id, **kw)

    def delete_user(self, conn: sqlite3.Connection, user_id: int) -> None:
        delete_user(conn, user_id)

    def update_profile_fields(
        self, conn: sqlite3.Connection, user_id: int, **kw: Any
    ) -> None:
        update_profile_fields(conn, user_id, **kw)


def get_backend(db_path: str | Path | None = None):
    """쓸 백엔드를 결정한다.

    1. ``db_path`` 를 명시하면 (테스트 등) 항상 그 SQLite 파일.
    2. ``AUTH_DB_URL`` 이 있으면 원격 MySQL/MariaDB.
    3. 둘 다 없으면 기본 SQLite(``AUTH_DB_PATH`` -> ``.runtime/auth.db``).
    """

    if db_path is not None:
        return SqliteBackend(db_path)
    url = os.environ.get(_ENV_DB_URL, "").strip()
    if url:
        dsn = parse_db_url(url)  # 스킴/형식 오류는 pymysql 유무와 무관하게 먼저 잡는다
        from ._mysql import MySQLBackend  # pymysql 은 이때만 필요

        return MySQLBackend(dsn)
    return SqliteBackend(None)
