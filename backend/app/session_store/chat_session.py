"""채팅 세션(LangGraph thread_id) 소유권 + 마지막 응답 캐시.

``session_id``는 ``src/rag_chatbot/graph/builder.py``에서 LangGraph
``thread_id``로 그대로 쓰인다(변환 없음). LangGraph의 ``MemorySaver``는
"이 thread_id가 어느 로그인 사용자 것인지"를 모르므로, 다른 사용자의
session_id를 추측해 이어 쓰는 것을 막으려면 이 저장소가 소유권을 따로
기록해야 한다(요구사항: API-11/12/13 "다른 사용자 세션 접근 차단").

동시에 이 저장소는 API-12(정책 상세 문의)가 필요로 하는 "이 세션에서 마지막
으로 받은 policies/profile"도 함께 캐시한다 - API-12는 무거운 그래프를 다시
돌리지 않고 이 캐시에서 policy_id를 찾아 ``light_followup``에 넘긴다
(API_정의서.xlsx API-12 설명 참고).

이 저장소에 session_id가 없다는 것 자체가 "세션 없음/만료"의 판단 기준이다
- LangGraph 내부 예외 타입에 의존하지 않는다(``app/core/errors.py`` 참고).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class ChatSessionRecord:
    user_id: int
    last_policies: list[dict] = field(default_factory=list)
    last_profile: list[dict] = field(default_factory=list)


class ChatSessionStore:
    def __init__(self):
        self._sessions: dict[str, ChatSessionRecord] = {}
        self._lock = threading.Lock()

    def create(self, session_id: str, *, user_id: int) -> None:
        with self._lock:
            self._sessions[session_id] = ChatSessionRecord(user_id=user_id)

    def get(self, session_id: str, *, user_id: int) -> ChatSessionRecord | None:
        """소유자가 일치할 때만 레코드를 반환한다 - 다른 사용자에게는 그냥 '없음'."""

        with self._lock:
            record = self._sessions.get(session_id)
            if record is None or record.user_id != user_id:
                return None
            return record

    def update_last_response(
        self, session_id: str, *, policies: list[dict], profile: list[dict]
    ) -> None:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is not None:
                record.last_policies = policies
                record.last_profile = profile

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


chat_session_store = ChatSessionStore()
