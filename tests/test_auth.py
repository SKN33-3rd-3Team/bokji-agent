"""로그인 / 회원가입 / 비밀번호 변경 + 암호 기법 + PII 로깅 규칙 테스트."""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from rag_chatbot.auth import (
    AccountLockedError,
    AuthError,
    AuthBackendUnavailableError,
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
from rag_chatbot.auth import repository as _repo
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


class AuthCliTests(unittest.TestCase):
    def test_keygen_script_runs_without_repo_root_on_import_path(self):
        script = Path(__file__).resolve().parents[1] / "src/rag_chatbot/auth/__main__.py"
        with tempfile.TemporaryDirectory() as workdir:
            result = subprocess.run(
                [sys.executable, "-B", "-E", str(script), "keygen"],
                cwd=workdir, capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(bool(result.stdout.strip()))


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
                interests=["임신/출산", "청년"], marketing_opt_in=True, db_path=self.db)
        got = authenticate("p@example.com", _GOOD_PW, db_path=self.db)
        self.assertEqual(got.region, "서울특별시")
        self.assertEqual(set(got.interests), {"임신/출산", "청년"})
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
                                 region="인천광역시", interests=["임신/출산", "노인/어르신"],
                                 db_path=self.db)
        self.assertEqual(updated.display_name, "new")
        self.assertEqual(updated.region, "인천광역시")
        self.assertEqual(set(updated.interests), {"임신/출산", "노인/어르신"})
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
        self.assertEqual(prof.gender, "")
        self.assertEqual(prof.birth_date, "")
        self.assertEqual(prof.interests, ())

    # -- 성별·생년월일 (region과 같은 방식으로 하드게이트 자동 연동에 쓰임) --
    def test_signup_persists_gender_and_birth_date(self):
        sign_up("gb@example.com", _GOOD_PW, "김성별", gender="female",
                birth_date="1998-05-12", db_path=self.db)
        got = authenticate("gb@example.com", _GOOD_PW, db_path=self.db)
        self.assertEqual(got.gender, "female")
        self.assertEqual(got.birth_date, "1998-05-12")

    def test_get_profile_decrypts_gender_and_birth_date(self):
        sign_up("gb2@example.com", _GOOD_PW, "김성별", gender="male",
                birth_date="1990-01-01", db_path=self.db)
        prof = get_profile("gb2@example.com", db_path=self.db)
        self.assertEqual(prof.gender, "male")
        self.assertEqual(prof.birth_date, "1990-01-01")

    def test_signup_rejects_invalid_gender(self):
        with self.assertRaises(AuthError):
            sign_up("badgender@example.com", _GOOD_PW, "n",
                    gender="alien", db_path=self.db)

    def test_signup_rejects_malformed_birth_date(self):
        with self.assertRaises(AuthError):
            sign_up("badbirth@example.com", _GOOD_PW, "n",
                    birth_date="1998/05/12", db_path=self.db)

    def test_signup_rejects_future_birth_date(self):
        with self.assertRaises(AuthError):
            sign_up("futurebirth@example.com", _GOOD_PW, "n",
                    birth_date="2999-01-01", db_path=self.db)

    def test_signup_rejects_implausible_birth_date(self):
        with self.assertRaises(AuthError):
            sign_up("oldbirth@example.com", _GOOD_PW, "n",
                    birth_date="1800-01-01", db_path=self.db)

    def test_update_profile_can_set_and_clear_gender_and_birth_date(self):
        sign_up("gbupdate@example.com", _GOOD_PW, "n", db_path=self.db)
        updated = update_profile("gbupdate@example.com", gender="female",
                                 birth_date="2000-03-26", db_path=self.db)
        self.assertEqual(updated.gender, "female")
        self.assertEqual(updated.birth_date, "2000-03-26")

        cleared = update_profile("gbupdate@example.com", gender="",
                                 birth_date="", db_path=self.db)
        self.assertEqual(cleared.gender, "")
        self.assertEqual(cleared.birth_date, "")

    def test_update_profile_leaves_gender_and_birth_date_untouched_when_omitted(self):
        sign_up("gbkeep@example.com", _GOOD_PW, "n", gender="male",
                birth_date="1995-07-01", db_path=self.db)
        update_profile("gbkeep@example.com", display_name="renamed", db_path=self.db)
        prof = get_profile("gbkeep@example.com", db_path=self.db)
        self.assertEqual(prof.gender, "male")
        self.assertEqual(prof.birth_date, "1995-07-01")

    def test_birth_date_is_encrypted_at_rest(self):
        """이름·관심조건처럼 원문 생년월일이 DB 파일에 평문으로 남지 않는다."""

        sign_up("encbirth@example.com", _GOOD_PW, "n", birth_date="1998-05-12",
                db_path=self.db)
        self.assertNotIn(b"1998-05-12", Path(self.db).read_bytes())

    # -- 장애·보훈·소득·가구유형 (강사님 주제 컨펌 반영) -------------------
    def test_signup_persists_disability_veteran_income_household(self):
        sign_up("ext@example.com", _GOOD_PW, "확장유저",
                disability_status="registered", veteran_status="registered",
                income_bracket="under_30",
                household_types=["single_parent", "newlywed"], db_path=self.db)
        got = authenticate("ext@example.com", _GOOD_PW, db_path=self.db)
        self.assertEqual(got.disability_status, "registered")
        self.assertEqual(got.veteran_status, "registered")
        self.assertEqual(got.income_bracket, "under_30")
        self.assertEqual(set(got.household_types), {"single_parent", "newlywed"})

    def test_get_profile_decrypts_disability_veteran_income_household(self):
        sign_up("ext2@example.com", _GOOD_PW, "확장유저2",
                disability_status="not_registered", veteran_status="not_registered",
                income_bracket="pct_100_150", household_types=["multi_child"],
                db_path=self.db)
        prof = get_profile("ext2@example.com", db_path=self.db)
        self.assertEqual(prof.disability_status, "not_registered")
        self.assertEqual(prof.veteran_status, "not_registered")
        self.assertEqual(prof.income_bracket, "pct_100_150")
        self.assertEqual(list(prof.household_types), ["multi_child"])

    def test_signup_without_extra_fields_leaves_them_empty(self):
        sign_up("bare2@example.com", _GOOD_PW, "", db_path=self.db)
        prof = get_profile("bare2@example.com", db_path=self.db)
        self.assertEqual(prof.disability_status, "")
        self.assertEqual(prof.veteran_status, "")
        self.assertEqual(prof.income_bracket, "")
        self.assertEqual(prof.household_types, ())

    def test_signup_rejects_invalid_disability_status(self):
        with self.assertRaises(AuthError):
            sign_up("baddis@example.com", _GOOD_PW, "n",
                    disability_status="alien", db_path=self.db)

    def test_signup_rejects_invalid_veteran_status(self):
        with self.assertRaises(AuthError):
            sign_up("badvet@example.com", _GOOD_PW, "n",
                    veteran_status="alien", db_path=self.db)

    def test_signup_rejects_invalid_income_bracket(self):
        with self.assertRaises(AuthError):
            sign_up("badincome@example.com", _GOOD_PW, "n",
                    income_bracket="alien", db_path=self.db)

    def test_signup_rejects_invalid_household_type(self):
        with self.assertRaises(AuthError):
            sign_up("badhousehold@example.com", _GOOD_PW, "n",
                    household_types=["single_parent", "alien"], db_path=self.db)

    def test_update_profile_can_set_and_clear_extra_fields(self):
        sign_up("extupdate@example.com", _GOOD_PW, "n", db_path=self.db)
        updated = update_profile(
            "extupdate@example.com", disability_status="registered",
            veteran_status="registered", income_bracket="under_30",
            household_types=["grandparent"], db_path=self.db,
        )
        self.assertEqual(updated.disability_status, "registered")
        self.assertEqual(updated.veteran_status, "registered")
        self.assertEqual(updated.income_bracket, "under_30")
        self.assertEqual(list(updated.household_types), ["grandparent"])

        cleared = update_profile(
            "extupdate@example.com", disability_status="", veteran_status="",
            income_bracket="", household_types=[], db_path=self.db,
        )
        self.assertEqual(cleared.disability_status, "")
        self.assertEqual(cleared.veteran_status, "")
        self.assertEqual(cleared.income_bracket, "")
        self.assertEqual(cleared.household_types, ())

    def test_update_profile_leaves_extra_fields_untouched_when_omitted(self):
        sign_up("extkeep@example.com", _GOOD_PW, "n", disability_status="registered",
                income_bracket="pct_50_75", household_types=["care_leaver"],
                db_path=self.db)
        update_profile("extkeep@example.com", display_name="renamed", db_path=self.db)
        prof = get_profile("extkeep@example.com", db_path=self.db)
        self.assertEqual(prof.disability_status, "registered")
        self.assertEqual(prof.income_bracket, "pct_50_75")
        self.assertEqual(list(prof.household_types), ["care_leaver"])

    def test_extra_fields_are_encrypted_at_rest(self):
        """장애·소득 코드값 등이 DB 파일에 평문으로 남지 않는다."""

        sign_up("encext@example.com", _GOOD_PW, "n", disability_status="registered",
                income_bracket="under_30", household_types=["north_korean_defector"],
                db_path=self.db)
        raw = Path(self.db).read_bytes()
        self.assertNotIn(b"registered", raw)
        self.assertNotIn(b"under_30", raw)
        self.assertNotIn(b"north_korean_defector", raw)

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

    # -- 아이디(이메일) 검증 -----------------------------------------
    def test_username_rejects_control_chars(self):
        with self.assertRaises(Exception):
            sign_up("a\nb@example.com", _GOOD_PW, "n", db_path=self.db)

    def test_username_rejects_overlong(self):
        huge = "a" * 250 + "@example.com"
        with self.assertRaises(Exception):
            sign_up(huge, _GOOD_PW, "n", db_path=self.db)

    def test_username_rejects_non_email_shape(self):
        for bad in ("notanemail", "a b@example.com", "a@@b.com", "a@bcom"):
            with self.assertRaises(Exception):
                sign_up(bad, _GOOD_PW, "n", db_path=self.db)

    def test_username_trims_surrounding_whitespace(self):
        sign_up("  spaced@example.com \n", _GOOD_PW, "n", db_path=self.db)
        self.assertEqual(
            authenticate("spaced@example.com", _GOOD_PW, db_path=self.db).username,
            "spaced@example.com",
        )

    def test_delete_account_scrubs_row_bytes(self):
        sign_up("scrubme@example.com", _GOOD_PW, "홍길동", db_path=self.db)
        self.assertIn(b"scrubme@example.com", Path(self.db).read_bytes())
        delete_account("scrubme@example.com", _GOOD_PW, db_path=self.db)
        # secure_delete + wal_checkpoint(TRUNCATE) 후 메인 DB 파일에 흔적이 없다.
        self.assertNotIn(b"scrubme@example.com", Path(self.db).read_bytes())

    # -- AUTH_ENC_KEY 오류가 AuthBackendUnavailableError 로 통일되는지 --------
    def test_signup_wraps_bad_enc_key_as_backend_unavailable(self):
        os.environ["AUTH_ENC_KEY"] = "not-a-valid-fernet-key"
        try:
            with self.assertRaises(AuthBackendUnavailableError):
                sign_up("badkey@example.com", _GOOD_PW, "홍길동", db_path=self.db)
        finally:
            os.environ["AUTH_ENC_KEY"] = generate_key()

    def test_signup_with_interests_wraps_bad_enc_key(self):
        # display_name 은 비워 _encrypt_interests 경로(관심조건 암호화)만 탄다.
        os.environ["AUTH_ENC_KEY"] = "not-a-valid-fernet-key"
        try:
            with self.assertRaises(AuthBackendUnavailableError):
                sign_up("badkey2@example.com", _GOOD_PW, "",
                        interests=["청년"], db_path=self.db)
        finally:
            os.environ["AUTH_ENC_KEY"] = generate_key()

    def test_update_profile_wraps_bad_enc_key_as_backend_unavailable(self):
        self._signup(username="badkey3@example.com")
        os.environ["AUTH_ENC_KEY"] = "not-a-valid-fernet-key"
        try:
            with self.assertRaises(AuthBackendUnavailableError):
                update_profile("badkey3@example.com", display_name="새이름",
                               db_path=self.db)
        finally:
            os.environ["AUTH_ENC_KEY"] = generate_key()


class SqliteBackendErrorTests(unittest.TestCase):
    """SQLite 저수준 함수의 raw 예외가 AuthBackendUnavailableError 로 통일되는지.

    ``connect()``/``init_schema()``/CRUD 6개 전부 대상. ``DuplicateUsername``
    은 그대로 통과해야 한다(데코레이터가 삼키면 안 됨).
    """

    def _dead_conn(self):
        class _DeadConn:
            def execute(self, *a, **kw):
                raise sqlite3.OperationalError("database is locked")

            def commit(self):
                raise sqlite3.OperationalError("database is locked")

        return _DeadConn()

    def test_connect_wraps_sqlite_error(self):
        real_connect = sqlite3.connect
        sqlite3.connect = lambda *a, **kw: (_ for _ in ()).throw(
            sqlite3.OperationalError("unable to open database file")
        )
        try:
            with self.assertRaises(AuthBackendUnavailableError):
                _repo.connect(Path(tempfile.gettempdir()) / "whatever-auth-test.db")
        finally:
            sqlite3.connect = real_connect

    def test_init_schema_wraps_sqlite_error(self):
        class _BadSchemaConn:
            def executescript(self, *a, **kw):
                raise sqlite3.OperationalError("disk I/O error")

        with self.assertRaises(AuthBackendUnavailableError):
            _repo.init_schema(_BadSchemaConn())

    def test_insert_user_wraps_sqlite_error(self):
        with self.assertRaises(AuthBackendUnavailableError):
            _repo.insert_user(
                self._dead_conn(), username="x@example.com",
                password_hash="h", display_name_enc=None,
            )

    def test_get_user_by_username_wraps_sqlite_error(self):
        with self.assertRaises(AuthBackendUnavailableError):
            _repo.get_user_by_username(self._dead_conn(), "x@example.com")

    def test_set_password_hash_wraps_sqlite_error(self):
        with self.assertRaises(AuthBackendUnavailableError):
            _repo.set_password_hash(self._dead_conn(), 1, "h")

    def test_set_login_security_wraps_sqlite_error(self):
        with self.assertRaises(AuthBackendUnavailableError):
            _repo.set_login_security(
                self._dead_conn(), 1, failed_login_count=1, locked_until=None
            )

    def test_delete_user_wraps_sqlite_error(self):
        with self.assertRaises(AuthBackendUnavailableError):
            _repo.delete_user(self._dead_conn(), 1)

    def test_update_profile_fields_wraps_sqlite_error(self):
        with self.assertRaises(AuthBackendUnavailableError):
            _repo.update_profile_fields(self._dead_conn(), 1, region="서울특별시")

    def test_insert_user_still_raises_duplicate_username(self):
        class _DupConn:
            def execute(self, *a, **kw):
                raise sqlite3.IntegrityError(
                    "UNIQUE constraint failed: users.username"
                )

        with self.assertRaises(_repo.DuplicateUsername):
            _repo.insert_user(
                _DupConn(), username="dup@example.com", password_hash="h",
                display_name_enc=None,
            )


class LoginLockoutTests(unittest.TestCase):
    """로그인 5회 연속 실패 → 일정 시간 잠금, 성공 시 카운터 초기화."""

    _LOCKOUT_ENV = (
        "AUTH_MAX_LOGIN_ATTEMPTS",
        "AUTH_LOCKOUT_MINUTES",
        "AUTH_LOCKOUT_SECONDS",
    )

    def setUp(self):
        os.environ["AUTH_ENC_KEY"] = generate_key()
        for var in self._LOCKOUT_ENV:
            os.environ.pop(var, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "auth.db"
        sign_up("u@example.com", _GOOD_PW, "홍길동", db_path=self.db)

    def tearDown(self):
        os.environ.pop("AUTH_ENC_KEY", None)
        for var in self._LOCKOUT_ENV:
            os.environ.pop(var, None)
        self._tmp.cleanup()

    def _wrong(self):
        return authenticate("u@example.com", "Wrong123!", db_path=self.db)

    def _ok(self):
        return authenticate("u@example.com", _GOOD_PW, db_path=self.db)

    def test_locks_after_five_failures(self):
        for _ in range(4):
            with self.assertRaises(InvalidCredentialsError):
                self._wrong()
        with self.assertRaises(AccountLockedError):
            self._wrong()

    def test_locked_blocks_even_correct_password(self):
        for _ in range(5):
            with self.assertRaises((InvalidCredentialsError, AccountLockedError)):
                self._wrong()
        with self.assertRaises(AccountLockedError):
            self._ok()

    def test_success_resets_counter(self):
        for _ in range(4):
            with self.assertRaises(InvalidCredentialsError):
                self._wrong()
        self.assertEqual(self._ok().username, "u@example.com")
        # 카운터가 0으로 초기화됐으므로 다시 4회까지는 잠기지 않는다.
        for _ in range(4):
            with self.assertRaises(InvalidCredentialsError):
                self._wrong()

    def test_threshold_is_configurable(self):
        os.environ["AUTH_MAX_LOGIN_ATTEMPTS"] = "2"
        with self.assertRaises(InvalidCredentialsError):
            self._wrong()
        with self.assertRaises(AccountLockedError):
            self._wrong()

    def test_lock_auto_expires(self):
        os.environ["AUTH_LOCKOUT_SECONDS"] = "1"
        for _ in range(4):
            with self.assertRaises(InvalidCredentialsError):
                self._wrong()
        with self.assertRaises(AccountLockedError) as ctx:
            self._wrong()
        self.assertGreaterEqual(ctx.exception.retry_after_seconds, 1)
        time.sleep(1.3)
        self.assertEqual(self._ok().username, "u@example.com")

    def test_expired_lock_starts_fresh_count(self):
        # 잠금이 자동 해제된 뒤 한 번 더 틀려도 즉시 재잠금되지 않는다.
        conn = _repo.connect(self.db)
        try:
            _repo.init_schema(conn)
            row = _repo.get_user_by_username(conn, "u@example.com")
            _repo.set_login_security(
                conn, int(row["id"]),
                failed_login_count=5,
                locked_until="2000-01-01T00:00:00+00:00",
            )
        finally:
            conn.close()
        with self.assertRaises(InvalidCredentialsError):
            self._wrong()


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
