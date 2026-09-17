"""세션의 회원 ID를 실제 행 처리까지 유지하는 계약. 네트워크/경합 재현 없음."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from backend.app.api.deps import get_current_user
from backend.app.main import app
from backend.app.session_store.auth_session import AuthSessionRecord, auth_session_store
from src.rag_chatbot.auth import service


@pytest.fixture
def backend(monkeypatch):
    backend, connection = Mock(), Mock()
    monkeypatch.setattr(service, "_open", lambda db_path: (backend, connection))
    return backend, connection


@pytest.mark.parametrize("method, path, payload", [
    ("GET", "/api/v1/users/me", None),
    ("GET", "/api/v1/users/me/chat-defaults", None),
    ("PATCH", "/api/v1/users/me", {"region": "서울특별시"}),
    ("POST", "/api/v1/users/me/password", {
        "current_password": "Passw0rd!123", "new_password": "NewPassw0rd!123",
    }),
    ("DELETE", "/api/v1/users/me", {"password": "Passw0rd!123"}),
])
@pytest.mark.parametrize("row", [None, {"id": 8}])
def test_routes_validate_actual_row_identity(client, backend, method, path, payload, row):
    repo, connection = backend
    repo.get_user_by_username.return_value = row
    # 공통 dependency의 사전 확인에 의존하지 않고, 실제 작업 단계에서 검사한다.
    app.dependency_overrides[get_current_user] = lambda: AuthSessionRecord(
        user_id=7, username="user@example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    try:
        response = client.request(method, path, json=payload)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    repo.update_profile_fields.assert_not_called()
    repo.set_password_hash.assert_not_called()
    repo.delete_user.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize("fresh", [None, {"id": 8}])
def test_profile_update_rechecks_returned_identity(backend, fresh):
    repo, connection = backend
    repo.get_user_by_username.side_effect = [{"id": 7}, fresh]
    with pytest.raises(service.UserNotFoundError):
        service.update_profile("user@example.com", region="서울특별시", expected_user_id=7)
    repo.update_profile_fields.assert_called_once_with(connection, 7, region="서울특별시")
    connection.close.assert_called_once()


@pytest.mark.parametrize("unavailable", [False, True])
def test_dependency_rejects_invalid_identity_but_preserves_session_on_db_outage(
    client, backend, unavailable,
):
    repo, _ = backend
    repo.get_user_by_username.return_value = {"id": 8}
    if unavailable:
        repo.get_user_by_username.side_effect = service.AuthBackendUnavailableError("unavailable")
    auth_session_store.create("test-token", user_id=7, username="user@example.com")
    client.cookies.set("session_id", "test-token")
    response = client.get("/api/v1/users/me")
    assert response.status_code == (503 if unavailable else 401)
    assert response.json()["code"] == ("AUTH_BACKEND_UNAVAILABLE" if unavailable else "UNAUTHORIZED")
    assert (auth_session_store.get("test-token") is not None) == unavailable
