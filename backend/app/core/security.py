"""로그인 세션 쿠키 발급/검증.

``src/rag_chatbot/auth``에는 세션/쿠키 개념이 전혀 없다(비밀번호 검증까지만
담당) - REST API는 무상태이므로 이 계층에서 처음 만든다. 세션 토큰 자체는
``secrets.token_urlsafe``로 생성한 불투명(opaque) 랜덤 문자열이고, 실제
사용자 매핑은 ``session_store/auth_session.py``가 프로세스 메모리에 들고
있는다(서명/JWT가 아님 - 토큰 자체에는 아무 정보도 담기지 않는다).
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response

from .config import settings

SESSION_COOKIE_NAME = "session_id"


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.auth_session_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def read_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)
