"""API-01~08 라우팅/에러코드 테스트. 실제 auth.service + 격리된 SQLite DB 사용."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

_SIGNUP_PAYLOAD = {
    "email": "tester@example.com",
    "password": "Passw0rd!123",
    "password_confirm": "Passw0rd!123",
    "name": "Tester",
    "terms_agreed": True,
    "privacy_agreed": True,
}


def _signup(client, **overrides):
    payload = {**_SIGNUP_PAYLOAD, **overrides}
    return client.post("/api/v1/auth/signup", json=payload)


def test_signup_sets_session_cookie_and_returns_user(client):
    r = _signup(client)
    assert r.status_code == 201
    assert "session_id" in r.cookies
    body = r.json()["user"]
    assert body["email"] == _SIGNUP_PAYLOAD["email"]
    assert body["display_name"] == "Tester"
    assert "marketing_opt_in" not in body


def test_signup_reauthenticates_under_user_lock(client, monkeypatch):
    from backend.app.services import auth_adapter
    from backend.app.session_store.chat_session import chat_session_store

    original = auth_adapter.login
    calls = []

    def locked_login(email, password):
        user = original(email, password)
        assert chat_session_store._user_locks[user.id].locked()
        calls.append((email, password))
        return user

    monkeypatch.setattr(auth_adapter, "login", locked_login)
    r = _signup(client)
    assert calls == [(_SIGNUP_PAYLOAD["email"], _SIGNUP_PAYLOAD["password"])]
    assert r.status_code == 201
    assert "session_id" in r.cookies
    body = r.json()["user"]
    assert body["email"] == _SIGNUP_PAYLOAD["email"]
    assert body["display_name"] == "Tester"
    assert client.get("/api/v1/users/me").status_code == 200


def test_signup_duplicate_email_is_409(client):
    _signup(client)
    r = _signup(client)
    assert r.status_code == 409
    assert r.json()["code"] == "USERNAME_TAKEN"


@pytest.mark.parametrize("field", ["terms_agreed", "privacy_agreed"])
@pytest.mark.parametrize("omitted", [False, True])
def test_signup_without_required_agreements_is_400(client, field, omitted):
    payload = {**_SIGNUP_PAYLOAD, "marketing_opt_in": True, field: False}
    if omitted:
        payload.pop(field)
    r = client.post("/api/v1/auth/signup", json=payload)
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("retired_value", [True, False, {"unexpected": "value"}])
def test_signup_ignores_retired_marketing_field(client, isolated_auth_db, retired_value):
    r = _signup(client, marketing_opt_in=retired_value)
    assert r.status_code == 201
    assert "marketing_opt_in" not in r.json()["user"]
    # 기존 extra 무시 정책을 유지하며 클라이언트 값은 저장하지 않는다.
    with sqlite3.connect(isolated_auth_db) as conn:
        assert conn.execute("SELECT marketing_opt_in FROM users").fetchall() == [(0,)]


def test_openapi_has_no_marketing_field(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "marketing_opt_in" not in r.text


@pytest.mark.parametrize("birth_date", [
    "20000101", "2000-W01-1", "2000-2-29", " 2000-02-29",
    "2000-02-29\n", "２０００-０２-２９", "2001-02-29", "2999-01-01", "1800-01-01",
])
def test_auth_invalid_birth_date_is_400_without_profile_change(client, birth_date):
    r = _signup(client, birth_date=birth_date)
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert "session_id" not in r.cookies

    assert _signup(client, birth_date="2000-02-29").status_code == 201
    r = client.patch("/api/v1/users/me", json={"birth_date": birth_date})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert client.get("/api/v1/users/me").json()["birth_date"] == "2000-02-29"


def test_birth_date_leap_date_omission_and_profile_clearing(client):
    r = _signup(client, birth_date="2000-02-29")
    assert r.status_code == 201
    assert r.json()["user"]["birth_date"] == "2000-02-29"
    assert client.patch("/api/v1/users/me", json={}).json()["birth_date"] == "2000-02-29"
    for clear_value in (None, ""):
        r = client.patch("/api/v1/users/me", json={"birth_date": clear_value})
        assert r.status_code == 200
        assert r.json()["birth_date"] == ""
        r = client.patch("/api/v1/users/me", json={"birth_date": "2000-02-29"})
        assert r.status_code == 200
        assert r.json()["birth_date"] == "2000-02-29"


def test_display_name_is_normalized_then_truncated(client):
    name = "  홍\t길\n동\x00  " + "가" * 40
    expected = "홍 길 동 " + "가" * 34
    r = _signup(client, name=name)
    assert r.status_code == 201
    assert r.json()["user"]["display_name"] == expected
    r = client.patch("/api/v1/users/me", json={"display_name": name})
    assert r.status_code == 200
    assert r.json()["display_name"] == expected


def test_signup_password_mismatch_is_400(client):
    r = _signup(client, password_confirm="different")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_signup_blank_name_is_400(client):
    r = _signup(client, name="   ")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_signup_invalid_region_is_400(client):
    r = _signup(client, region="없는지역")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_signup_invalid_interest_is_400(client):
    r = _signup(client, interests=["없는조건"])
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_signup_accepts_interest_field_options(client):
    """회원가입(API-01) 화면도 마이페이지와 같은 interest_field_options
    (19종) 목록을 보여준다(2026-09-22 - 이전엔 가입만 signup_interest_
    options 4종으로 더 좁았다) - 서버도 그 값을 그대로 허용해야 한다.
    계약 밖 값은 여전히 거부한다(test_signup_invalid_interest_is_400)."""

    options = client.get("/api/v1/config/search-options").json()
    field_options = options["interest_field_options"]

    r = _signup(client, interests=field_options)
    assert r.status_code == 201
    assert r.json()["user"]["interests"] == field_options


def test_profile_update_accepts_interest_field_options(client):
    """마이페이지 수정(API-05)은 interest_field_options(19종)를 허용한다 -
    화면과 서버 허용값이 어긋나 전체 PATCH가 거부되던 회귀(관심조건
    저장 실패로 다른 필드까지 함께 저장 안 되던 버그)의 재발 방지용.
    예전 회원가입 4종(signup_interest_options)으로 이미 가입한 계정도
    이후 재검증에서 거부되지 않고(하위 호환), 계약 밖 값은 여전히
    거부한다."""

    options = client.get("/api/v1/config/search-options").json()
    field_options = options["interest_field_options"]
    _signup(client, interests=field_options)

    r = client.patch("/api/v1/users/me", json={"interests": field_options})
    assert r.status_code == 200
    assert r.json()["interests"] == field_options

    # 예전 signup_interest_options(4종)으로 가입한 계정의 값도 여전히 통과해야 한다.
    legacy = ["임신/출산", "노인/어르신"]
    r = client.patch("/api/v1/users/me", json={"interests": legacy})
    assert r.status_code == 200
    assert r.json()["interests"] == legacy

    r = client.patch("/api/v1/users/me", json={"interests": ["존재하지-않는-관심조건"]})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert client.get("/api/v1/users/me").json()["interests"] == legacy

    r = client.patch("/api/v1/users/me", json={"interests": None})
    assert r.status_code == 200
    assert r.json()["interests"] == []


def test_login_success(client):
    _signup(client)
    r = client.post(
        "/api/v1/auth/login",
        json={"email": _SIGNUP_PAYLOAD["email"], "password": _SIGNUP_PAYLOAD["password"]},
    )
    assert r.status_code == 200
    assert "session_id" in r.cookies


def test_login_wrong_password_is_401(client):
    _signup(client)
    r = client.post(
        "/api/v1/auth/login",
        json={"email": _SIGNUP_PAYLOAD["email"], "password": "wrong-password"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.parametrize("deletion_time", ["before_update", "after_commit"])
def test_login_deleted_during_failed_attempt_is_401(client, isolated_auth_db, monkeypatch, deletion_time):
    from src.rag_chatbot.auth import service
    from backend.app.session_store.auth_session import auth_session_store

    signup = _signup(client)
    assert signup.status_code == 201
    user_id = signup.json()["user"]["id"]
    client.cookies.clear()
    sessions_before = dict(auth_session_store._sessions)
    record_failed_login = service.repo.record_failed_login
    outcomes = []

    def delete_account_row():
        with sqlite3.connect(isolated_auth_db) as other:
            assert other.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount == 1

    def record_after_deletion(conn, current_id, **kwargs):
        assert current_id == user_id

        def commit_then_delete():
            conn.commit()
            assert conn.execute("SELECT failed_login_count FROM users WHERE id = ?", (user_id,)).fetchone()[0] == 1
            delete_account_row()

        if deletion_time == "before_update":
            delete_account_row()
            recording_conn = conn
        else:
            recording_conn = SimpleNamespace(execute=conn.execute, commit=commit_then_delete)
        outcome = record_failed_login(recording_conn, current_id, **kwargs)
        outcomes.append(outcome)
        assert not conn.in_transaction
        return outcome

    monkeypatch.setattr(service.repo, "record_failed_login", record_after_deletion)
    response = client.post("/api/v1/auth/login", json={
        "email": _SIGNUP_PAYLOAD["email"], "password": "Wrong123!",
    })
    assert (response.status_code, response.json()["code"]) == (401, "INVALID_CREDENTIALS")
    assert outcomes == [(0, None)]
    assert "session_id" not in response.cookies
    assert auth_session_store._sessions == sessions_before


def test_me_requires_login(client):
    r = client.get("/api/v1/users/me")
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_me_after_login_returns_profile(client):
    _signup(client)
    r = client.get("/api/v1/users/me")
    assert r.status_code == 200
    assert r.json()["email"] == _SIGNUP_PAYLOAD["email"]
    assert r.headers["cache-control"] == "no-store"


def test_profile_routes_keep_grade_and_hide_legacy_marketing(client, isolated_auth_db):
    r = _signup(client)
    assert r.status_code == 201
    profiles = [r.json()["user"]]
    # 과거 동의 데이터는 유지하되 HTTP 프로필에 노출하거나 수정하지 않는다.
    with sqlite3.connect(isolated_auth_db) as conn:
        conn.execute("UPDATE users SET marketing_opt_in = 1")
    r = client.post("/api/v1/auth/login", json={
        "email": _SIGNUP_PAYLOAD["email"], "password": _SIGNUP_PAYLOAD["password"],
    })
    assert r.status_code == 200
    profiles.append(r.json()["user"])
    r = client.get("/api/v1/users/me")
    assert r.status_code == 200
    profiles.append(r.json())
    r = client.patch("/api/v1/users/me", json={"marketing_opt_in": False})
    assert r.status_code == 200
    profiles.append(r.json())
    for profile in profiles:
        assert profile["membership_grade"] == "일반 회원"
        assert "marketing_opt_in" not in profile
    with sqlite3.connect(isolated_auth_db) as conn:
        assert conn.execute("SELECT marketing_opt_in FROM users").fetchall() == [(1,)]


def test_update_profile_partial_fields(client):
    _signup(client)
    r = client.patch("/api/v1/users/me", json={"region": "서울특별시", "gender": "female"})
    assert r.status_code == 200
    body = r.json()
    assert body["region"] == "서울특별시"
    assert body["gender"] == "female"

    # 다시 조회해도 값이 유지되는지 (실제 DB round-trip 확인)
    r = client.get("/api/v1/users/me")
    assert r.json()["region"] == "서울특별시"


def test_update_profile_invalid_region_is_400(client):
    _signup(client)
    r = client.patch("/api/v1/users/me", json={"region": "없는지역"})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_update_profile_omitted_field_is_unchanged_but_explicit_null_clears_it(client):
    """API_정의서.xlsx API-05: "필드를 아예 보내지 않으면 '수정하지 않음',
    null/빈 값을 보내면 '값을 지움'". model_dump(exclude_unset=True)만으로는
    이 세 상태(안 보냄/null/빈 값)를 다 구분하지 못해 실제 버그가 있었다
    (코드 리뷰로 발견, auth_adapter._normalize_clear_semantics로 수정) - 이
    테스트가 그 회귀를 막는다.
    """

    _signup(client)
    client.patch("/api/v1/users/me", json={"region": "서울특별시", "gender": "female"})

    # region을 아예 안 보내면 - 미수정 유지
    r = client.patch("/api/v1/users/me", json={"gender": "male"})
    assert r.status_code == 200
    assert r.json()["region"] == "서울특별시"
    assert r.json()["gender"] == "male"

    # region을 명시적으로 null로 보내면 - 지워져야 함(빈 문자열)
    r = client.patch("/api/v1/users/me", json={"region": None})
    assert r.status_code == 200
    assert r.json()["region"] == ""
    # 같이 안 보낸 gender는 여전히 미수정 유지
    assert r.json()["gender"] == "male"

    r = client.patch("/api/v1/users/me", json={"household_types": ["single_person"]})
    assert r.json()["household_types"] == ["single_person"]
    r = client.patch("/api/v1/users/me", json={"household_types": None})
    assert r.json()["household_types"] == []


def test_change_password_wrong_current_is_401(client):
    _signup(client)
    r = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "wrong", "new_password": "NewPassw0rd!1"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_CURRENT_PASSWORD"


def test_change_password_same_as_current_is_400(client):
    _signup(client)
    r = client.post(
        "/api/v1/users/me/password",
        json={
            "current_password": _SIGNUP_PAYLOAD["password"],
            "new_password": _SIGNUP_PAYLOAD["password"],
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SAME_AS_CURRENT"


def test_change_password_success_then_old_password_fails(client):
    _signup(client)
    r = client.post(
        "/api/v1/users/me/password",
        json={
            "current_password": _SIGNUP_PAYLOAD["password"],
            "new_password": "NewPassw0rd!1",
        },
    )
    assert r.status_code == 200

    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"email": _SIGNUP_PAYLOAD["email"], "password": _SIGNUP_PAYLOAD["password"]},
    )
    assert r.status_code == 401


def test_delete_account_requires_correct_password_then_invalidates_session(client):
    _signup(client)
    r = client.request(
        "DELETE", "/api/v1/users/me", json={"password": "wrong-password"}
    )
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_CREDENTIALS"

    r = client.request(
        "DELETE", "/api/v1/users/me", json={"password": _SIGNUP_PAYLOAD["password"]}
    )
    assert r.status_code == 200

    r = client.get("/api/v1/users/me")
    assert r.status_code == 401


def test_logout_is_idempotent(client):
    _signup(client)
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 200

    # 쿠키가 이미 없는데 다시 호출해도 여전히 200(멱등)
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 200


def test_chat_defaults_reflects_profile(client):
    _signup(client)
    client.patch("/api/v1/users/me", json={"region": "부산광역시"})
    r = client.get("/api/v1/users/me/chat-defaults")
    assert r.status_code == 200
    body = r.json()
    assert body["known_region"] == "부산광역시"
    assert body["known_gender"] is None
    assert body["employment_status_available"] is False


def test_search_options_is_public(client):
    r = client.get("/api/v1/config/search-options")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sido_options"]) == 17
    assert body["default_top_k"] == 5
