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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

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

# 성별: 하드게이트 슬롯 계약(graph.slot_schema.Gender)과 같은 값("male"/
# "female")을 그대로 저장한다 - 이 모듈이 graph 패키지를 의존하지는 않되,
# 값 자체는 한쪽만 고쳐 어긋나지 않도록 문자열을 그대로 맞춰 둔다.
_GENDER_VALUES = frozenset({"male", "female"})

# 장애 등록 여부/보훈대상자 여부/소득구간/가구유형: graph.slot_schema의
# DisabilityStatus/VeteranStatus/IncomeBracket/HouseholdType과 같은 값을
# 그대로 쓴다(위 성별과 같은 이유로 graph 패키지 자체는 import하지 않는다).
_DISABILITY_VALUES = frozenset({"registered", "not_registered"})
_VETERAN_VALUES = frozenset({"registered", "not_registered"})
_INCOME_BRACKET_VALUES = frozenset(
    {
        "under_30",
        "pct_30_50",
        "pct_50_75",
        "pct_75_100",
        "pct_100_150",
        "over_150",
    }
)
_HOUSEHOLD_TYPE_VALUES = frozenset(
    {
        "single_person",
        "single_parent",
        "grandparent",
        "multicultural",
        "multi_child",
        "north_korean_defector",
        "care_leaver",
        "facility_leaver",
        "newlywed",
    }
)

# 시/도: streamlit_ui.constants.SIDO_OPTIONS 와 같은 값을 그대로 쓴다(위
# 성별 등과 같은 이유로 streamlit_ui 패키지 자체는 import하지 않는다 - 이
# 모듈은 API가 직접 호출될 수도 있으므로 폼 제한과 별개로 서버에서도
# 검증한다/fail-closed). 값이 바뀌면 두 곳을 같이 고쳐야 한다.
_SIDO_VALUES = frozenset(
    {
        "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
        "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
        "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
        "경상남도", "제주특별자치도",
    }
)

# UI 의존 없이 API-01/05의 관심조건을 검증한다(streamlit_ui.constants의
# SIGNUP_INTEREST_OPTIONS/INTEREST_FIELD_OPTIONS와 같은 값을 그대로 쓴다 -
# 위 _SIDO_VALUES와 같은 이유로 streamlit_ui 패키지 자체는 import하지 않는다.
# 값이 바뀌면 두 곳을 같이 고쳐야 한다).
#
# 회원가입(API-01)은 4종(signup_interest_options)만 허용한다 - 계약 밖 값은
# 여전히 거부한다(fail-closed, test_signup_and_profile_interests_follow_
# signup_options).
_SIGNUP_INTEREST_VALUES = frozenset({"임신/출산", "노인/어르신", "농어업인", "청년"})

# 마이페이지 수정(API-05)은 19종(interest_field_options)까지 넓게 허용한다.
# 가입 때 고른 4종 값도 이후 마이페이지에서 재검증(다른 필드 수정 시 함께
# 전송됨) 시 거부되지 않도록 두 목록의 합집합이다.
_PROFILE_INTEREST_VALUES = _SIGNUP_INTEREST_VALUES | frozenset(
    {
        "육아", "출산", "보육", "주거", "취업", "일자리", "창업", "교육", "장학",
        "의료", "건강", "돌봄", "노인", "장애인", "저소득", "다문화", "한부모",
        "지원금",
    }
)

# graph.slot_schema와 같은 한국 날짜·만 120세 상한. 인증 모듈은 무거운
# graph 패키지를 import하지 않으며 경계 일치는 계약 테스트로 확인한다.
_MAX_PLAUSIBLE_AGE_YEARS = 120


def _clean_gender(value: object) -> str:
    """빈 값은 "선택 안 함"으로 허용한다. 계약에 없는 값은 거부한다(fail-closed)."""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text not in _GENDER_VALUES:
        raise AuthError("성별 값이 올바르지 않습니다.")
    return text


def _clean_birth_date(value: object) -> str:
    """ISO ``YYYY-MM-DD`` 형식·개연성만 확인한다. 빈 값은 "선택 안 함"."""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise AuthError("생년월일 형식이 올바르지 않습니다.") from exc
    if parsed.isoformat() != text:
        raise AuthError("생년월일 형식이 올바르지 않습니다.")
    today = _korea_today()
    if parsed > today:
        raise AuthError("생년월일이 미래일 수 없습니다.")
    age = today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
    if age > _MAX_PLAUSIBLE_AGE_YEARS:
        raise AuthError("생년월일이 올바르지 않습니다.")
    return parsed.isoformat()


def _korea_today() -> date:
    return datetime.now(timezone(timedelta(hours=9))).date()


def _clean_choice(value: object, allowed: frozenset[str], label: str) -> str:
    """빈 값은 "선택 안 함"으로 허용한다. 계약에 없는 값은 거부한다(fail-closed).

    ``_clean_gender``와 같은 모양을 4개 필드(장애·보훈·소득구간)가 함께
    쓰도록 일반화했다.
    """

    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text not in allowed:
        raise AuthError(f"{label} 값이 올바르지 않습니다.")
    return text


def _clean_disability_status(value: object) -> str:
    return _clean_choice(value, _DISABILITY_VALUES, "장애 등록 여부")


def _clean_veteran_status(value: object) -> str:
    return _clean_choice(value, _VETERAN_VALUES, "보훈대상자 여부")


def _clean_income_bracket(value: object) -> str:
    return _clean_choice(value, _INCOME_BRACKET_VALUES, "소득 수준")


def _clean_household_types(values: object) -> tuple[str, ...]:
    """가구유형 목록을 검증한다. 계약 밖 값이 하나라도 있으면 거부한다
    (폼 위조 방지 - fail-closed). 중복은 제거하고 입력 순서는 유지한다."""

    if values is None:
        return ()
    items = [str(x).strip().lower() for x in values if str(x).strip()]
    for item in items:
        if item not in _HOUSEHOLD_TYPE_VALUES:
            raise AuthError("가구 유형 값이 올바르지 않습니다.")
    return tuple(dict.fromkeys(items))


def _clean_region(value: object) -> str:
    """빈 값은 "선택 안 함"으로 허용한다. API-09 시/도 목록에 없는 값은
    거부한다(폼 위조 방지 - fail-closed)."""

    text = str(value or "").strip()
    if not text:
        return ""
    if text not in _SIDO_VALUES:
        raise AuthError("거주 지역 값이 올바르지 않습니다.")
    return text


def _clean_interests(values: object, *, allowed: frozenset[str]) -> tuple[str, ...]:
    """관심 지원조건 목록을 검증한다. 계약 밖 값이 하나라도 있으면 거부한다
    (폼 위조 방지 - fail-closed). 중복은 제거하고 입력 순서는 유지한다.

    ``allowed``는 호출부(가입/마이페이지 수정)마다 다르다 - 위
    _SIGNUP_INTEREST_VALUES/_PROFILE_INTEREST_VALUES 주석 참고."""

    if values is None:
        return ()
    items = [str(x).strip() for x in values if str(x).strip()]
    for item in items:
        if item not in allowed:
            raise AuthError("관심 지원조건 값이 올바르지 않습니다.")
    return tuple(dict.fromkeys(items))


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


class AuthBackendUnavailableError(AuthError):
    """회원 DB(원격 MySQL/MariaDB 등)에 연결할 수 없음.

    ``AUTH_DB_URL`` 로 원격 DB 를 쓰는데 DB 서버가 꺼져 있거나 주소/계정이
    틀렸을 때. 화면단은 ``except AuthError`` 로 잡아 안내만 하면 된다.
    """


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
    gender: str = ""
    birth_date: str = ""
    interests: tuple[str, ...] = field(default_factory=tuple)
    disability_status: str = ""
    veteran_status: str = ""
    income_bracket: str = ""
    household_types: tuple[str, ...] = field(default_factory=tuple)
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
    """``(backend, conn)`` 를 돌려준다.

    ``AUTH_DB_URL`` 이 있으면 원격 MySQL/MariaDB, 없으면(또는 ``db_path`` 를
    명시하면) SQLite. 백엔드 선택 규칙은 ``repository.get_backend`` 참고.

    ``AUTH_DB_URL`` 형식 오류(``ValueError``)는 화면단이 다루기 쉽도록
    :class:`AuthBackendUnavailableError` 로 감싼다.
    """

    try:
        backend = repo.get_backend(db_path)
    except ValueError as exc:
        raise AuthBackendUnavailableError(
            f"회원 DB 설정(AUTH_DB_URL)이 올바르지 않습니다: {exc}"
        ) from exc
    conn = backend.connect()
    try:
        backend.init_schema(conn)
    except Exception:
        conn.close()
        raise
    return backend, conn


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
def _encrypt_safe(plaintext: str) -> str:
    """``encrypt_pii`` 를 감싸 키 설정 오류를 AuthError 계열로 통일한다.

    ``AUTH_ENC_KEY`` 가 잘못된 형식이면 ``crypto.load_encryption_key`` 가 raw
    ``RuntimeError`` 를 던지는데, 이는 ``AuthError`` 가 아니라서 화면단의
    ``except AuthError`` 를 그대로 통과해 사용자에게 노출된다(회원가입·프로필
    수정마다 재현됨). 원인은 로그에만 남기고 사용자에게는 일반 안내만 준다.
    """
    try:
        return encrypt_pii(plaintext)
    except RuntimeError as exc:
        _log.error("PII 암호화 실패(키 설정 오류): %s", exc)
        raise AuthBackendUnavailableError(
            "서비스 설정 오류로 요청을 처리할 수 없습니다. 관리자에게 문의해 주세요."
        ) from exc


def _encrypt_string_list(items) -> str | None:
    """문자열 리스트를 JSON으로 묶어 암호화한다(``interests``/``household_types``
    공용). ``_encrypt_safe``를 거치므로 키 설정 오류가 원본 ``RuntimeError``로
    새지 않고 ``AuthBackendUnavailableError``로 통일된다."""

    cleaned = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not cleaned:
        return None
    return _encrypt_safe(json.dumps(cleaned, ensure_ascii=False))


def _decrypt_string_list(token) -> tuple[str, ...]:
    if not token:
        return ()
    try:
        data = json.loads(decrypt_pii(token))
    except Exception:  # noqa: BLE001 - 키 불일치/변조/깨진 JSON
        return ()
    return tuple(str(x) for x in data) if isinstance(data, list) else ()


def _encrypt_interests(interests) -> str | None:
    return _encrypt_string_list(interests)


def _decrypt_interests(token) -> tuple[str, ...]:
    return _decrypt_string_list(token)


def _safe_decrypt_field(token, uname: str, field_label: str) -> str:
    """단일 문자열 필드를 복호화한다. 실패하면(키 불일치/변조) 그 필드만
    조용히 비운다 - ``display_name``/``birth_date``/장애·보훈·소득 공용."""

    if not token:
        return ""
    try:
        return decrypt_pii(token)
    except Exception:  # noqa: BLE001 - 키 불일치/변조 시 이 필드만 비운다
        _log.warning("%s 복호화 실패 username=%s", field_label, mask_email(uname))
        return ""


def _safe_decrypt_name(token, uname: str) -> str:
    return _safe_decrypt_field(token, uname, "display_name")


def _safe_decrypt_birth_date(token, uname: str) -> str:
    return _safe_decrypt_field(token, uname, "birth_date")


def _row_to_user(
    row,
    *,
    display_name: str,
    birth_date: str,
    disability_status: str = "",
    veteran_status: str = "",
    income_bracket: str = "",
    household_types: tuple[str, ...] = (),
) -> AuthUser:
    return AuthUser(
        id=int(row["id"]),
        username=row["username"],
        display_name=display_name,
        created_at=row["created_at"],
        region=row["region"] or "",
        gender=row["gender"] or "",
        birth_date=birth_date,
        interests=_decrypt_interests(row["interests_enc"]),
        disability_status=disability_status,
        veteran_status=veteran_status,
        income_bracket=income_bracket,
        household_types=household_types,
        marketing_opt_in=bool(row["marketing_opt_in"]),
    )


def _decrypt_profile_extras(row, uname: str) -> dict[str, object]:
    """장애·보훈·소득·가구유형을 한 번에 복호화한다(``_row_to_user`` 호출
    3곳 - authenticate/get_profile/update_profile - 이 공유)."""

    return {
        "disability_status": _safe_decrypt_field(
            row["disability_status_enc"], uname, "disability_status"
        ),
        "veteran_status": _safe_decrypt_field(
            row["veteran_status_enc"], uname, "veteran_status"
        ),
        "income_bracket": _safe_decrypt_field(
            row["income_bracket_enc"], uname, "income_bracket"
        ),
        "household_types": _decrypt_string_list(row["household_types_enc"]),
    }


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def sign_up(
    username: str,
    password: str,
    display_name: str = "",
    *,
    region: str = "",
    gender: str = "",
    birth_date: str = "",
    interests=None,
    disability_status: str = "",
    veteran_status: str = "",
    income_bracket: str = "",
    household_types=None,
    marketing_opt_in: bool = False,
    db_path=None,
) -> AuthUser:
    uname = _normalize_username(username)

    violations = validate_password(password)
    if violations:
        raise PasswordPolicyError(violations)

    name = _clean_display_name(display_name)
    region = _clean_region(region)
    gender = _clean_gender(gender)
    birth_date = _clean_birth_date(birth_date)
    interest_items = _clean_interests(interests, allowed=_SIGNUP_INTEREST_VALUES)
    disability_status = _clean_disability_status(disability_status)
    veteran_status = _clean_veteran_status(veteran_status)
    income_bracket = _clean_income_bracket(income_bracket)
    household_type_items = _clean_household_types(household_types)

    backend, conn = _open(db_path)
    try:
        try:
            user_id, created_at = backend.insert_user(
                conn,
                username=uname,
                password_hash=hash_password(password),
                display_name_enc=_encrypt_safe(name) if name else None,
                region=region or None,
                gender=gender or None,
                birth_date_enc=encrypt_pii(birth_date) if birth_date else None,
                interests_enc=_encrypt_interests(interest_items),
                disability_status_enc=(
                    encrypt_pii(disability_status) if disability_status else None
                ),
                veteran_status_enc=(
                    encrypt_pii(veteran_status) if veteran_status else None
                ),
                income_bracket_enc=(
                    encrypt_pii(income_bracket) if income_bracket else None
                ),
                household_types_enc=_encrypt_string_list(household_type_items),
                marketing_opt_in=marketing_opt_in,
            )
        except repo.DuplicateUsername as exc:
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
        gender=gender,
        birth_date=birth_date,
        interests=interest_items,
        disability_status=disability_status,
        veteran_status=veteran_status,
        income_bracket=income_bracket,
        household_types=household_type_items,
        marketing_opt_in=bool(marketing_opt_in),
    )


def authenticate(username: str, password: str, *, db_path=None) -> AuthUser:
    uname = _normalize_username(username)

    backend, conn = _open(db_path)
    try:
        row = backend.get_user_by_username(conn, uname)

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

        if not verify_password(password, row["password_hash"]):
            # 실패 횟수 증가와 잠금 판정을 DB에서 원자적으로 처리한다.
            # 이전에는 여기서 값을 읽어 +1 해 다시 썼는데, 동시에
            # 들어온 여러 잘못된 로그인 요청이 같은 이전 값을 읽어 같은 값을
            # 저장할 수 있어(lost update) 실제보다 적게 집계되는 문제가 있었다.
            limit = lockout.max_attempts()
            secs = lockout.lockout_seconds()
            fails, locked_after = backend.record_failed_login(
                conn, user_id, max_attempts=limit, lock_seconds=secs
            )
            if locked_after is not None:
                _log.info(
                    "login fail username=%s (locked, fails=%d)",
                    mask_email(uname), fails,
                )
                raise AccountLockedError(secs)
            _log.info(
                "login fail username=%s (fails=%d/%d)",
                mask_email(uname), fails, limit,
            )
            raise InvalidCredentialsError()

        # 성공: 이전 실패 흔적이 있으면 초기화한다.
        if row["failed_login_count"] or row["locked_until"]:
            backend.set_login_security(
                conn, user_id, failed_login_count=0, locked_until=None
            )

        # 저장 시 암호화한 값들을 로그인 시점에 명시적으로 복호화한다 (요구사항 4).
        display_name = _safe_decrypt_name(row["display_name_enc"], uname)
        birth_date = _safe_decrypt_birth_date(row["birth_date_enc"], uname)
        _log.info("login ok username=%s", mask_email(uname))
        return _row_to_user(
            row,
            display_name=display_name,
            birth_date=birth_date,
            **_decrypt_profile_extras(row, uname),
        )
    finally:
        conn.close()


def _check_user_identity(row, expected_user_id: int | None) -> None:
    """이메일을 재사용해도 기존 세션이 다른 회원 행을 읽거나 변경하지 못하게 한다."""

    if row is None or (expected_user_id is not None and int(row["id"]) != expected_user_id):
        raise UserNotFoundError("존재하지 않는 사용자입니다.")


def get_profile(username: str, *, expected_user_id: int | None = None, db_path=None) -> AuthUser:
    """비밀번호 검증 없이 프로필을 읽어 복호화한다 (호출 전 세션으로 인증 확인)."""

    uname = _normalize_username(username)
    backend, conn = _open(db_path)
    try:
        row = backend.get_user_by_username(conn, uname)
    finally:
        conn.close()
    _check_user_identity(row, expected_user_id)
    return _row_to_user(
        row,
        display_name=_safe_decrypt_name(row["display_name_enc"], uname),
        birth_date=_safe_decrypt_birth_date(row["birth_date_enc"], uname),
        **_decrypt_profile_extras(row, uname),
    )


def update_profile(
    username: str,
    *,
    display_name: str | None = None,
    region: str | None = None,
    gender: str | None = None,
    birth_date: str | None = None,
    interests=None,
    disability_status: str | None = None,
    veteran_status: str | None = None,
    income_bracket: str | None = None,
    household_types=None,
    expected_user_id: int | None = None,
    db_path=None,
) -> AuthUser:
    """전달한 필드만 수정하고 최신 :class:`AuthUser` 를 돌려준다.

    ``None`` 인 인자는 "수정하지 않음"이다(빈 문자열/빈 리스트는 "지움").
    """

    uname = _normalize_username(username)
    backend, conn = _open(db_path)
    try:
        row = backend.get_user_by_username(conn, uname)
        _check_user_identity(row, expected_user_id)

        changes: dict[str, object] = {}
        if display_name is not None:
            trimmed = _clean_display_name(display_name)
            changes["display_name_enc"] = _encrypt_safe(trimmed) if trimmed else None
        if region is not None:
            changes["region"] = _clean_region(region) or None
        if gender is not None:
            changes["gender"] = _clean_gender(gender) or None
        if birth_date is not None:
            cleaned = _clean_birth_date(birth_date)
            changes["birth_date_enc"] = encrypt_pii(cleaned) if cleaned else None
        if interests is not None:
            changes["interests_enc"] = _encrypt_interests(
                _clean_interests(interests, allowed=_PROFILE_INTEREST_VALUES)
            )
        if disability_status is not None:
            cleaned = _clean_disability_status(disability_status)
            changes["disability_status_enc"] = encrypt_pii(cleaned) if cleaned else None
        if veteran_status is not None:
            cleaned = _clean_veteran_status(veteran_status)
            changes["veteran_status_enc"] = encrypt_pii(cleaned) if cleaned else None
        if income_bracket is not None:
            cleaned = _clean_income_bracket(income_bracket)
            changes["income_bracket_enc"] = encrypt_pii(cleaned) if cleaned else None
        if household_types is not None:
            changes["household_types_enc"] = _encrypt_string_list(
                _clean_household_types(household_types)
            )

        backend.update_profile_fields(conn, int(row["id"]), **changes)
        fresh = backend.get_user_by_username(conn, uname)
        _check_user_identity(fresh, int(row["id"]))
    finally:
        conn.close()

    _log.info("profile update ok username=%s", mask_email(uname))
    return _row_to_user(
        fresh,
        display_name=_safe_decrypt_name(fresh["display_name_enc"], uname),
        birth_date=_safe_decrypt_birth_date(fresh["birth_date_enc"], uname),
        **_decrypt_profile_extras(fresh, uname),
    )


def change_password(
    username: str,
    current_password: str,
    new_password: str,
    *,
    expected_user_id: int | None = None,
    db_path=None,
) -> None:
    uname = _normalize_username(username)

    backend, conn = _open(db_path)
    try:
        row = backend.get_user_by_username(conn, uname)
        _check_user_identity(row, expected_user_id)
        if not verify_password(current_password, row["password_hash"]):
            _log.info("password change fail (bad current) username=%s", mask_email(uname))
            raise InvalidCredentialsError("현재 비밀번호가 올바르지 않습니다.")

        violations = validate_password(new_password)
        if violations:
            raise PasswordPolicyError(violations)
        if verify_password(new_password, row["password_hash"]):
            raise PasswordPolicyError(["새 비밀번호는 현재 비밀번호와 달라야 합니다."])

        backend.set_password_hash(conn, int(row["id"]), hash_password(new_password))
    finally:
        conn.close()

    _log.info("password change ok username=%s", mask_email(uname))


def delete_account(
    username: str, password: str, *, expected_user_id: int | None = None, db_path=None
) -> None:
    """비밀번호를 확인한 뒤 회원 행과 그 내용을 삭제한다(되돌릴 수 없음).

    SQLite 백엔드에서는 ``delete_user`` 가 ``secure_delete`` 로 삭제 페이지를
    0으로 덮고 WAL 을 체크포인트한다(파일 크기 축소·디스크 물리 소거까지는
    보장하지 않음). 원격 MySQL/MariaDB 에서는 평범한 ``DELETE`` 이며, 저장소
    수준의 잔재 제거는 그쪽 DB 운영 정책에 달려 있다.
    """

    uname = _normalize_username(username)

    backend, conn = _open(db_path)
    try:
        row = backend.get_user_by_username(conn, uname)
        _check_user_identity(row, expected_user_id)
        if not verify_password(password, row["password_hash"]):
            _log.info("account delete fail (bad password) username=%s", mask_email(uname))
            raise InvalidCredentialsError("비밀번호가 올바르지 않습니다.")
        backend.delete_user(conn, int(row["id"]))
    finally:
        conn.close()

    _log.info("account delete ok username=%s", mask_email(uname))
