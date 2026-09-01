"""암호 기법 두 가지 — 설치된 라이브러리에 따라 강한 구현/표준 라이브러리 대체.

- **비밀번호 해싱 (되돌릴 수 없음)**
  - ``bcrypt`` 설치 시: bcrypt (보안 기획안 채택안). 저장 문자열은 ``$2b$...``.
  - 미설치 시: ``hashlib.pbkdf2_hmac`` (표준 라이브러리, 별도 설치 불필요).
    저장 문자열은 ``pbkdf2_sha256$<rounds>$<salt>$<hash>``.
  두 방식 모두 저장 전 SHA-256→base64 프리해시로 길이를 44바이트로 고정한다
  (bcrypt 72바이트 절단·널바이트 함정 회피).

- **PII 암호화 (키로 되돌릴 수 있음)** — 회원 표시 이름·관심조건에 사용.
  로그인 시 명시적으로 복호화한다.
  - ``cryptography`` 설치 시: Fernet(AES-128-CBC + HMAC). 토큰은 ``gAAAA...``.
  - 미설치 시: 표준 라이브러리만으로 만든 인증 암호화(SHA-256 키스트림 XOR +
    HMAC-SHA256). 토큰은 ``pii1$<nonce>$<ct>$<tag>``.

두 경우 모두 ``streamlit run app.py`` 가 추가 설치 없이 동작하도록 하는 것이
목적이다. 운영에서는 ``requirements-auth.txt`` 로 강한 구현을 설치하는 것을
권장한다.

키 관리
-------
Fernet/대체 방식 모두 같은 키 소스를 쓴다. **DB와 분리해서** 보관한다.

1. ``AUTH_ENC_KEY`` 환경변수가 있으면 그 값을 쓴다(운영 방식).
2. 없으면 ``.runtime/auth_dev.key`` 를 한 번 만들어 재사용한다(개발 전용,
   ``.gitignore`` 에 포함). 최초 생성 시 경고 로그를 남긴다.

새 키 생성: ``python src/rag_chatbot/auth/__main__.py keygen``
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from .pii_logging import get_auth_logger

try:  # 강한 구현 (선택 설치)
    import bcrypt as _bcrypt
except ImportError:  # pragma: no cover - 설치 환경에 따라 갈림
    _bcrypt = None

try:  # 강한 구현 (선택 설치)
    from cryptography.fernet import Fernet as _Fernet
except ImportError:  # pragma: no cover
    _Fernet = None


class PiiTokenInvalid(Exception):
    """PII 토큰이 변조됐거나 키가 맞지 않음."""


_log = get_auth_logger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_DEV_KEY_PATH = _ROOT / ".runtime" / "auth_dev.key"
_ENV_KEY = "AUTH_ENC_KEY"
_PBKDF2_ROUNDS = 600_000

_dev_key_warned = False


# ---------------------------------------------------------------------------
# 비밀번호
# ---------------------------------------------------------------------------
def _prehash(password: str) -> bytes:
    """SHA-256 -> base64 (44바이트). bcrypt 72바이트 절단 함정 회피."""

    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    """저장용 해시 문자열. bcrypt 가 있으면 bcrypt, 없으면 PBKDF2."""

    pre = _prehash(password)
    if _bcrypt is not None:
        return _bcrypt.hashpw(pre, _bcrypt.gensalt()).decode("ascii")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pre, salt, _PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ROUNDS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, hashed: object) -> bool:
    """저장 문자열의 방식을 판별해 검증한다. 검증 불가하면 ``False``."""

    if not isinstance(hashed, str) or not hashed:
        return False
    try:
        if hashed.startswith("pbkdf2_sha256$"):
            _, rounds, salt_b64, dk_b64 = hashed.split("$")
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(dk_b64)
            dk = hashlib.pbkdf2_hmac(
                "sha256", _prehash(password), salt, int(rounds)
            )
            return hmac.compare_digest(dk, expected)
        # bcrypt 형식 ($2a$/$2b$/$2y$)
        if _bcrypt is None:
            # bcrypt 로 만든 해시를 bcrypt 없이 검증할 수는 없다.
            return False
        return _bcrypt.checkpw(_prehash(password), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# 사용자가 존재하지 않을 때도 동일한 시간을 쓰기 위한 더미 해시(모듈 로드 시 1회).
# 로그인 실패 응답 시간으로 계정 존재 여부를 알아내는 타이밍 공격을 막는다.
_DUMMY_HASH = hash_password("timing-side-channel-equalizer")


def verify_password_dummy() -> None:
    """사용자 미존재 시 호출해 해시 검증 시간만 소비한다(타이밍 평준화)."""

    verify_password("timing-side-channel-equalizer", _DUMMY_HASH)


# ---------------------------------------------------------------------------
# 키
# ---------------------------------------------------------------------------
def generate_key() -> str:
    """새 암호화 키(문자열). Fernet 유무와 무관하게 유효한 형식(32바이트 urlsafe b64)."""

    if _Fernet is not None:
        return _Fernet.generate_key().decode("ascii")
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def load_encryption_key() -> bytes:
    """환경변수 -> 개발용 키파일 순으로 키를 얻는다."""

    global _dev_key_warned

    env_value = os.environ.get(_ENV_KEY, "").strip()
    if env_value:
        key = env_value.encode("ascii")
        if _Fernet is not None:
            try:
                _Fernet(key)  # 형식 검증 (Fernet 사용 시에만 의미 있음)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"{_ENV_KEY} 값이 올바른 키가 아닙니다. "
                    f"`python src/rag_chatbot/auth/__main__.py keygen` 으로 "
                    f"다시 생성하세요."
                ) from exc
        return key

    if _DEV_KEY_PATH.exists():
        return _DEV_KEY_PATH.read_bytes().strip()

    _DEV_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = generate_key().encode("ascii")
    _DEV_KEY_PATH.write_bytes(key)
    try:
        _DEV_KEY_PATH.chmod(0o600)
    except OSError:
        pass
    if not _dev_key_warned:
        _log.warning(
            "%s 미설정 — 개발용 키를 %s 에 생성했습니다. 운영에서는 환경변수로 "
            "관리하세요.",
            _ENV_KEY,
            _DEV_KEY_PATH,
        )
        _dev_key_warned = True
    return key


def _raw_key() -> bytes:
    """대체 방식용 32바이트 대칭키 — 어떤 키 소스든 SHA-256 으로 정규화."""

    return hashlib.sha256(load_encryption_key()).digest()


# ---------------------------------------------------------------------------
# PII 암호화
# ---------------------------------------------------------------------------
def _keystream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """SHA-256(key‖nonce‖counter) 블록을 이어붙인 키스트림과 XOR."""

    out = bytearray()
    counter = 0
    while len(out) < len(data):
        out.extend(
            hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        )
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out))


def encrypt_pii(plaintext: str) -> str:
    """저장 시 호출. 원문 -> 토큰 문자열."""

    data = plaintext.encode("utf-8")
    if _Fernet is not None:
        return _Fernet(load_encryption_key()).encrypt(data).decode("ascii")

    key = _raw_key()
    nonce = secrets.token_bytes(16)
    ct = _keystream_xor(key, nonce, data)
    tag = hmac.new(key, b"pii1" + nonce + ct, hashlib.sha256).digest()
    return "pii1${}${}${}".format(
        base64.b64encode(nonce).decode("ascii"),
        base64.b64encode(ct).decode("ascii"),
        base64.b64encode(tag).decode("ascii"),
    )


def decrypt_pii(token: str) -> str:
    """필요할 때만 호출. 토큰 -> 원문. 변조/키 불일치면 :class:`PiiTokenInvalid`."""

    if isinstance(token, str) and token.startswith("pii1$"):
        try:
            _, nonce_b64, ct_b64, tag_b64 = token.split("$")
            key = _raw_key()
            nonce = base64.b64decode(nonce_b64)
            ct = base64.b64decode(ct_b64)
            tag = base64.b64decode(tag_b64)
        except (ValueError, TypeError) as exc:
            raise PiiTokenInvalid("깨진 PII 토큰") from exc
        expected = hmac.new(key, b"pii1" + nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise PiiTokenInvalid("PII 토큰 인증 실패")
        return _keystream_xor(key, nonce, ct).decode("utf-8")

    # Fernet 토큰
    if _Fernet is None:
        raise PiiTokenInvalid("cryptography 미설치 — Fernet 토큰을 읽을 수 없습니다")
    try:
        return _Fernet(load_encryption_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - InvalidToken/base64 오류 등 통일
        raise PiiTokenInvalid("Fernet 토큰 인증 실패") from exc
