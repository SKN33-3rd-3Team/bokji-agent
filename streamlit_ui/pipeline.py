"""Streamlit 입력을 공식 N1~N14 서비스 API로 전달하는 얇은 어댑터."""

from __future__ import annotations

from src.rag_chatbot.service import ChatResponse, answer_followup, ask


def run_pipeline(
    *,
    user_input: str,
    session_id: str,
    awaiting_followup: bool,
    top_k: int,
    extra_interests: list[str] | None = None,
) -> ChatResponse:
    """첫 질문은 ``ask``, N3 응답은 같은 세션의 ``answer_followup``으로 보낸다.

    ``extra_interests``(사이드바에서 고른 지원조건·관심 분야)는 첫 질문에만
    전달한다. 재개 중에는 체크포인터의 슬롯이 이미 확정돼 있어서
    ``answer_followup``이 이 인자를 받지 않는다.
    """

    if awaiting_followup:
        return answer_followup(session_id, user_input)
    return ask(user_input, session_id, top_k=top_k, extra_interests=extra_interests)
