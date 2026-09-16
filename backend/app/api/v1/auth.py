"""API-01(회원가입) / API-02(로그인) / API-03(로그아웃)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ...core.security import (
    clear_session_cookie,
    generate_session_token,
    read_session_token,
    set_session_cookie,
)
from ...schemas.auth import LoginRequest, LoginResponse, MessageResponse, SignupRequest, SignupResponse
from ...services import auth_adapter
from ...session_store.auth_session import auth_session_store

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response) -> SignupResponse:
    user = auth_adapter.signup(payload)
    token = generate_session_token()
    auth_session_store.create(token, user_id=user.id, username=user.email)
    set_session_cookie(response, token)
    return SignupResponse(user=user)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response) -> LoginResponse:
    user = auth_adapter.login(payload.email, payload.password)
    token = generate_session_token()
    auth_session_store.create(token, user_id=user.id, username=user.email)
    set_session_cookie(response, token)
    return LoginResponse(user=user)


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response) -> MessageResponse:
    # 멱등 설계(API_정의서.xlsx API-03 백엔드 참고사항): 세션이 없거나 이미
    # 만료됐어도 401이 아니라 200으로 응답한다 - 로그아웃 재호출이 에러가
    # 되지 않게 한다. 그래서 이 라우트는 get_current_user를 강제하지 않는다.
    token = read_session_token(request)
    if token:
        auth_session_store.delete(token)
    clear_session_cookie(response)
    return MessageResponse(message="로그아웃되었습니다.")
