"""로그인 실패 횟수 제한 — 계정 임시 잠금 설정.

정책: 연속 로그인 실패가 임계값에 도달하면 계정을 일정 시간 잠근다. 잠금
중에는 비밀번호가 맞아도 로그인할 수 없고, 잠금 시간이 지나면 자동 해제된다.
로그인에 성공하면 실패 카운터가 0으로 초기화된다.

기본값
------
- 연속 실패 5회 → 15분 잠금.

환경변수(선택)
--------------
- ``AUTH_MAX_LOGIN_ATTEMPTS`` : 잠금까지 허용하는 연속 실패 횟수 (기본 5).
- ``AUTH_LOCKOUT_MINUTES``    : 잠금 지속 시간(분) (기본 15).
- ``AUTH_LOCKOUT_SECONDS``    : 지정하면 분(minutes)보다 우선한다(테스트·미세조정).

값은 호출 시점마다 환경변수를 읽으므로, 배포 중 재시작 없이 조정할 수 있고
테스트에서도 손쉽게 바꿀 수 있다.
"""

from __future__ import annotations

import os

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LOCKOUT_MINUTES = 15


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def max_attempts() -> int:
    """잠금이 걸리기까지 허용하는 연속 실패 횟수."""

    return _positive_int_env("AUTH_MAX_LOGIN_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)


def lockout_seconds() -> int:
    """계정 잠금 지속 시간(초). ``AUTH_LOCKOUT_SECONDS`` 가 분 설정보다 우선."""

    seconds = _positive_int_env("AUTH_LOCKOUT_SECONDS", 0)
    if seconds:
        return seconds
    return _positive_int_env("AUTH_LOCKOUT_MINUTES", DEFAULT_LOCKOUT_MINUTES) * 60
