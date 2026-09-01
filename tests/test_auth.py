"""로그인 / 회원가입 / 비밀번호 변경 + 암호 기법 + PII 로깅 규칙 테스트."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from rag_chatbot.auth import (
    AuthUser,
    InvalidCredentialsError,
    PasswordPolicyError,
    UserNotFoundError,
    UsernameTakenError,
    authenticate,
    change_password,
    delete_account,
    get_profile,
    sign_up,
    update_profile,
)
from rag_chatbot.auth import crypto as _crypto
from rag_chatbot.auth.crypto import (
    PiiTokenInvalid,
    decrypt_pii,
    encrypt_pii,
    generate_key,
    hash_password,
    load_encryption_key,
    verify_password,
    verify_password_dummy,
)
from rag_chatbot.auth.passwords import validate_password
from rag_chatbot.auth.pii_logging import PiiRedactingFilter, mask_email, redact
from rag_chatbot.auth.service import _clean_display_name

_GOOD_PW = "Abcd1234!"
_GOOD_PW2 = "Zyxw9876$"


class PasswordPolicyTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_password(_GOOD_PW), [])

    def test_too_short(self):
        self.assertTrue(validate_password("Ab1!"))

    def test_missing_digit(self):
        self.assertTrue(any("숫자" in v for v in validate_password("Abcdefg!")))

    def test_missing_letter(self):
        self.assertTrue(any("영문" in v for v in validate_password("1234567!")))

    def test_missing_special(self):
        self.assertTrue(any("특수문자" in v for v in validate_password("Abcd12345")))

    def test_non_string(self):
        self.assertTrue(validate_password(None))

    def test_non_ascii_digit_does_not_satisfy_digit_rule(self):
        # 전각 숫자(３)는 "숫자" 규칙을 만족시키지 않는다
        self.assertTrue(any("숫자" in v for v in validate_password("Abcdefg３!")))


class CryptoTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTH_ENC_KEY"] = generate_key()

    def tearDown(self):
        os.environ.pop("AUTH_ENC_KEY", None)

    def test_hash_roundtrip(self):
        hashed = hash_password(_GOOD_PW)
        self.assertNotIn(_GOOD_PW, hashed)
        self.assertTrue(verify_password(_GOOD_PW, hashed))
        self.assertFalse(verify_password("wrong-pw", hashed))

    def test_hash_is_salted(self):
        self.assertNotEqual(hash_password(_GOOD_PW), hash_password(_GOOD_PW))

    def test_long_password_not_truncated(self):
        # bcrypt 72바이트 절단이면 아래 두 비밀번호가 같은 것으로 취급된다.
        base = "A1!" + "x" * 100
        hashed = hash_password(base)
        self.assertTrue(verify_password(base, hashed))
        self.assertFalse(verify_password(base + "-different-tail", hashed))

    def test_pii_roundtrip(self):
        token = encrypt_pii("홍길동")
        self.assertNotIn("홍길동", token)
        self.assertEqual(decrypt_pii(token), "홍길동")

    def test_pii_tamper_rejected(self):
        token = encrypt_pii("홍길동")
        with self.assertRaises(PiiTokenInvalid):
            decrypt_pii(token[:-4] + "AAAA")

    def test_verify_password_rejects_non_string_hash(self):
        self.assertFalse(verify_password("x", None))
        self.assertFalse(verify_password("x", 12345))
        self.assertFalse(verify_password("x", ""))

    def test_verify_password_dummy_runs(self):
        verify_password_dummy()  # 예외 없이 실행되면 통과 (타이밍 평준화용)

    def test_bad_env_key_raises_clear_error(self):
        os.environ["AUTH_ENC_KEY"] = "not-a-valid-fernet-key"
        try:
            with self.assertRaises(RuntimeError):
                load_encryption_key()
        finally:
            os.environ["AUTH_ENC_KEY"] = generate_key()


class StdlibFallbackCryptoTests(unittest.TestCase):
    """bcrypt / cryptography 미설치 환경을 흉내내어 표준 라이브러리 대체 경로를 검증."""

    def setUp(self):
        os.environ["AUTH_ENC_KEY"] = generate_key()
        self._bcrypt, self._fernet = _crypto._bcrypt, _crypto._Fernet
        _crypto._bcrypt = None
        _crypto._Fernet = None

    def tearDown(self):
        _crypto._bcrypt, _crypto._Fernet = self._bcrypt, self._fernet
        os.environ.pop("AUTH_ENC_KEY", None)

    def test_pbkdf2_password_roundtrip(self):
        hashed = _crypto.hash_password(_GOOD_PW)
        self.assertTrue(hashed.startswith("pbkdf2_sha256$"))
        self.assertNotIn(_GOOD_PW, hashed)
        self.assertTrue(_crypto.verify_password(_GOOD_PW, hashed))
        self.assertFalse(_crypto.verify_password("wrong-pw", hashed))

    def test_pbkdf2_is_salted(self):
        self.assertNotEqual(
            _crypto.hash_password(_GOOD_PW), _crypto.hash_password(_GOOD_PW)
        )

    def test_stdlib_pii_roundtrip(self):
        token = _crypto.encrypt_pii("홍길동")
        self.assertTrue(token.startswith("pii1$"))
        self.assertNotIn("홍길동", token)
        self.assertEqual(_crypto.decrypt_pii(token), "홍길동")

    def test_stdlib_pii_tamper_rejected(self):
        token = _crypto.encrypt_pii("홍길동")
        with self.assertRaises(PiiTokenInvalid):
            _crypto.decrypt_pii(token[:-6] + "AAAAAA")

    def test_dummy_verify_still_runs(self):
        _crypto.verify_password_dummy()

    def test_bcryptless_cannot_read_bcrypt_hash(self):
        # bcrypt 로 만든 해시를 bcrypt 없이 검증하면 조용히 실패(예외 없음).
        self.assertFalse(_crypto.verify_password(_GOOD_PW, "$2b$12$" + "x" * 53))


class DisplayNameCleaningTests(unittest.TestCase):
    def test_strips_control_chars_and_collapses_whitespace(self):
        self.assertEqual(_clean_display_name("  홍\x00길\t동  "), "홍길 동")

    def test_caps_length(self):
        self.assertEqual(len(_clean_display_name("가" * 100)), 40)

    def test_empty_and_none(self):
        self.assertEqual(_clean_display_name(None), "")
        self.assertEqual(_clean_display_name("   "), "")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTH_ENC_KEY"] = generate_key()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "auth.db"

    def tearDown(self):
        os.environ.pop("AUTH_ENC_KEY", None)
        self._tmp.cleanup()

    def _signup(self, username="user@example.com", password=_GOOD_PW, name="홍길동"):
        return sign_up(username, password, name, db_path=self.db)

    def test_signup_then_authenticate_decrypts_name(self):
        created = self._signup()
        self.assertIsInstance(created, AuthUser)
        got = authenticate("user@example.com", _GOOD_PW, db_path=self.db)
        self.assertEqual(got.username, "user@example.com")
        self.assertEqual(got.display_name, "홍길동")

    def test_username_is_case_insensitive(self):
        self._signup(username="User@Example.com")
        got = authenticate("user@example.com", _GOOD_PW, db_path=self.db)
        self.assertEqual(got.display_name, "홍길동")

    def test_duplicate_username(self):
        self._signup()
        with self.assertRaises(UsernameTakenError):
            self._signup()

    def test_weak_password_rejected(self):
        with self.assertRaises(PasswordPolicyError):
            sign_up("x@example.com", "weak", "n", db_path=self.db)

    def test_wrong_password(self):
        self._signup()
        with self.assertRaises(InvalidCredentialsError):
            authenticate("user@example.com", "Wrong123!", db_path=self.db)

    def test_unknown_user_same_error(self):
        with self.assertRaises(InvalidCredentialsError):
            authenticate("nobody@example.com", _GOOD_PW, db_path=self.db)

    def test_change_password(self):
        self._signup()
        change_password("user@example.com", _GOOD_PW, _GOOD_PW2, db_path=self.db)
        with self.assertRaises(InvalidCredentialsError):
            authenticate("user@example.com", _GOOD_PW, db_path=self.db)
        self.assertEqual(
            authenticate("user@example.com", _GOOD_PW2, db_path=self.db).display_name,
            "홍길동",
        )

    def test_change_password_wrong_current(self):
        self._signup()
        with self.assertRaises(InvalidCredentialsError):
            change_password("user@example.com", "Nope1234!", _GOOD_PW2, db_path=self.db)

    def test_change_password_same_as_current(self):
        self._signup()
        with self.assertRaises(PasswordPolicyError):
            change_password("user@example.com", _GOOD_PW, _GOOD_PW, db_path=self.db)

    def test_change_password_new_must_pass_policy(self):
        self._signup()
        with self.assertRaises(PasswordPolicyError):
            change_password("user@example.com", _GOOD_PW, "weak", db_path=self.db)

    # -- 프로필(회원가입 입력 내용) 저장·조회·수정 -----------------------
    def test_signup_persists_profile_and_authenticate_returns_it(self):
        sign_up("p@example.com", _GOOD_PW, "복지왕", region="서울특별시",
                interests=["장애인", "청년"], marketing_opt_in=True, db_path=self.db)
        got = authenticate("p@example.com", _GOOD_PW, db_path=self.db)
        self.assertEqual(got.region, "서울특별시")
        self.assertEqual(set(got.interests), {"장애인", "청년"})
        self.assertTrue(got.marketing_opt_in)

    def test_get_profile_decrypts_without_password(self):
        sign_up("g@example.com", _GOOD_PW, "김복지", region="부산광역시",
                interests=["노인/어르신"], db_path=self.db)
        prof = get_profile("g@example.com", db_path=self.db)
        self.assertEqual(prof.display_name, "김복지")
        self.assertEqual(prof.region, "부산광역시")
        self.assertEqual(list(prof.interests), ["노인/어르신"])
        self.assertFalse(prof.marketing_opt_in)

    def test_get_profile_unknown_user(self):
        with self.assertRaises(UserNotFoundError):
            get_profile("ghost@example.com", db_path=self.db)

    def test_update_profile_changes_fields(self):
        sign_up("u@example.com", _GOOD_PW, "old", region="대구광역시",
                interests=["청년"], db_path=self.db)
        updated = update_profile("u@example.com", display_name="new",
                                 region="인천광역시", interests=["장애인", "한부모/조손가정"],
                                 db_path=self.db)
        self.assertEqual(updated.display_name, "new")
        self.assertEqual(updated.region, "인천광역시")
        self.assertEqual(set(updated.interests), {"장애인", "한부모/조손가정"})
        # 재조회해도 유지된다
        again = get_profile("u@example.com", db_path=self.db)
        self.assertEqual(again.display_name, "new")
        self.assertEqual(again.region, "인천광역시")

    def test_update_profile_none_args_leave_fields_untouched(self):
        sign_up("k@example.com", _GOOD_PW, "keep", region="세종특별자치시",
                interests=["청년"], db_path=self.db)
        update_profile("k@example.com", display_name="renamed", db_path=self.db)
        prof = get_profile("k@example.com", db_path=self.db)
        self.assertEqual(prof.display_name, "renamed")
        self.assertEqual(prof.region, "세종특별자치시")   # 그대로
        self.assertEqual(list(prof.interests), ["청년"])   # 그대로

    def test_update_profile_can_clear_fields(self):
        sign_up("c@example.com", _GOOD_PW, "name", region="광주광역시",
                interests=["청년"], db_path=self.db)
        update_profile("c@example.com", region="", interests=[], db_path=self.db)
        prof = get_profile("c@example.com", db_path=self.db)
        self.assertEqual(prof.region, "")
        self.assertEqual(prof.interests, ())

    def test_signup_without_profile_fields_still_works(self):
        sign_up("bare@example.com", _GOOD_PW, "", db_path=self.db)
        prof = get_profile("bare@example.com", db_path=self.db)
        self.assertEqual(prof.display_name, "")
        self.assertEqual(prof.region, "")
        self.assertEqual(prof.interests, ())

    # -- 회원 탈퇴 -----------------------------------------------------
    def test_delete_account_removes_row(self):
        self._signup()
        delete_account("user@example.com", _GOOD_PW, db_path=self.db)
        with self.assertRaises(InvalidCredentialsError):
            authenticate("user@example.com", _GOOD_PW, db_path=self.db)
        with self.assertRaises(UserNotFoundError):
            get_profile("user@example.com", db_path=self.db)

    def test_delete_account_wrong_password_keeps_row(self):
        self._signup()
        with self.assertRaises(InvalidCredentialsError):
            delete_account("user@example.com", "Nope1234!", db_path=self.db)
        # 계정은 그대로 살아 있다
        self.assertEqual(
            authenticate("user@example.com", _GOOD_PW, db_path=self.db).username,
            "user@example.com",
        )

    def test_delete_account_unknown_user(self):
        with self.assertRaises(UserNotFoundError):
            delete_account("ghost@example.com", _GOOD_PW, db_path=self.db)

    def test_username_reusable_after_delete(self):
        self._signup(name="첫번째")
        delete_account("user@example.com", _GOOD_PW, db_path=self.db)
        self._signup(name="두번째")  # 같은 아이디로 재가입 가능
        self.assertEqual(
            authenticate("user@example.com", _GOOD_PW, db_path=self.db).display_name,
            "두번째",
        )


class PiiLoggingTests(unittest.TestCase):
    def test_mask_email(self):
        self.assertEqual(mask_email("hong@example.com"), "h***@example.com")
        self.assertEqual(mask_email("not-an-email"), "***")
        self.assertEqual(mask_email(""), "")

    def test_redact_email_and_number(self):
        self.assertNotIn("hong@example.com", redact("user hong@example.com in"))
        self.assertNotIn("010-1234-5678", redact("phone 010-1234-5678 saved"))

    def test_filter_scrubs_record_args(self):
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1, "login %s", ("a@b.com",), None
        )
        PiiRedactingFilter().filter(record)
        self.assertNotIn("a@b.com", record.getMessage())


if __name__ == "__main__":
    unittest.main()
