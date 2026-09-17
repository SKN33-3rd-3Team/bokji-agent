"""API-01~08 라우팅/에러코드 테스트. 실제 auth.service + 격리된 SQLite DB 사용."""

from __future__ import annotations

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
    assert body["marketing_opt_in"] is False


def test_signup_duplicate_email_is_409(client):
    _signup(client)
    r = _signup(client)
    assert r.status_code == 409
    assert r.json()["code"] == "USERNAME_TAKEN"


def test_signup_without_required_agreements_is_400(client):
    r = _signup(client, terms_agreed=False)
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


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


def test_signup_and_profile_interests_follow_signup_options(client):
    options = client.get("/api/v1/config/search-options").json()
    allowed = options["signup_interest_options"]
    sidebar_only = set(options["sidebar_interest_options"]) - set(allowed)
    assert sidebar_only
    for interest in sidebar_only:
        r = _signup(client, interests=[interest])
        assert r.status_code == 400
        assert r.json()["code"] == "VALIDATION_ERROR"

    r = _signup(client, interests=allowed)
    assert r.status_code == 201
    assert r.json()["user"]["interests"] == allowed
    for interest in sidebar_only:
        r = client.patch("/api/v1/users/me", json={"interests": [interest]})
        assert r.status_code == 400
        assert r.json()["code"] == "VALIDATION_ERROR"
        assert client.get("/api/v1/users/me").json()["interests"] == allowed

    r = client.patch("/api/v1/users/me", json={"interests": allowed[::-1]})
    assert r.status_code == 200
    assert r.json()["interests"] == allowed[::-1]
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


def test_me_membership_grade_is_fixed_general(client):
    # 요구사항_정의서.xlsx S09-01: 등급 체계가 백엔드에 없어 항상 "일반 회원" 고정값.
    _signup(client)
    r = client.get("/api/v1/users/me")
    assert r.status_code == 200
    assert r.json()["membership_grade"] == "일반 회원"


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
