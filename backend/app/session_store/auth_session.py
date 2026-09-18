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
    revoked: bool = False


class AuthSessionStore:
    def __init__(self, *, ttl_days: int = 7):
        self._ttl = timedelta(days=ttl_days)
        self._sessions: dict[str, AuthSessionRecord] = {}
        self._lock = threading.Lock()
        self._next_cleanup: datetime | None = None

    def _prune_expired(self, now: datetime) -> None:
        """호출자는 _lock을 보유한다. 사용하지 않는 만료 토큰도 회수한다."""

        if self._next_cleanup is not None and now < self._next_cleanup:
            return
        # ponytail: 분당 최대 한 번 O(n) 순회. 대규모 세션은 TTL 저장소로 전환.
        expired = [token for token, record in self._sessions.items() if record.expires_at < now]
        for token in expired:
            self._sessions.pop(token).revoked = True
        self._next_cleanup = now + timedelta(minutes=1)

    def create(self, token: str, *, user_id: int, username: str) -> None:
        now = datetime.now(timezone.utc)
        record = AuthSessionRecord(
            user_id=user_id,
            username=username,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._prune_expired(now)
            previous = self._sessions.get(token)
            if previous is not None:
                previous.revoked = True
            self._sessions[token] = record

    def get(self, token: str) -> AuthSessionRecord | None:
        with self._lock:
            now = datetime.now(timezone.utc)
            self._prune_expired(now)
            record = self._sessions.get(token)
            if record is None:
                return None
            if record.expires_at < now:
                self._sessions.pop(token).revoked = True
                return None
            return record

    def is_active(self, record: AuthSessionRecord) -> bool:
        """잠금을 기다리던 요청이 보유한 레코드에도 폐기/만료를 즉시 반영한다."""

        with self._lock:
            return not record.revoked and record.expires_at >= datetime.now(timezone.utc)

    def delete(self, token: str) -> None:
        with self._lock:
            record = self._sessions.pop(token, None)
            if record is not None:
                record.revoked = True

    def delete_all_for_user(self, user_id: int, *, except_record: AuthSessionRecord | None = None) -> None:
        """회원 세션을 폐기한다. 비밀번호 변경은 현재 레코드만 유지한다."""

        with self._lock:
            stale = [
                token for token, record in self._sessions.items()
                if record.user_id == user_id and record is not except_record
            ]
            for token in stale:
                self._sessions.pop(token).revoked = True


auth_session_store = AuthSessionStore(ttl_days=settings.auth_session_ttl_days)
