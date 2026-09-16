"""로그인 세션(session_id 쿠키 ↔ user_id) 저장소.

프로세스 메모리(dict) 구현 - 기존 LangGraph ``MemorySaver``(``src/rag_chatbot/
graph/builder.py``)도 동일하게 프로세스 메모리 기반이라 일관되고, 서버
재시작 시 전체 세션이 사라지는 한계도 기존과 동일하다. 나중에 Redis 등으로
교체할 때는 이 클래스의 get/set/delete 인터페이스만 유지하면 된다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..core.config import settings


@dataclass
class AuthSessionRecord:
    user_id: int
    username: str
    expires_at: datetime


class AuthSessionStore:
    def __init__(self, *, ttl_days: int = 7):
        self._ttl = timedelta(days=ttl_days)
        self._sessions: dict[str, AuthSessionRecord] = {}
        self._lock = threading.Lock()

    def create(self, token: str, *, user_id: int, username: str) -> None:
        record = AuthSessionRecord(
            user_id=user_id,
            username=username,
            expires_at=datetime.now(timezone.utc) + self._ttl,
        )
        with self._lock:
            self._sessions[token] = record

    def get(self, token: str) -> AuthSessionRecord | None:
        with self._lock:
            record = self._sessions.get(token)
            if record is None:
                return None
            if record.expires_at < datetime.now(timezone.utc):
                del self._sessions[token]
                return None
            return record

    def delete(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def delete_all_for_user(self, user_id: int) -> None:
        """탈퇴 등으로 해당 사용자의 세션 토큰을 전부 즉시 폐기한다."""

        with self._lock:
            stale = [token for token, record in self._sessions.items() if record.user_id == user_id]
            for token in stale:
                del self._sessions[token]


auth_session_store = AuthSessionStore(ttl_days=settings.auth_session_ttl_days)
