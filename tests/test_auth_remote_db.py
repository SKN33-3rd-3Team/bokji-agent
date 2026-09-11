"""원격 회원 DB(MySQL/MariaDB) 백엔드 — URL 파싱, 백엔드 선택, 연결 실패,
그리고 (환경변수가 있을 때만) 실제 DB 왕복.

라이브 테스트는 ``AUTH_TEST_DB_URL`` 이 설정돼 있을 때만 돈다. 예:

    AUTH_TEST_DB_URL=mysql://user:pass@host:3306/bokji_test \
        python -m pytest tests/test_auth_remote_db.py -q

설정돼 있지 않으면 파싱·선택 테스트만 돌고 나머지는 skip 된다. ``pymysql``
미설치 시 드라이버가 필요한 테스트도 skip 된다.
"""

from __future__ import annotations

import os
import unittest
import uuid

from rag_chatbot.auth import (
    AuthBackendUnavailableError,
    UsernameTakenError,
    authenticate,
    change_password,
    delete_account,
    get_profile,
    sign_up,
    update_profile,
)
from rag_chatbot.auth import repository as repo
from rag_chatbot.auth.crypto import generate_key

try:
    import pymysql  # noqa: F401

    _HAS_PYMYSQL = True
except ImportError:
    _HAS_PYMYSQL = False

_LIVE_URL = os.environ.get("AUTH_TEST_DB_URL", "").strip()
_GOOD_PW = "Abcd1234!"
_GOOD_PW2 = "Zyxw9876$"


class ParseDbUrlTests(unittest.TestCase):
    def test_basic(self):
        dsn = repo.parse_db_url("mysql://alice:s3cret@db.example.com:3307/bokji")
        self.assertEqual(dsn["host"], "db.example.com")
        self.assertEqual(dsn["port"], 3307)
        self.assertEqual(dsn["user"], "alice")
        self.assertEqual(dsn["password"], "s3cret")
        self.assertEqual(dsn["database"], "bokji")

    def test_default_port(self):
        self.assertEqual(
            repo.parse_db_url("mysql://u:p@h/bokji")["port"], 3306
        )

    def test_percent_encoded_password(self):
        dsn = repo.parse_db_url("mysql://u:p%40ss%2Fword@h:3306/bokji")
        self.assertEqual(dsn["password"], "p@ss/word")

    def test_mariadb_scheme_ok(self):
        self.assertEqual(
            repo.parse_db_url("mariadb://u:p@h/bokji")["database"], "bokji"
        )

    def test_pymysql_scheme_ok(self):
        self.assertEqual(
            repo.parse_db_url("mysql+pymysql://u:p@h/bokji")["database"], "bokji"
        )

    def test_rejects_other_scheme(self):
        with self.assertRaises(ValueError):
            repo.parse_db_url("postgres://u:p@h/bokji")

    def test_rejects_missing_dbname(self):
        with self.assertRaises(ValueError):
            repo.parse_db_url("mysql://u:p@h")

    def test_rejects_missing_host(self):
        with self.assertRaises(ValueError):
            repo.parse_db_url("mysql:///bokji")


class BackendSelectionTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("AUTH_DB_URL")
        os.environ.pop("AUTH_DB_URL", None)

    def tearDown(self):
        os.environ.pop("AUTH_DB_URL", None)
        if self._saved is not None:
            os.environ["AUTH_DB_URL"] = self._saved

    def test_no_env_is_sqlite(self):
        self.assertIsInstance(repo.get_backend(), repo.SqliteBackend)

    def test_explicit_db_path_forces_sqlite_even_with_url(self):
        os.environ["AUTH_DB_URL"] = "mysql://u:p@h/bokji"
        backend = repo.get_backend(db_path="/tmp/whatever.db")
        self.assertIsInstance(backend, repo.SqliteBackend)

    def test_bad_url_scheme_raises_valueerror_regardless_of_driver(self):
        os.environ["AUTH_DB_URL"] = "postgres://u:p@h/bokji"
        with self.assertRaises(ValueError):
            repo.get_backend()

    @unittest.skipUnless(_HAS_PYMYSQL, "pymysql 미설치")
    def test_url_selects_mysql_backend(self):
        from rag_chatbot.auth._mysql import MySQLBackend

        os.environ["AUTH_DB_URL"] = "mysql://u:p@h:3306/bokji"
        self.assertIsInstance(repo.get_backend(), MySQLBackend)

    def test_service_wraps_bad_url_as_backend_unavailable(self):
        # 형식 오류는 pymysql 유무와 무관하게 화면단이 다루기 쉬운
        # AuthBackendUnavailableError 로 올라와야 한다(raw ValueError 아님).
        os.environ["AUTH_DB_URL"] = "postgres://u:p@h/bokji"
        with self.assertRaises(AuthBackendUnavailableError):
            authenticate("someone@example.com", _GOOD_PW)

    def test_service_wraps_missing_dbname_as_backend_unavailable(self):
        os.environ["AUTH_DB_URL"] = "mysql://u:p@h:3306"
        with self.assertRaises(AuthBackendUnavailableError):
            authenticate("someone@example.com", _GOOD_PW)


class BackendUnavailableTests(unittest.TestCase):
    """원격 DB 가 응답하지 않을 때 AuthBackendUnavailableError 로 통일되는지."""

    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("AUTH_DB_URL", "AUTH_ENC_KEY", "AUTH_DB_CONNECT_TIMEOUT")
        }
        # 127.0.0.1:1 은 어떤 서비스도 듣고 있지 않은 포트 → 즉시 연결 거부.
        os.environ["AUTH_DB_URL"] = "mysql://u:p@127.0.0.1:1/bokji"
        os.environ["AUTH_ENC_KEY"] = generate_key()
        os.environ["AUTH_DB_CONNECT_TIMEOUT"] = "2"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @unittest.skipUnless(_HAS_PYMYSQL, "pymysql 미설치")
    def test_authenticate_raises_backend_unavailable(self):
        with self.assertRaises(AuthBackendUnavailableError):
            authenticate("someone@example.com", _GOOD_PW)

    @unittest.skipUnless(_HAS_PYMYSQL, "pymysql 미설치")
    def test_signup_raises_backend_unavailable(self):
        with self.assertRaises(AuthBackendUnavailableError):
            sign_up("someone@example.com", _GOOD_PW, "홍길동")


@unittest.skipUnless(_HAS_PYMYSQL, "pymysql 미설치")
class CrudDisconnectTests(unittest.TestCase):
    """connect() 는 성공했지만 CRUD 도중 연결이 끊기면(Lost connection 등)
    raw pymysql 예외가 아니라 AuthBackendUnavailableError 로 올라와야 한다."""

    def _backend(self):
        from rag_chatbot.auth._mysql import MySQLBackend

        return MySQLBackend(
            {"host": "h", "port": 3306, "user": "u", "password": "p",
             "database": "bokji"}
        )

    def _dead_conn(self):
        import pymysql

        class _DeadConn:
            def cursor(self, *a, **kw):
                raise pymysql.err.OperationalError(
                    2013, "Lost connection to MySQL server during query"
                )

            def commit(self):
                raise pymysql.err.OperationalError(2006, "MySQL server has gone away")

        return _DeadConn()

    def test_get_user_by_username_wraps_disconnect(self):
        with self.assertRaises(AuthBackendUnavailableError):
            self._backend().get_user_by_username(self._dead_conn(), "someone@example.com")

    def test_insert_user_wraps_disconnect(self):
        with self.assertRaises(AuthBackendUnavailableError):
            self._backend().insert_user(
                self._dead_conn(),
                username="someone@example.com",
                password_hash="x",
                display_name_enc=None,
            )

    def test_set_password_hash_wraps_disconnect(self):
        with self.assertRaises(AuthBackendUnavailableError):
            self._backend().set_password_hash(self._dead_conn(), 1, "x")

    def test_set_login_security_wraps_disconnect(self):
        with self.assertRaises(AuthBackendUnavailableError):
            self._backend().set_login_security(
                self._dead_conn(), 1, failed_login_count=1, locked_until=None
            )

    def test_delete_user_wraps_disconnect(self):
        with self.assertRaises(AuthBackendUnavailableError):
            self._backend().delete_user(self._dead_conn(), 1)

    def test_update_profile_fields_wraps_disconnect(self):
        with self.assertRaises(AuthBackendUnavailableError):
            self._backend().update_profile_fields(
                self._dead_conn(), 1, region="서울특별시"
            )

    def test_insert_user_still_raises_duplicate_username(self):
        """중복(errno 1062)은 여전히 DuplicateUsername 으로 통과해야 한다
        (데코레이터가 삼키면 안 됨)."""
        import pymysql

        class _DupConn:
            def cursor(self, *a, **kw):
                raise pymysql.err.IntegrityError(
                    1062, "Duplicate entry 'x' for key 'uq_users_username'"
                )

        with self.assertRaises(repo.DuplicateUsername):
            self._backend().insert_user(
                _DupConn(),
                username="dup@example.com",
                password_hash="x",
                display_name_enc=None,
            )


@unittest.skipUnless(_LIVE_URL, "AUTH_TEST_DB_URL 미설정 — 라이브 DB 테스트 skip")
@unittest.skipUnless(_HAS_PYMYSQL, "pymysql 미설치")
class LiveRemoteDbTests(unittest.TestCase):
    """실제 원격 MySQL/MariaDB 에 붙어 회원가입~탈퇴 전체 흐름을 검증한다.

    각 테스트는 고유한 이메일을 만들고 끝나면 그 행을 지운다(공유 DB 를
    더럽히지 않기 위해). 테이블 자체는 첫 실행 시 init_schema 가 만든다.
    """

    def setUp(self):
        self._saved = {
            k: os.environ.get(k) for k in ("AUTH_DB_URL", "AUTH_ENC_KEY")
        }
        os.environ["AUTH_DB_URL"] = _LIVE_URL
        os.environ["AUTH_ENC_KEY"] = generate_key()
        self._email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"

    def tearDown(self):
        try:
            delete_account(self._email, _GOOD_PW)
        except Exception:
            pass
        try:
            delete_account(self._email, _GOOD_PW2)
        except Exception:
            pass
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_signup_authenticate_roundtrip_decrypts_pii(self):
        sign_up(self._email, _GOOD_PW, "홍길동", region="서울특별시",
                interests=["장애인", "청년"], marketing_opt_in=True)
        user = authenticate(self._email, _GOOD_PW)
        self.assertEqual(user.username, self._email)
        self.assertEqual(user.display_name, "홍길동")
        self.assertEqual(user.region, "서울특별시")
        self.assertEqual(set(user.interests), {"장애인", "청년"})
        self.assertTrue(user.marketing_opt_in)

    def test_duplicate_signup_rejected(self):
        sign_up(self._email, _GOOD_PW, "홍길동")
        with self.assertRaises(UsernameTakenError):
            sign_up(self._email, _GOOD_PW, "다른이름")

    def test_wrong_password_rejected(self):
        sign_up(self._email, _GOOD_PW, "홍길동")
        with self.assertRaises(Exception):
            authenticate(self._email, "Wrong999$")

    def test_update_profile_and_get_profile(self):
        sign_up(self._email, _GOOD_PW, "원래이름", region="부산광역시")
        update_profile(self._email, display_name="바뀐이름",
                       region="인천광역시", interests=["노인/어르신"])
        prof = get_profile(self._email)
        self.assertEqual(prof.display_name, "바뀐이름")
        self.assertEqual(prof.region, "인천광역시")
        self.assertEqual(set(prof.interests), {"노인/어르신"})

    def test_change_password(self):
        sign_up(self._email, _GOOD_PW, "홍길동")
        change_password(self._email, _GOOD_PW, _GOOD_PW2)
        self.assertEqual(authenticate(self._email, _GOOD_PW2).username, self._email)
        with self.assertRaises(Exception):
            authenticate(self._email, _GOOD_PW)

    def test_delete_account(self):
        sign_up(self._email, _GOOD_PW, "홍길동")
        delete_account(self._email, _GOOD_PW)
        with self.assertRaises(Exception):
            authenticate(self._email, _GOOD_PW)


if __name__ == "__main__":
    unittest.main()
