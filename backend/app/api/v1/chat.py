"""API-10(상담 시작)/11(되묻기)/12(정책 문의)/13(세션 초기화) + 진행률 조회."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from src.rag_chatbot.progress import PROGRESS

from ...core.errors import ApiError

from ...schemas.auth import MessageResponse
from ...schemas.chat import (
    ChatProgressResponse,
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

# 진행률 조회용 토큰. 클라이언트가 요청마다 새로 만들어 보내고, 같은 값으로
# GET /progress/{token}을 폴링한다. 상담 시작(API-10/14)은 session_id를 서버가
# 만들기 때문에 응답이 오기 전까지 클라이언트가 조회할 이름이 없어서 필요하다.
# 본문이 아니라 헤더인 이유: API-14는 요청 본문/쿼리 자체를 금지한다
# (_no_recommendation_input).
_MAX_PROGRESS_TOKEN_LENGTH = 64
ProgressToken = Annotated[
    str | None, Header(alias="X-Progress-Token", max_length=_MAX_PROGRESS_TOKEN_LENGTH)
]


async def _no_recommendation_input(request: Request) -> None:
    if request.query_params or await request.body():
        raise ApiError(400, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다.")


@router.post("/recommendations", response_model=ChatResponse)
def recommend(
    response: Response,
    current: AuthSessionRecord = Depends(get_current_user),
    _input: None = Depends(_no_recommendation_input),
    progress_token: ProgressToken = None,
) -> ChatResponse:
    response.headers["Cache-Control"] = "no-store"
    with user_operation(current) as profile:
        return chat_adapter.start_recommendations(
            profile, user_id=current.user_id, progress_token=progress_token
        )


@router.post("/messages", response_model=ChatResponse)
def send_message(
    payload: ChatRequest,
    current: AuthSessionRecord = Depends(get_current_user),
    progress_token: ProgressToken = None,
) -> ChatResponse:
    with user_operation(current):
        return chat_adapter.start_chat(
            payload, user_id=current.user_id, progress_token=progress_token
        )


@router.post("/sessions/{session_id}/followup", response_model=ChatResponse)
def send_followup(
    session_id: str,
    payload: FollowupRequest,
    current: AuthSessionRecord = Depends(get_current_user),
    progress_token: ProgressToken = None,
) -> ChatResponse:
    with user_operation(current):
        answer = payload.calc_answers.model_dump() if payload.calc_answers is not None else payload.message
        return chat_adapter.continue_chat(
            session_id, answer, user_id=current.user_id, progress_token=progress_token
        )


@router.get("/progress/{token}", response_model=ChatProgressResponse)
def get_progress(
    token: str,
    response: Response,
    current: AuthSessionRecord = Depends(get_current_user),
) -> ChatProgressResponse:
    """진행 중인 상담이 지금 어느 단계인지 알려준다(프론트 진행 막대).

    ``user_operation``(회원별 직렬화 잠금)을 **쓰지 않는다** - 그 잠금은 지금
    돌고 있는 상담 요청이 쥐고 있어서, 여기서 같이 기다리면 상담이 끝날 때까지
    진행률을 한 번도 못 읽는다. 즉 진행 막대가 아무 의미가 없어진다. 이
    엔드포인트는 남의 상태를 못 보게 막는 소유권 검사(``owner``)만 하고 읽기만
    한다.

    기록이 없으면(아직 시작 전이거나 보관 기간이 지남) ``status="unknown"``을
    200으로 돌려준다 - 폴링이 404 오류 배너를 띄우게 하지 않기 위함이다.
    """

    response.headers["Cache-Control"] = "no-store"
    snapshot = PROGRESS.snapshot(token, owner=current.user_id)
    if snapshot is None:
        return ChatProgressResponse(status="unknown")
    return ChatProgressResponse(**snapshot)


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
