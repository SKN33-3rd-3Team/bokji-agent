"""비밀번호 변경과 로그인 발급/인증 후 대기의 순서를 실제 SQLite로 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
import sqlite3

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from backend.app.api.deps import get_current_user
from backend.app.services import auth_adapter, chat_adapter
from backend.app.session_store.auth_session import auth_session_store
from backend.app.session_store.chat_session import chat_session_store
from backend.tests.test_account_chat_cleanup import signup, withdraw
from backend.tests.test_chat_session_lifecycle import ObservedLock

OLD = "Passw0rd!123"
NEW = "NewPassw0rd!123"
EMAIL = "owner@example.com"


def change(client, current=OLD, new=NEW):
    return client.post("/api/v1/users/me/password", json={
        "current_password": current, "new_password": new,
    })


def login(client, password=OLD):
    return client.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})


@pytest.fixture
def sessions(client):
    user_id = signup(client, EMAIL)
    second = TestClient(client.app)
    assert login(second).status_code == 200
    other = TestClient(client.app)
    signup(other, "other@example.com")
    return client, second, other, user_id


def test_password_change_keeps_current_session_and_revokes_only_others(sessions):
    current, second, other, _ = sessions
    token = current.cookies.get("session_id")
    record = auth_session_store.get(token)
    expiry = record.expires_at
    assert change(current).status_code == 200
    assert current.cookies.get("session_id") == token
    assert auth_session_store.get(token) is record
    assert record.expires_at == expiry and expiry.utcoffset().total_seconds() == 0
    assert current.get("/api/v1/users/me").status_code == 200
    assert second.get("/api/v1/users/me").status_code == 401
    assert other.get("/api/v1/users/me").status_code == 200
    assert login(second).status_code == 401
    assert login(second, NEW).status_code == 200


@pytest.mark.parametrize("failure, expected", [("password", 401), ("same", 400), ("policy", 400), ("db", 503)])
def test_failed_password_change_preserves_sessions(sessions, isolated_auth_db, failure, expected):
    current, second, _, _ = sessions
    before = dict(auth_session_store._sessions)
    if failure == "db":
        with sqlite3.connect(isolated_auth_db) as conn:
            conn.execute("CREATE TRIGGER fail_password BEFORE UPDATE OF password_hash ON users "
                         "BEGIN SELECT missing_test_function(); END")
    result = change(current, "wrong" if failure == "password" else OLD,
                    OLD if failure == "same" else "weak" if failure == "policy" else NEW)
    assert result.status_code == expected
    assert auth_session_store._sessions == before
    assert current.get("/api/v1/users/me").status_code == 200
    assert second.get("/api/v1/users/me").status_code == 200


@pytest.mark.parametrize("method, path, payload", [
    ("GET", "/api/v1/users/me", None),
    ("GET", "/api/v1/users/me/chat-defaults", None),
    ("PATCH", "/api/v1/users/me", {"display_name": "late"}),
    ("POST", "/api/v1/users/me/password", {"current_password": NEW, "new_password": OLD}),
    ("DELETE", "/api/v1/users/me", {"password": NEW}),
    ("POST", "/api/v1/chat/messages", {"message": "late"}),
    ("POST", "/api/v1/chat/sessions/owned/followup", {"message": "late"}),
    ("DELETE", "/api/v1/chat/sessions/owned", None),
])
def test_preauthenticated_requests_respect_revocation_boundary(sessions, monkeypatch, method, path, payload):
    current, second, _, user_id = sessions
    authenticated, release = Event(), Event()
    stale_token = second.cookies.get("session_id")
    chat_session_store.create("owned", user_id=user_id)
    start = Mock(return_value={"session_id": "late", "status": "answered"})
    resume = Mock(return_value={"session_id": "owned", "status": "answered"})
    monkeypatch.setattr(chat_adapter, "start_chat", start)
    monkeypatch.setattr(chat_adapter, "continue_chat", resume)
    delete = Mock()
    monkeypatch.setattr(chat_adapter, "delete_chat_session", delete)

    def delayed(request: Request):
        record = get_current_user(request)
        if request.cookies.get("session_id") == stale_token:
            authenticated.set()
            assert release.wait(10)
        return record

    monkeypatch.setitem(current.app.dependency_overrides, get_current_user, delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(second.request, method, path, json=payload)
        try:
            assert authenticated.wait(10)
            assert change(current).status_code == 200
        finally:
            release.set()
        response = pending.result(timeout=10)
    if (method, path) == ("GET", "/api/v1/users/me"):
        # 승인된 계약: 폐기 전에 인증한 읽기 요청은 200으로 완료될 수 있다.
        assert response.status_code == 200 and response.json()["id"] == user_id
        assert response.headers["cache-control"] == "no-store"
    else:
        assert (response.status_code, response.json().get("code")) == (401, "UNAUTHORIZED")
    after_revocation = second.request(method, path, json=payload)
    assert (after_revocation.status_code, after_revocation.json().get("code")) == (401, "UNAUTHORIZED")
    start.assert_not_called()
    resume.assert_not_called()
    delete.assert_not_called()
    assert chat_session_store.get("owned", user_id=user_id) is not None


@pytest.mark.parametrize("mutation", ["password", "withdraw_and_register"])
def test_signup_committed_before_account_change_cannot_issue_old_token(client, monkeypatch, mutation):
    pending_client = TestClient(client.app)
    registered, release = Event(), Event()
    original = auth_adapter.signup

    def pause(payload):
        result = original(payload)
        if not registered.is_set():
            registered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(auth_adapter, "signup", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(pending_client.post, "/api/v1/auth/signup", json={
            "email": EMAIL, "name": "Tester", "password": OLD,
            "password_confirm": OLD, "terms_agreed": True, "privacy_agreed": True,
        })
        try:
            assert registered.wait(10)
            authenticated = login(client)
            assert authenticated.status_code == 200
            if mutation == "password":
                assert change(client).status_code == 200
            else:
                assert withdraw(client).status_code == 200
                assert signup(client, EMAIL) != authenticated.json()["user"]["id"]
            sessions_after_change = dict(auth_session_store._sessions)
        finally:
            release.set()
        result = pending.result(timeout=10)
    assert result.status_code == 401
    assert "set-cookie" not in result.headers
    assert pending_client.get("/api/v1/users/me").status_code == 401
    assert auth_session_store._sessions == sessions_after_change
    assert client.get("/api/v1/users/me").status_code == 200


@pytest.mark.parametrize("mutation", ["password", "withdraw_and_register"])
def test_login_authenticated_before_account_change_cannot_issue_old_token(sessions, monkeypatch, mutation):
    current, _, _, _ = sessions
    pending_client = TestClient(current.app)
    authenticated, release = Event(), Event()
    original = auth_adapter.login

    def pause(email, password):
        result = original(email, password)
        if not authenticated.is_set():
            authenticated.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(auth_adapter, "login", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(login, pending_client)
        try:
            assert authenticated.wait(10)
            if mutation == "password":
                assert change(current).status_code == 200
            else:
                assert withdraw(current).status_code == 200
                signup(current, EMAIL)
        finally:
            release.set()
        result = pending.result(timeout=10)
    assert result.status_code == 401
    assert "set-cookie" not in result.headers
    assert len([r for r in auth_session_store._sessions.values() if r.username == EMAIL]) == 1


def test_token_issuance_finishes_before_change_then_is_revoked(sessions, monkeypatch):
    current, _, other, user_id = sessions
    pending_client = TestClient(current.app)
    entered, release = Event(), Event()
    lock = ObservedLock()
    chat_session_store._user_locks[user_id] = lock
    original = auth_session_store.create

    def pause(token, **kwargs):
        if kwargs["user_id"] == user_id:
            entered.set()
            assert release.wait(10)
        return original(token, **kwargs)

    monkeypatch.setattr(auth_session_store, "create", pause)
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending_login = pool.submit(login, pending_client)
        try:
            assert entered.wait(10)
            pending_change = pool.submit(change, current)
            assert lock.waiting.wait(3)
            assert not pending_change.done()
            assert pool.submit(other.get, "/api/v1/users/me").result(timeout=5).status_code == 200
        finally:
            release.set()
        assert pending_login.result(timeout=10).status_code == 200
        assert pending_change.result(timeout=10).status_code == 200
    assert pending_client.get("/api/v1/users/me").status_code == 401
    assert current.get("/api/v1/users/me").status_code == 200


def test_profile_get_during_password_change_finishes_but_new_requests_are_revoked(sessions, monkeypatch):
    current, second, _, user_id = sessions
    changed, release = Event(), Event()
    original = auth_adapter.change_password

    def pause(*args, **kwargs):
        original(*args, **kwargs)
        changed.set()
        assert release.wait(10)

    monkeypatch.setattr(auth_adapter, "change_password", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(change, current)
        try:
            assert changed.wait(10)
            # DB 변경 뒤 토큰 폐기 전의 GET은 회원 잠금을 기다리지 않는다.
            waiting = pool.submit(second.get, "/api/v1/users/me")
            result = waiting.result(timeout=5)
            assert result.status_code == 200 and result.json()["id"] == user_id
        finally:
            release.set()
        assert changing.result(timeout=10).status_code == 200
    after_revocation = second.get("/api/v1/users/me")
    assert (after_revocation.status_code, after_revocation.json()["code"]) == (401, "UNAUTHORIZED")
    active = current.get("/api/v1/users/me")
    assert active.status_code == 200 and active.json()["id"] == user_id
