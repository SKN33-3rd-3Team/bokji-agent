"""API-04(조회)/05(수정)/06(비밀번호)/07(탈퇴)/08(채팅 프리필)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ...core.security import clear_session_cookie
from ...schemas.auth import (
    ChangePasswordRequest,
    ChatDefaultsResponse,
    DeleteAccountRequest,
    MessageResponse,
    UpdateProfileRequest,
    UserProfile,
)
from ...services import auth_adapter
from ...session_store.auth_session import AuthSessionRecord, auth_session_store
from ..deps import get_current_user

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/me", response_model=UserProfile)
def get_me(current: AuthSessionRecord = Depends(get_current_user)) -> UserProfile:
    return auth_adapter.get_profile(current.username)


@router.patch("/me", response_model=UserProfile)
def update_me(
    payload: UpdateProfileRequest, current: AuthSessionRecord = Depends(get_current_user)
) -> UserProfile:
    return auth_adapter.update_profile(current.username, payload)


@router.post("/me/password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest, current: AuthSessionRecord = Depends(get_current_user)
) -> MessageResponse:
    auth_adapter.change_password(current.username, payload.current_password, payload.new_password)
    return MessageResponse(message="비밀번호가 변경되었습니다.")


@router.delete("/me", response_model=MessageResponse)
def delete_me(
    payload: DeleteAccountRequest,
    response: Response,
    current: AuthSessionRecord = Depends(get_current_user),
) -> MessageResponse:
    auth_adapter.delete_account(current.username, payload.password)
    auth_session_store.delete_all_for_user(current.user_id)
    clear_session_cookie(response)
    return MessageResponse(message="회원 탈퇴가 완료되었습니다.")


@router.get("/me/chat-defaults", response_model=ChatDefaultsResponse)
def get_chat_defaults(
    current: AuthSessionRecord = Depends(get_current_user),
) -> ChatDefaultsResponse:
    return auth_adapter.get_chat_defaults(current.username)
