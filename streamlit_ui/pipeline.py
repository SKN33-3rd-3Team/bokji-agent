"""Streamlit 입력을 공식 N1~N14 서비스 API로 전달하는 얇은 어댑터."""

from __future__ import annotations

from src.rag_chatbot.service import ChatResponse, answer_followup, ask


def run_pipeline(
    *,
    user_input: str,
    session_id: str,
    awaiting_followup: bool,
    top_k: int,
) -> ChatResponse:
    """첫 질문은 ``ask``, N3 응답은 같은 세션의 ``answer_followup``으로 보낸다."""

    if awaiting_followup:
        return answer_followup(session_id, user_input)
    return ask(user_input, session_id, top_k=top_k)
