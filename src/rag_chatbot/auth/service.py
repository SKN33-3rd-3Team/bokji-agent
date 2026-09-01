"""로그인 / 회원가입 / 프로필 조회·수정 / 비밀번호 변경 오케스트레이션.

흐름
----
- ``sign_up``        : 정책 검사 -> 비밀번호 해싱 -> 표시이름·관심조건 암호화 ->
                       INSERT
- ``authenticate``   : 조회 -> **계정 잠금 확인** -> 비밀번호 검증 (실패 시
                       실패 횟수 누적, 임계값 도달 시 일정 시간 잠금) ->
                       표시이름·관심조건 명시적 복호화 -> :class:`AuthUser`
                       반환 (요구사항 4). 잠금 정책은 ``lockout`` 참고.
- ``get_profile``    : 비밀번호 없이 프로필을 다시 읽어 복호화 (마이페이지 표시용)
- ``update_profile`` : 표시이름·지역·관심조건 수정 후 최신 :class:`AuthUser` 반환
- ``change_password``: 현재 비밀번호 검증 -> 새 비밀번호 정책 검사 -> UPDATE
                       (비밀번호 "찾기"는 만들지 않는다 — 변경만)
- ``delete_account`` : 비밀번호 확인 -> 회원 행 즉시 삭제 (탈퇴)

로그인 실패는 "아이디 없음"과 "비밀번호 틀림"을 구분하지 않고 항상
:class:`InvalidCredentialsError` 로 통일한다(계정 존재 여부 노출 방지).

로깅: 아이디는 ``mask_email`` 로 마스킹해서만 남기고 비밀번호·이름·관심조건·
암호문은 절대 남기지 않는다 (``docs/PII_LOGGING.md``).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import lockout
from . import repository as repo
from .crypto import (
    decrypt_pii,
    encrypt_pii,
    hash_password,
    verify_password,
    verify_password_dummy,
)
from .passwords import validate_password
from .pii_logging import get_auth_logger, mask_email

_log = get_auth_logger(__name__)

# 표시 이름: 제어문자 제거 + 공백 정규화 + 길이 제한. 화면(Markdown)에
# 그대로 들어가므로 통제되지 않은 문자열이 저장되지 않게 여기서 정리한다.
DISPLAY_NAME_MAX = 40
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")

# 아이디(이메일): RFC 5321 경로 최대 길이 254 로 제한하고, 제어문자·공백·다중
# ``@`` 를 거부한다(로그 주입·화면 Markdown 오염·DB 오염 방지). 배달 가능성까지
# 검증하지는 않고 형태만 본다.
USERNAME_MAX = 254
_EMAIL_SHAPE_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_display_name(value: object) -> str:
    text = _WS_RE.sub(" ", str(value or ""))   # 탭·개행 등은 공백으로
    text = _CONTROL_RE.sub("", text)           # 남은 제어문자는 제거
    text = _WS_RE.sub(" ", text).strip()
    return text[:DISPLAY_NAME_MAX]


class AuthError(Exception):
    """인증 관련 오류의 최상위 타입."""


class UsernameTakenError(AuthError):
    """이미 존재하는 아이디."""


class PasswordPolicyError(AuthError):
    """비밀번호가 정책을 만족하지 않음. ``violations`` 에 사유 리스트."""

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        super().__init__(
            " ".join(self.violations) or "비밀번호 정책을 만족하지 않습니다."
        )


class InvalidCredentialsError(AuthError):
    """아이디 또는 비밀번호 불일치 (로그인 실패 통일 메시지)."""

    def __init__(self, message: str = "이메일 또는 비밀번호가 올바르지 않습니다."):
        super().__init__(message)


class UserNotFoundError(AuthError):
    """대상 사용자가 없음 (로그인 이후 흐름에서만 사용)."""


class AccountLockedError(AuthError):
    """연속 로그인 실패로 계정이 일시적으로 잠김. ``retry_after_seconds`` 참고."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        minutes = (self.retry_after_seconds + 59) // 60
        super().__init__(
            f"로그인 시도가 너무 많습니다. 약 {minutes}분 후 다시 시도해 주세요."
        )


@dataclass(frozen=True)
class AuthUser:
    """로그인/조회 결과. 암호화된 값들은 메모리에서만 존재하는 복호화 결과."""

    id: int
    username: str
    display_name: str
    created_at: str
    region: str = ""
    interests: tuple[str, ...] = field(default_factory=tuple)
    marketing_opt_in: bool = False


def _normalize_username(username: object) -> str:
    """아이디(이메일)를 정규화·검증한다.

    앞뒤 공백 제거 후: 빈 값·제어문자·길이 초과·이메일 형태 위반을 거부한다.
    ``sign_up`` 과 ``authenticate`` 가 모두 이 함수를 지나므로, 형태에 맞지
    않는 값은 애초에 계정 ID 로 저장되지 않는다.
    """

    if not isinstance(username, str):
        raise AuthError("아이디(이메일)를 입력해 주세요.")
    uname = username.strip()
    if not uname:
        raise AuthError("아이디(이메일)를 입력해 주세요.")
    if _CONTROL_RE.search(uname):
        raise AuthError("아이디에 사용할 수 없는 문자가 포함되어 있습니다.")
    if len(uname) > USERNAME_MAX:
        raise AuthError(f"아이디(이메일)는 {USERNAME_MAX}자 이하여야 합니다.")
    if not _EMAIL_SHAPE_RE.match(uname):
        raise AuthError("올바른 이메일 형식이 아닙니다.")
    return uname


def _open(db_path):
    conn = repo.connect(db_path)
    try:
        repo.init_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    """저장된 ISO8601 타임스탬프 문자열을 tz-aware datetime 으로. 실패 시 None."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 암호화 헬퍼
# ---------------------------------------------------------------------------
def _encrypt_interests(interests) -> str | None:
    items = [str(x).strip() for x in (interests or []) if str(x).strip()]
    if not items:
        return None
    return encrypt_pii(json.dumps(items, ensure_ascii=False))


def _decrypt_interests(token) -> tuple[str, ...]:
    if not token:
        return ()
    try:
        data = json.loads(decrypt_pii(token))
    except Exception:  # noqa: BLE001 - 키 불일치/변조/깨진 JSON
        return ()
    return tuple(str(x) for x in data) if isinstance(data, list) else ()


def _safe_decrypt_name(token, uname: str) -> str:
    if not token:
        return ""
    try:
        return decrypt_pii(token)
    except Exception:  # noqa: BLE001 - 키 불일치/변조 시 이름만 비운다
        _log.warning("display_name 복호화 실패 username=%s", mask_email(uname))
        return ""


def _row_to_user(row, *, display_name: str) -> AuthUser:
    return AuthUser(
        id=int(row["id"]),
        username=row["username"],
        display_name=display_name,
        created_at=row["created_at"],
        region=row["region"] or "",
        interests=_decrypt_interests(row["interests_enc"]),
        marketing_opt_in=bool(row["marketing_opt_in"]),
    )


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def sign_up(
    username: str,
    password: str,
    display_name: str = "",
    *,
    region: str = "",
    interests=None,
    marketing_opt_in: bool = False,
    db_path=None,
) -> AuthUser:
    uname = _normalize_username(username)

    violations = validate_password(password)
    if violations:
        raise PasswordPolicyError(violations)

    name = _clean_display_name(display_name)
    region = (region or "").strip()
    interest_items = tuple(
        str(x).strip() for x in (interests or []) if str(x).strip()
    )

    conn = _open(db_path)
    try:
        try:
            user_id, created_at = repo.insert_user(
                conn,
                username=uname,
                password_hash=hash_password(password),
                display_name_enc=encrypt_pii(name) if name else None,
                region=region or None,
                interests_enc=_encrypt_interests(interest_items),
                marketing_opt_in=marketing_opt_in,
            )
        except sqlite3.IntegrityError as exc:
            raise UsernameTakenError("이미 가입된 아이디(이메일)입니다.") from exc
    finally:
        conn.close()

    _log.info("signup ok username=%s", mask_email(uname))
    return AuthUser(
        id=user_id,
        username=uname,
        display_name=name,
        created_at=created_at,
        region=region,
        interests=interest_items,
        marketing_opt_in=bool(marketing_opt_in),
    )


def authenticate(username: str, password: str, *, db_path=None) -> AuthUser:
    uname = _normalize_username(username)

    conn = _open(db_path)
    try:
        row = repo.get_user_by_username(conn, uname)

        if row is None:
            # 계정 존재 여부를 응답 시간으로 알아내지 못하게 같은 검증 시간을 쓴다.
            verify_password_dummy()
            _log.info("login fail username=%s", mask_email(uname))
            raise InvalidCredentialsError()

        user_id = int(row["id"])
        now = _utcnow()
        locked_until = _parse_ts(row["locked_until"])

        # 잠금 상태면 비밀번호가 맞아도 차단한다.
        if locked_until is not None and locked_until > now:
            remaining = int((locked_until - now).total_seconds())
            _log.info("login blocked (locked) username=%s", mask_email(uname))
            raise AccountLockedError(remaining)

        # 잠금 창이 지났으면(자동 해제) 카운터를 새로 센다.
        prev_fails = 0 if locked_until is not None else int(row["failed_login_count"] or 0)

        if not verify_password(password, row["password_hash"]):
            fails = prev_fails + 1
            limit = lockout.max_attempts()
            if fails >= limit:
                secs = lockout.lockout_seconds()
                new_lock = (now + timedelta(seconds=secs)).isoformat(timespec="seconds")
                repo.set_login_security(
                    conn, user_id, failed_login_count=fails, locked_until=new_lock
                )
                _log.info(
                    "login fail username=%s (locked, fails=%d)",
                    mask_email(uname), fails,
                )
                raise AccountLockedError(secs)
            repo.set_login_security(
                conn, user_id, failed_login_count=fails, locked_until=None
            )
            _log.info(
                "login fail username=%s (fails=%d/%d)",
                mask_email(uname), fails, limit,
            )
            raise InvalidCredentialsError()

        # 성공: 이전 실패 흔적이 있으면 초기화한다.
        if row["failed_login_count"] or row["locked_until"]:
            repo.set_login_security(
                conn, user_id, failed_login_count=0, locked_until=None
            )

        # 저장 시 암호화한 값들을 로그인 시점에 명시적으로 복호화한다 (요구사항 4).
        display_name = _safe_decrypt_name(row["display_name_enc"], uname)
        _log.info("login ok username=%s", mask_email(uname))
        return _row_to_user(row, display_name=display_name)
    finally:
        conn.close()


def get_profile(username: str, *, db_path=None) -> AuthUser:
    """비밀번호 검증 없이 프로필을 읽어 복호화한다 (호출 전 세션으로 인증 확인)."""

    uname = _normalize_username(username)
    conn = _open(db_path)
    try:
        row = repo.get_user_by_username(conn, uname)
    finally:
        conn.close()
    if row is None:
        raise UserNotFoundError("존재하지 않는 사용자입니다.")
    return _row_to_user(
        row, display_name=_safe_decrypt_name(row["display_name_enc"], uname)
    )


def update_profile(
    username: str,
    *,
    display_name: str | None = None,
    region: str | None = None,
    interests=None,
    db_path=None,
) -> AuthUser:
    """전달한 필드만 수정하고 최신 :class:`AuthUser` 를 돌려준다.

    ``None`` 인 인자는 "수정하지 않음"이다(빈 문자열/빈 리스트는 "지움").
    """

    uname = _normalize_username(username)
    conn = _open(db_path)
    try:
        row = repo.get_user_by_username(conn, uname)
        if row is None:
            raise UserNotFoundError("존재하지 않는 사용자입니다.")

        changes: dict[str, object] = {}
        if display_name is not None:
            trimmed = _clean_display_name(display_name)
            changes["display_name_enc"] = encrypt_pii(trimmed) if trimmed else None
        if region is not None:
            trimmed = region.strip()
            changes["region"] = trimmed or None
        if interests is not None:
            changes["interests_enc"] = _encrypt_interests(interests)

        repo.update_profile_fields(conn, int(row["id"]), **changes)
        fresh = repo.get_user_by_username(conn, uname)
    finally:
        conn.close()

    _log.info("profile update ok username=%s", mask_email(uname))
    return _row_to_user(
        fresh, display_name=_safe_decrypt_name(fresh["display_name_enc"], uname)
    )


def change_password(
    username: str,
    current_password: str,
    new_password: str,
    *,
    db_path=None,
) -> None:
    uname = _normalize_username(username)

    conn = _open(db_path)
    try:
        row = repo.get_user_by_username(conn, uname)
        if row is None:
            raise UserNotFoundError("존재하지 않는 사용자입니다.")
        if not verify_password(current_password, row["password_hash"]):
            _log.info("password change fail (bad current) username=%s", mask_email(uname))
            raise InvalidCredentialsError("현재 비밀번호가 올바르지 않습니다.")

        violations = validate_password(new_password)
        if violations:
            raise PasswordPolicyError(violations)
        if verify_password(new_password, row["password_hash"]):
            raise PasswordPolicyError(["새 비밀번호는 현재 비밀번호와 달라야 합니다."])

        repo.set_password_hash(conn, int(row["id"]), hash_password(new_password))
    finally:
        conn.close()

    _log.info("password change ok username=%s", mask_email(uname))


def delete_account(username: str, password: str, *, db_path=None) -> None:
    """비밀번호를 확인한 뒤 회원 행과 그 내용을 삭제한다(되돌릴 수 없음).

    ``repo.delete_user`` 가 ``secure_delete`` 로 삭제 페이지를 0으로 덮고 WAL 을
    체크포인트한다. 다만 파일 크기 축소나 디스크 물리 소거까지는 보장하지
    않는다(포렌식 수준 완전 삭제 아님).
    """

    uname = _normalize_username(username)

    conn = _open(db_path)
    try:
        row = repo.get_user_by_username(conn, uname)
        if row is None:
            raise UserNotFoundError("존재하지 않는 사용자입니다.")
        if not verify_password(password, row["password_hash"]):
            _log.info("account delete fail (bad password) username=%s", mask_email(uname))
            raise InvalidCredentialsError("비밀번호가 올바르지 않습니다.")
        repo.delete_user(conn, int(row["id"]))
    finally:
        conn.close()

    _log.info("account delete ok username=%s", mask_email(uname))
