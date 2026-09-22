"""ASGI 진입점의 CORS, lifespan, 공개 경로 계약."""

import pytest
from fastapi.testclient import TestClient

from backend.app.api.v1 import config
from backend.app.core.config import settings
from backend.app.core.errors import ApiError
from backend.app.main import app


@pytest.mark.parametrize("error, status_code", [
    (None, 200),
    (ApiError(503, "UNAVAILABLE", "unavailable"), 503),
    (RuntimeError("unexpected"), 500),
])
def test_cors_covers_success_and_error_responses(monkeypatch, error, status_code):
    def options():
        if error is not None:
            raise error
        return {}

    monkeypatch.setattr(config, "build_search_options", options)
    client = TestClient(app, raise_server_exceptions=False)
    origin = settings.cors_origin_list[0]
    response = client.get("/api/v1/config/search-options", headers={"Origin": origin})
    assert response.status_code == status_code
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]
    if status_code == 500:
        assert response.json()["code"] == "INTERNAL_ERROR"
    denied = client.get("/api/v1/config/search-options", headers={"Origin": "https://other.invalid"})
    assert "access-control-allow-origin" not in denied.headers


def test_app_preserves_lifespan_routes_and_preflight(monkeypatch):
    from src.rag_chatbot import service

    warmed = []
    monkeypatch.setattr(service, "warm_up", lambda: warmed.append(True))
    with TestClient(app) as client:
        # 워밍업은 별도 스레드에서 돈다(main.lifespan) - 임베딩 모델 로딩이
        # 끝날 때까지 서버가 연결을 못 받으면 로그인/회원가입까지 같이 멈춰
        # 보이기 때문이다. 시작됐는지만 결정적으로 확인한다.
        app.state.warmup_thread.join(timeout=5)
        assert warmed == [True]
        assert client.get("/healthz").json() == {"status": "ok"}
        schema = client.get("/openapi.json").json()
        assert "/api/v1/users/me" in schema["paths"]
        assert client.get("/docs").status_code == 200
        response = client.options("/api/v1/users/me", headers={
            "Origin": settings.cors_origin_list[0],
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        })
        assert response.status_code == 200
        assert "PATCH" in response.headers["access-control-allow-methods"]
