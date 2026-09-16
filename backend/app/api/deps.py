"""공통 FastAPI 의존성."""

from __future__ import annotations

from fastapi import Request, status

from ..core.errors import ApiError
from ..core.security import read_session_token
from ..session_store.auth_session import AuthSessionRecord, auth_session_store


def get_current_user(request: Request) -> AuthSessionRecord:
    """세션 쿠키로 현재 로그인 사용자를 찾는다. 없으면 401.

    가벼운 조회(세션 저장소 dict 조회만, DB 왕복 없음)라 채팅류 라우트에서
    "이 요청의 로그인 사용자가 누구인지"만 필요할 때 바로 쓴다. 전체 프로필
    (복호화된 PII 포함)이 필요한 라우트(API-04/05/06/07/08)는 이 dependency로
    얻은 ``username``을 ``services/auth_adapter.py``에 넘겨 별도로 조회한다.
    """

    token = read_session_token(request)
    record = auth_session_store.get(token) if token else None
    if record is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.")
    return record
