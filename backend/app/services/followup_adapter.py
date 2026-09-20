"""API-12(정책 상세 문의)의 ``light_followup.respond_to_policy_question`` 호출 어댑터.

무거운 N1~N14 그래프를 다시 돌리지 않는다 - 세션에 캐시된 마지막
``policies``/``profile``에서 ``policy_id``가 일치하는 항목만 컨텍스트로
쓴다(API_정의서.xlsx API-12 설명, ``session_store/chat_session.py`` 참고).
"""

from __future__ import annotations

from fastapi import status

from src.rag_chatbot.light_followup import respond_to_policy_question
from src.rag_chatbot.service import get_llm_client

from ..core.errors import ApiError
from ..schemas.chat import PolicyQuestionResponse
from ..session_store.chat_session import chat_session_store


def ask_policy_question(
    session_id: str, policy_id: str, question: str, *, user_id: int
) -> PolicyQuestionResponse:
    record = chat_session_store.get(session_id, user_id=user_id)
    if record is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "SESSION_NOT_FOUND",
            "세션이 만료되었거나 존재하지 않습니다.",
        )
    policy = next(
        (p for p in record.last_policies if p.get("policy_id") == policy_id), None
    )
    if policy is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "POLICY_NOT_FOUND_IN_SESSION",
            "이 세션에서 추천된 정책이 아닙니다.",
        )
    try:
        result = respond_to_policy_question(
            policy,
            question,
            llm_client=get_llm_client(),
            user_profile=record.last_profile,
        )
    except SystemExit as exc:
        # get_llm_client()도 내부적으로 get_graph()를 거쳐 vectorDB에 연결한다
        # (service.py 참고) - chat_adapter._run()과 동일한 이유로 변환한다.
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "VECTOR_STORE_UNAVAILABLE",
            "검색 서비스에 일시적으로 연결할 수 없습니다.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "일시적인 오류가 발생했습니다. 다시 시도해주세요.",
        ) from exc
    return PolicyQuestionResponse(**result)
