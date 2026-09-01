"""SQLite ``users`` 테이블 — 스키마와 저수준 CRUD.

이 모듈은 암호화/해싱을 하지 않는다. 호출자(``service``)가 이미 해시·암호문을
만들어 넘긴다. 중복 아이디는 ``sqlite3.IntegrityError`` 로 그대로 올려보내고,
사용자용 예외 변환은 ``service`` 가 한다.

경로: ``AUTH_DB_PATH`` 환경변수 -> 없으면 ``.runtime/auth.db``.

컬럼
----
- ``display_name_enc`` : 표시 이름, Fernet 암호문 (없으면 NULL)
- ``region``           : 시/도, 평문 (민감정보 아님)
- ``interests_enc``    : 관심 지원조건 JSON 배열, Fernet 암호문 (장애·보훈·
                         기초수급 등 민감 범주가 섞일 수 있어 암호화한다)
- ``marketing_opt_in`` : 0/1
- ``failed_login_count``: 연속 로그인 실패 횟수 (성공 시 0으로 초기화)
- ``locked_until``     : 계정 잠금 해제 시각, ISO8601 UTC (없으면 NULL)
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = _ROOT / ".runtime" / "auth.db"
_ENV_DB = "AUTH_DB_PATH"

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
_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("region", "region TEXT"),
    ("interests_enc", "interests_enc TEXT"),
    ("marketing_opt_in", "marketing_opt_in INTEGER NOT NULL DEFAULT 0"),
    ("failed_login_count", "failed_login_count INTEGER NOT NULL DEFAULT 0"),
    ("locked_until", "locked_until TEXT"),
)

# "이 컬럼은 건드리지 마라"(_UNSET)와 "NULL 로 지워라"(None)를 구분하는 센티넬.
_UNSET = object()


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    env_value = os.environ.get(_ENV_DB, "").strip()
    return Path(env_value) if env_value else DEFAULT_DB_PATH


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    """``(user_id, created_at)`` 를 돌려준다. 중복이면 ``sqlite3.IntegrityError``."""

    now = _utcnow()
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
