"""원격 MySQL/MariaDB 백엔드 (예: RunPod Pod 의 MariaDB 컨테이너).

``AUTH_DB_URL`` 이 설정돼 있을 때만 ``repository.get_backend`` 이 이 모듈을
import 한다. ``pymysql`` (순수 파이썬, 빌드 도구 불필요) 이 필요하다:

    pip install pymysql        # 또는  pip install -r requirements-auth.txt

배포 절차는 ``docs/AUTH_REMOTE_DB.md`` 참고.

SQLite 백엔드와 동일한 :class:`~rag_chatbot.auth.repository.SqliteBackend`
인터페이스를 구현한다. 차이는 방언뿐이다:

- 파라미터 자리표시자 ``%s`` (SQLite 는 ``?``)
- 스키마 DDL 이 MySQL 방언 (``BIGINT AUTO_INCREMENT`` 등)
- ``username`` 대소문자 무시는 컬럼 콜레이션(``utf8mb4_unicode_ci``)이 담당
- 중복키(errno 1062) -> :class:`~rag_chatbot.auth.repository.DuplicateUsername`
- 탈퇴는 평범한 ``DELETE`` (SQLite 의 ``secure_delete`` PRAGMA 는 없음)

타임스탬프는 ``DATETIME`` 이 아니라 ISO8601 **문자열**(``VARCHAR``)로 저장한다 —
``service._parse_ts`` 가 문자열을 그대로 파싱하므로 시간 처리 코드가 백엔드에
독립적이다.
"""

from __future__ import annotations

import functools
import os
from typing import Any, Mapping

from .repository import _UNSET, DuplicateUsername, _utcnow
from .service import AuthBackendUnavailableError

try:  # AUTH_DB_URL 을 설정한 사람만 필요
    import pymysql
    from pymysql.cursors import DictCursor
    from pymysql.err import IntegrityError as _MySQLIntegrityError
    from pymysql.err import MySQLError as _MySQLError
    from pymysql.err import OperationalError as _MySQLOperationalError
except ImportError as exc:  # pragma: no cover - 설치 환경에 따라 갈림
    raise AuthBackendUnavailableError(
        "AUTH_DB_URL 이 설정됐지만 pymysql 이 없습니다. "
        "`pip install pymysql` 또는 `pip install -r requirements-auth.txt` 후 "
        "다시 실행하세요."
    ) from exc

_DUP_ENTRY_ERRNO = 1062

# CREATE TABLE 을 매 로그인마다 왕복시키지 않도록, (host,port,db) 별로 한 번만.
_schema_ready: set[tuple[str, int, str]] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  BIGINT       NOT NULL AUTO_INCREMENT,
    username            VARCHAR(254) NOT NULL,
    password_hash       VARCHAR(255) NOT NULL,
    display_name_enc    TEXT         NULL,
    region              VARCHAR(255) NULL,
    interests_enc       TEXT         NULL,
    marketing_opt_in    TINYINT      NOT NULL DEFAULT 0,
    created_at          VARCHAR(32)  NOT NULL,
    updated_at          VARCHAR(32)  NOT NULL,
    password_changed_at VARCHAR(32)  NULL,
    failed_login_count  INT          NOT NULL DEFAULT 0,
    locked_until        VARCHAR(32)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _as_backend_unavailable(fn):
    """CRUD 경계에서 raw 드라이버 예외(``_MySQLError``)가 새어나가지 않게 감싼다.

    ``connect()`` 는 성공했지만 그 뒤 CRUD 도중 연결이 끊기는 경우가 있다
    (``wait_timeout`` 만료, 서버 재시작, ``read_timeout``/``write_timeout``,
    "Lost connection to MySQL server during query" 등). 이때 pymysql 예외를
    그대로 두면 ``service`` 를 거쳐 화면단까지 올라가는데, 화면단은
    ``except AuthError`` 만 잡으므로 사용자에게 트레이스백이 노출된다.
    ``connect()``/``init_schema()`` 와 동일하게
    :class:`AuthBackendUnavailableError` 로 통일한다.

    ``DuplicateUsername`` 은 ``_MySQLError`` 가 아니므로 그대로 통과한다.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _MySQLError as exc:
            raise AuthBackendUnavailableError(
                "회원 데이터베이스 처리 중 연결이 끊겼습니다. RunPod Pod 가 "
                "실행 중인지 확인하고 잠시 후 다시 시도해 주세요."
            ) from exc

    return wrapper


def _connect_timeout() -> int:
    raw = os.environ.get("AUTH_DB_CONNECT_TIMEOUT", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 10
    return value if value > 0 else 10


class MySQLBackend:
    """원격 MySQL/MariaDB 백엔드. :class:`SqliteBackend` 와 같은 인터페이스."""

    kind = "mysql"

    def __init__(self, dsn: Mapping[str, Any]) -> None:
        self._dsn = dict(dsn)
        self._key = (
            str(self._dsn.get("host")),
            int(self._dsn.get("port", 3306)),
            str(self._dsn.get("database")),
        )

    # -- 연결 -----------------------------------------------------------
    def connect(self):
        try:
            return pymysql.connect(
                host=self._dsn["host"],
                port=int(self._dsn.get("port", 3306)),
                user=self._dsn.get("user") or "",
                password=self._dsn.get("password") or "",
                database=self._dsn["database"],
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
                connect_timeout=_connect_timeout(),
                read_timeout=30,
                write_timeout=30,
            )
        except _MySQLOperationalError as exc:
            raise AuthBackendUnavailableError(
                "회원 데이터베이스에 연결할 수 없습니다. RunPod Pod 가 실행 "
                "중인지, AUTH_DB_URL 의 호스트/포트/계정/DB이름이 맞는지 "
                "확인하세요."
            ) from exc
        except _MySQLError as exc:  # 그 외 드라이버 오류도 사용자에겐 동일 메시지
            raise AuthBackendUnavailableError(
                "회원 데이터베이스 연결에 실패했습니다."
            ) from exc

    def init_schema(self, conn) -> None:
        if self._key in _schema_ready:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()
        except _MySQLError as exc:
            # 대개 계정에 CREATE 권한이 없을 때. 화면단이 안내만 하도록
            # AuthError 계열로 바꿔 던진다(raw 드라이버 예외 노출 방지).
            raise AuthBackendUnavailableError(
                "회원 테이블(users)을 준비하지 못했습니다. DB 계정에 해당 "
                "스키마의 CREATE 권한이 있는지 확인하세요 "
                "(docs/AUTH_REMOTE_DB.md)."
            ) from exc
        _schema_ready.add(self._key)

    # -- CRUD ---------------------------------------------------------------
    # 각 메서드는 @_as_backend_unavailable 로 감싸 CRUD 도중 연결이 끊기면
    # raw pymysql 예외 대신 AuthBackendUnavailableError 가 올라가게 한다.
    @_as_backend_unavailable
    def insert_user(
        self,
        conn,
        *,
        username: str,
        password_hash: str,
        display_name_enc: str | None,
        region: str | None = None,
        interests_enc: str | None = None,
        marketing_opt_in: bool = False,
    ) -> tuple[int, str]:
        now = _utcnow()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users "
                    "(username, password_hash, display_name_enc, region, "
                    "interests_enc, marketing_opt_in, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
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
                new_id = int(cur.lastrowid)
        except _MySQLIntegrityError as exc:
            if exc.args and exc.args[0] == _DUP_ENTRY_ERRNO:
                raise DuplicateUsername(str(exc)) from exc
            raise
        conn.commit()
        return new_id, now

    @_as_backend_unavailable
    def get_user_by_username(self, conn, username: str) -> Mapping[str, Any] | None:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            return cur.fetchone()

    @_as_backend_unavailable
    def set_password_hash(self, conn, user_id: int, password_hash: str) -> None:
        now = _utcnow()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s, updated_at = %s, "
                "password_changed_at = %s WHERE id = %s",
                (password_hash, now, now, user_id),
            )
        conn.commit()

    @_as_backend_unavailable
    def set_login_security(
        self,
        conn,
        user_id: int,
        *,
        failed_login_count: int,
        locked_until: str | None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET failed_login_count = %s, locked_until = %s "
                "WHERE id = %s",
                (int(failed_login_count), locked_until, user_id),
            )
        conn.commit()

    @_as_backend_unavailable
    def delete_user(self, conn, user_id: int) -> None:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()

    @_as_backend_unavailable
    def update_profile_fields(
        self,
        conn,
        user_id: int,
        *,
        display_name_enc: object = _UNSET,
        region: object = _UNSET,
        interests_enc: object = _UNSET,
    ) -> None:
        sets: list[str] = []
        params: list[object] = []
        for column, value in (
            ("display_name_enc", display_name_enc),
            ("region", region),
            ("interests_enc", interests_enc),
        ):
            if value is not _UNSET:
                sets.append(f"{column} = %s")
                params.append(value)
        if not sets:
            return
        sets.append("updated_at = %s")
        params.append(_utcnow())
        params.append(user_id)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = %s", params
            )
        conn.commit()


__all__ = ["MySQLBackend"]
