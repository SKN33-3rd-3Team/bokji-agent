"""조회되지 않은 만료 토큰 정리와 즉시 만료 판정."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.session_store import auth_session


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 1, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(auth_session, "datetime", Clock)
    return Clock


@pytest.mark.parametrize("activity", ["create", "get"])
def test_unused_expired_sessions_are_pruned_on_activity(clock, activity):
    store = auth_session.AuthSessionStore(ttl_days=7)
    store.create("old", user_id=1, username="old@example.com")
    clock.current += timedelta(days=6)
    store.create("active", user_id=2, username="active@example.com")
    clock.current += timedelta(days=1, seconds=1)
    if activity == "create":
        store.create("new", user_id=3, username="new@example.com")
    else:
        assert store.get("active").user_id == 2
    assert "old" not in store._sessions
    assert store.get("active").user_id == 2


def test_sweep_is_throttled_but_requested_expired_token_is_rejected(clock):
    store = auth_session.AuthSessionStore(ttl_days=7)
    store.create("old", user_id=1, username="old@example.com")
    store.create("unused", user_id=1, username="old@example.com")
    clock.current += timedelta(days=7, seconds=-1)
    store.create("active", user_id=2, username="active@example.com")
    clock.current += timedelta(seconds=2)
    assert store.get("active") is not None
    assert "unused" in store._sessions  # 분당 순회 제한은 유지한다.
    assert store.get("old") is None     # 개별 토큰의 만료 판정은 즉시 적용한다.
    clock.current += timedelta(minutes=1)
    assert store.get("active") is not None
    assert "unused" not in store._sessions
