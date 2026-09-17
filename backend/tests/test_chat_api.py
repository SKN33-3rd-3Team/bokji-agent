"""API-10~13 라우팅/스키마/에러코드 테스트.

무거운 LangGraph 실행 자체는 재검증하지 않는다(tests/test_graph_builder.py가
이미 담당) - ``chat_adapter``/``followup_adapter``가 참조하는 ``ask``/
``answer_followup``/``get_graph``/``respond_to_policy_question`` 함수만
monkeypatch해서 HTTP 계약(상태 코드/필드/에러 매핑/세션 소유권)만 확인한다.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from rag_design.vector_store import ChromaUnavailableError

from backend.app.services import chat_adapter, followup_adapter

_SIGNUP_PAYLOAD = {
    "password": "Passw0rd!123",
    "password_confirm": "Passw0rd!123",
    "name": "Chat User",
    "terms_agreed": True,
    "privacy_agreed": True,
}


def _signup(client, email: str):
    return client.post("/api/v1/auth/signup", json={**_SIGNUP_PAYLOAD, "email": email})


def _fake_chat_response(session_id: str, status: str = "answered") -> dict:
    return {
        "status": status,
        "session_id": session_id,
        "question": "지역이 어디신가요?" if status == "needs_input" else None,
        "missing_slots": ["region"] if status == "needs_input" else [],
        "slot_conflicts": None,
        "answer_status": "complete" if status == "answered" else None,
        "final_answer": "정책 안내입니다." if status == "answered" else None,
        "final_citations": [],
        "policies": (
            [
                {
                    "rank": 1,
                    "policy_id": "P1",
                    "title": "청년월세지원",
                    "badge": "추천",
                    "eligibility_status": "충족",
                    "eligibility_reasons": [],
                    "verification_checked": [],
                    "verification_unchecked": [],
                    "detail": {},
                }
            ]
            if status == "answered"
            else []
        ),
        "output_json": {"profile": [{"key": "region", "label": "지역", "value": "서울"}]},
        "output_text": "",
        "output_markdown": "",
        "llm_status": {"enabled": False},
        "timing": {},
    }


def test_chat_requires_login(client):
    r = client.post("/api/v1/chat/messages", json={"message": "안녕하세요"})
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_chat_top_k_out_of_range_is_400(client):
    _signup(client, "chatuser1@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "안녕", "top_k": 21})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_chat_empty_message_is_400(client):
    _signup(client, "chatuser2@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": ""})
    assert r.status_code == 400


def test_chat_whitespace_only_message_is_400(client):
    # Field(min_length=1)만으로는 공백뿐인 메시지("   ")를 걸러내지 못한다 -
    # API_정의서.xlsx API-10 "빈 메시지 등 요청값 오류" -> 400 VALIDATION_ERROR.
    _signup(client, "chatuser2b@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "   "})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "field,value",
    [
        ("known_gender", "invalid"),
        ("known_disability_status", "invalid"),
        ("known_veteran_status", "invalid"),
        ("known_income_bracket", "invalid"),
        ("known_gender", "unknown"),
        ("known_disability_status", "unknown"),
        ("known_veteran_status", "unknown"),
        ("known_income_bracket", "unknown"),
        ("known_region", "not-a-region"),
        ("known_region", ""),
        ("known_household_types", ["single_parent", "invalid"]),
        ("known_household_types", ["unknown"]),
        ("known_household_types", [None]),
        ("known_household_types", "single_parent"),
        ("known_household_types", None),
        ("known_gender", ""),
        ("known_birth_date", "20000101"),
        ("known_birth_date", "2000-W01-1"),
        ("known_birth_date", "2000-2-29"),
        ("known_birth_date", "2000-02-29\n"),
        ("known_birth_date", "２０００-０２-２９"),
        ("known_birth_date", "2001-02-29"),
        ("known_birth_date", "1800-01-01"),
        ("known_birth_date", ""),
        ("known_birth_date", "2999-01-01"),  # 미래 날짜
        ("known_birth_date", "not-a-date"),
    ],
)
def test_chat_invalid_known_slot_value_is_400(client, monkeypatch, field, value):
    # 2026-09 검토: 이전에는 잘못된 known_* 값을 코어(service.ask())가 조용히
    # 무시했다(400 대신 통과) - API_정의서.xlsx API-10 "요청값 오류 -> 400
    # VALIDATION_ERROR"에 맞춰 요청 스키마 단계에서 막는다.
    _signup(client, "invalid-prefill@example.com")
    ask = Mock(side_effect=AssertionError("invalid prefill reached graph"))
    create = Mock(side_effect=AssertionError("invalid prefill created session"))
    monkeypatch.setattr(chat_adapter, "ask", ask)
    monkeypatch.setattr(chat_adapter.chat_session_store, "create", create)
    r = client.post("/api/v1/chat/messages", json={"message": "안녕", field: value})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"
    ask.assert_not_called()
    create.assert_not_called()
    assert chat_adapter.chat_session_store._sessions == {}


def test_chat_accepts_public_choices_and_optional_prefill(client, monkeypatch):
    _signup(client, "valid-prefill@example.com")
    options = client.get("/api/v1/config/search-options").json()
    ask = Mock(side_effect=lambda message, session_id, **kwargs: _fake_chat_response(session_id))
    monkeypatch.setattr(chat_adapter, "ask", ask)
    cases = [{}, {"known_birth_date": "2000-02-29"}, {"known_household_types": []}]
    for field, option in [
        ("known_region", "sido_options"),
        ("known_gender", "gender_options"),
        ("known_disability_status", "disability_status_options"),
        ("known_veteran_status", "veteran_status_options"),
        ("known_income_bracket", "income_bracket_options"),
    ]:
        cases.append({field: None})
        cases.extend({field: item if isinstance(item, str) else item["code"]}
                     for item in options[option])
    cases.append({"known_birth_date": None})
    cases.append({"known_household_types": [item["code"] for item in options["household_type_options"]]})
    for prefill in cases:
        ask.reset_mock()
        r = client.post("/api/v1/chat/messages", json={"message": "지원 정책", **prefill})
        assert r.status_code == 200, prefill
        ask.assert_called_once()
        for field, value in prefill.items():
            expected = None if field == "known_household_types" and value == [] else value
            assert ask.call_args.kwargs[field] == expected
        assert r.json()["session_id"] in chat_adapter.chat_session_store._sessions


def test_chat_start_and_followup_happy_path(client, monkeypatch):
    _signup(client, "chatuser3@example.com")

    monkeypatch.setattr(
        chat_adapter,
        "ask",
        lambda message, session_id, **kwargs: _fake_chat_response(session_id, "needs_input"),
    )
    monkeypatch.setattr(
        chat_adapter,
        "answer_followup",
        lambda session_id, message: _fake_chat_response(session_id, "answered"),
    )

    r = client.post("/api/v1/chat/messages", json={"message": "유치원비 지원 정책 있나요?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_input"
    session_id = body["session_id"]

    r = client.post(f"/api/v1/chat/sessions/{session_id}/followup", json={"message": "서울특별시"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "answered"
    assert body["policies"][0]["policy_id"] == "P1"


def test_chat_response_includes_parsed_required_documents_items(client, monkeypatch):
    # S07-06/S10-01(2026-09-16 옵션 ② 확정): service.py는 원문 문자열만
    # 주지만, 백엔드 응답에는 파싱된 *_items 배열이 함께 실려야 한다.
    def fake_ask(message, session_id, **kwargs):
        raw = _fake_chat_response(session_id, "answered")
        raw["policies"][0]["detail"] = {
            "required_documents": "주민등록등본, 신분증",
            "required_documents_official": "해당없음",
            "required_documents_self": None,
        }
        return raw

    monkeypatch.setattr(chat_adapter, "ask", fake_ask)
    _signup(client, "chatuser10@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    assert r.status_code == 200
    detail = r.json()["policies"][0]["detail"]
    assert detail["required_documents_items"] == ["주민등록등본", "신분증"]
    assert detail["required_documents_official_items"] is None
    assert detail["required_documents_self_items"] is None
    # 원본 문자열 필드도 그대로 남아 있어야 한다(계약 유지, 대체 아님).
    assert detail["required_documents"] == "주민등록등본, 신분증"


def test_followup_unknown_session_is_404(client):
    _signup(client, "chatuser4@example.com")
    r = client.post("/api/v1/chat/sessions/does-not-exist/followup", json={"message": "hi"})
    assert r.status_code == 404
    assert r.json()["code"] == "SESSION_NOT_FOUND"


def test_other_users_session_is_not_accessible(client, monkeypatch):
    monkeypatch.setattr(
        chat_adapter,
        "ask",
        lambda message, session_id, **kwargs: _fake_chat_response(session_id, "answered"),
    )

    _signup(client, "owner@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    session_id = r.json()["session_id"]

    client.cookies.clear()
    _signup(client, "intruder@example.com")
    r = client.post(f"/api/v1/chat/sessions/{session_id}/followup", json={"message": "hi"})
    assert r.status_code == 404
    assert r.json()["code"] == "SESSION_NOT_FOUND"


def test_vector_store_error_maps_to_503(client, monkeypatch):
    from rag_design.vector_store import ChromaUnavailableError

    def failing_ask(message, session_id, **kwargs):
        raise ChromaUnavailableError("boom")

    monkeypatch.setattr(chat_adapter, "ask", failing_ask)
    _signup(client, "chatuser5@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    assert r.status_code == 503
    assert r.json()["code"] == "VECTOR_STORE_UNAVAILABLE"


@pytest.mark.parametrize("error, expected_status", [
    (RuntimeError("boom"), 500),
    (SystemExit("no vector db"), 503),
    (ChromaUnavailableError("boom"), 503),
])
def test_failed_start_does_not_leave_session(client, monkeypatch, error, expected_status):
    def failing_ask(*args, **kwargs):
        raise error

    monkeypatch.setattr(chat_adapter, "ask", failing_ask)
    _signup(client, "failed-chat@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    assert r.status_code == expected_status
    assert chat_adapter.chat_session_store._sessions == {}


def test_missing_local_vector_db_system_exit_maps_to_503(client, monkeypatch):
    """service.connect_store()가 실제로 던지는 SystemExit 케이스(로컬에 vectorDB 없음)."""

    def failing_ask(message, session_id, **kwargs):
        raise SystemExit("no vector db")

    monkeypatch.setattr(chat_adapter, "ask", failing_ask)
    _signup(client, "chatuser6@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    assert r.status_code == 503
    assert r.json()["code"] == "VECTOR_STORE_UNAVAILABLE"


def test_unexpected_error_maps_to_graph_execution_error(client, monkeypatch):
    def failing_ask(message, session_id, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_adapter, "ask", failing_ask)
    _signup(client, "chatuser7@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    assert r.status_code == 500
    assert r.json()["code"] == "GRAPH_EXECUTION_ERROR"


def test_delete_session_is_idempotent_and_invokes_checkpointer(client, monkeypatch):
    monkeypatch.setattr(
        chat_adapter,
        "ask",
        lambda message, session_id, **kwargs: _fake_chat_response(session_id, "answered"),
    )
    _signup(client, "chatuser8@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    session_id = r.json()["session_id"]

    # 존재하지 않는 세션도 200(멱등, API_정의서.xlsx API-13 권장사항)
    r = client.delete("/api/v1/chat/sessions/never-existed")
    assert r.status_code == 200

    deleted = {}

    class FakeCheckpointer:
        def delete_thread(self, sid):
            deleted["session_id"] = sid

    class FakeGraph:
        checkpointer = FakeCheckpointer()

    monkeypatch.setattr(chat_adapter, "get_graph", lambda: FakeGraph())
    r = client.delete(f"/api/v1/chat/sessions/{session_id}")
    assert r.status_code == 200
    assert deleted["session_id"] == session_id


def test_policy_question_requires_known_policy_in_session(client, monkeypatch):
    monkeypatch.setattr(
        chat_adapter,
        "ask",
        lambda message, session_id, **kwargs: _fake_chat_response(session_id, "answered"),
    )
    _signup(client, "chatuser9@example.com")
    r = client.post("/api/v1/chat/messages", json={"message": "hi"})
    session_id = r.json()["session_id"]

    monkeypatch.setattr(
        followup_adapter,
        "respond_to_policy_question",
        lambda policy, question, *, llm_client, user_profile=None: {
            "kind": "answer",
            "text": "답변입니다.",
            "evidence_quotes": ["근거"],
        },
    )
    monkeypatch.setattr(followup_adapter, "get_llm_client", lambda: None)

    r = client.post(
        f"/api/v1/chat/sessions/{session_id}/policies/UNKNOWN/questions",
        json={"question": "이 정책 신청 방법은?"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "POLICY_NOT_FOUND_IN_SESSION"

    r = client.post(
        f"/api/v1/chat/sessions/{session_id}/policies/P1/questions",
        json={"question": "이 정책 신청 방법은?"},
    )
    assert r.status_code == 200
    assert r.json()["kind"] == "answer"
