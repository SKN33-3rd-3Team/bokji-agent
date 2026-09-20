"""API-10(상담 시작)/11(되묻기)/12(정책 문의)/13(세션 초기화)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError

from ...schemas.auth import MessageResponse
from ...schemas.chat import (
    ChatRequest,
    ChatResponse,
    FollowupRequest,
    PolicyQuestionRequest,
    PolicyQuestionResponse,
)
from ...services import chat_adapter, followup_adapter
from ...session_store.auth_session import AuthSessionRecord
from ..deps import get_current_user, user_operation

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


async def _no_recommendation_input(request: Request) -> None:
    if request.query_params or await request.body():
        raise ApiError(400, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다.")


@router.post("/recommendations", response_model=ChatResponse)
def recommend(
    response: Response,
    current: AuthSessionRecord = Depends(get_current_user),
    _input: None = Depends(_no_recommendation_input),
) -> ChatResponse:
    response.headers["Cache-Control"] = "no-store"
    with user_operation(current) as profile:
        return chat_adapter.start_recommendations(profile, user_id=current.user_id)


@router.post("/messages", response_model=ChatResponse)
def send_message(
    payload: ChatRequest, current: AuthSessionRecord = Depends(get_current_user)
) -> ChatResponse:
    with user_operation(current):
        return chat_adapter.start_chat(payload, user_id=current.user_id)


@router.post("/sessions/{session_id}/followup", response_model=ChatResponse)
def send_followup(
    session_id: str,
    payload: FollowupRequest,
    current: AuthSessionRecord = Depends(get_current_user),
) -> ChatResponse:
    with user_operation(current):
        answer = payload.calc_answers.model_dump() if payload.calc_answers is not None else payload.message
        return chat_adapter.continue_chat(session_id, answer, user_id=current.user_id)


@router.post(
    "/sessions/{session_id}/policies/{policy_id}/questions",
    response_model=PolicyQuestionResponse,
)
def ask_policy_question(
    session_id: str,
    policy_id: str,
    payload: PolicyQuestionRequest,
    current: AuthSessionRecord = Depends(get_current_user),
) -> PolicyQuestionResponse:
    with user_operation(current):
        return followup_adapter.ask_policy_question(
            session_id, policy_id, payload.question, user_id=current.user_id
        )


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
def delete_session(
    session_id: str, current: AuthSessionRecord = Depends(get_current_user)
) -> MessageResponse:
    with user_operation(current):
        chat_adapter.delete_chat_session(session_id, user_id=current.user_id)
    return MessageResponse(message="상담 세션이 초기화되었습니다.")
