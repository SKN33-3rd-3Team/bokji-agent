"""공통 FastAPI 의존성."""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import Request, status

from ..core.errors import ApiError
from ..core.security import read_session_token
from ..services import auth_adapter
from ..session_store.auth_session import AuthSessionRecord, auth_session_store
from ..session_store.chat_session import chat_session_store


def get_current_user(request: Request) -> AuthSessionRecord:
    """쿠키의 회원 ID가 여전히 유효한지 확인한다. 실제 작업도 이 ID에 바인딩한다."""

    token = read_session_token(request)
    record = auth_session_store.get(token) if token else None
    if record is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.")
    try:
        auth_adapter.get_profile(record.username, user_id=record.user_id)
    except ApiError as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            auth_session_store.delete(token)
        raise
    return record


@contextmanager
def user_operation(current: AuthSessionRecord):
    """인증 이후 기다리던 상담도 탈퇴 후 실행되지 않도록 잠금 안에서 재확인한다."""

    with chat_session_store.locked_user(current.user_id):
        auth_adapter.get_profile(current.username, user_id=current.user_id)
        yield
