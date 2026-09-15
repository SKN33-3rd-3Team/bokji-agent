"""로그인 / 회원가입 / 비밀번호 변경 — 서비스 계층.

구성
----
- ``passwords``   : 비밀번호 정책 (8자 이상, 영문·숫자·특수문자)
- ``crypto``      : bcrypt 해싱 + Fernet(AES) PII 암호화, 키 로딩
- ``repository``  : ``users`` 테이블 스키마와 CRUD (SQLite 기본, ``AUTH_DB_URL``
                    지정 시 원격 MySQL/MariaDB — ``_mysql`` 백엔드)
- ``service``     : sign_up / authenticate / change_password
- ``lockout``     : 로그인 실패 횟수 제한(계정 임시 잠금) 설정
- ``pii_logging`` : PII 로깅 금지 규칙 (``docs/PII_LOGGING.md``)

키 · DB 위치
------------
- ``AUTH_ENC_KEY`` (환경변수): Fernet 키. **DB와 분리 보관한다.** 새 키는
  ``python src/rag_chatbot/auth/__main__.py keygen`` 으로 만든다. 없으면
  ``.runtime/auth_dev.key`` 를 개발용으로 자동 생성한다(운영 금지).
  원격 DB 를 팀이 공유할 땐 이 키도 **같은 값으로 공유**해야 서로의 PII
  (표시이름·관심조건)를 복호화할 수 있다.
- ``AUTH_DB_URL`` (환경변수): 있으면 원격 MySQL/MariaDB 로 붙는다
  (``mysql://user:pass@host:port/dbname``). RunPod Pod 배포는
  ``docs/AUTH_REMOTE_DB.md`` 참고.
- ``AUTH_DB_PATH`` (환경변수): ``AUTH_DB_URL`` 이 없을 때 쓰는 SQLite 경로.
  이것도 없으면 ``.runtime/auth.db``.
"""

from __future__ import annotations

from .service import (
    AccountLockedError,
    AuthBackendUnavailableError,
    AuthError,
    AuthUser,
    InvalidCredentialsError,
    PasswordPolicyError,
    UsernameTakenError,
    UserNotFoundError,
    authenticate,
    change_password,
    delete_account,
    get_profile,
    sign_up,
    update_profile,
)

__all__ = [
    "AccountLockedError",
    "AuthBackendUnavailableError",
    "AuthError",
    "AuthUser",
    "InvalidCredentialsError",
    "PasswordPolicyError",
    "UsernameTakenError",
    "UserNotFoundError",
    "authenticate",
    "change_password",
    "delete_account",
    "get_profile",
    "sign_up",
    "update_profile",
]
