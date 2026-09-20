"""한국 날짜/만 120세 경계를 HTTP·인증·그래프에서 함께 확인한다."""

from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from backend.app.services import chat_adapter
from backend.tests.test_auth_api import _signup
from src.rag_chatbot import service
from src.rag_chatbot.auth import service as auth
from src.rag_chatbot.graph import builder, slot_schema
from src.rag_chatbot.graph.nodes import slot_parser
from src.rag_chatbot.graph.nodes.slot_completeness_gate import check_slot_completeness


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime(2025, 12, 31, 15, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(auth, "datetime", Clock)
    monkeypatch.setattr(slot_schema, "datetime", Clock, raising=False)
    return Clock


@pytest.mark.parametrize("birth, valid", [
    ("1905-01-02", True),  # 121번째 생일 전날: 여전히 만 120세
    ("1905-01-01", False),
    ("1906-01-01", True),
    ("2026-01-01", True),  # UTC에서는 아직 전날
    ("2026-01-02", False),
    ("20000101", False), ("2000-W01-1", False),
    ("2001-02-29", False), ("2000-02-29", True),
])
def test_auth_and_graph_use_same_strict_completed_age_contract(clock, birth, valid):
    if valid:
        assert auth._clean_birth_date(birth) == birth
    else:
        with pytest.raises(auth.AuthError):
            auth._clean_birth_date(birth)
    parsed = slot_schema.parse_birth_date(birth)
    assert (parsed is not None) is valid
    if parsed is not None:
        assert parsed.isoformat() == birth


@pytest.mark.parametrize("instant, valid", [
    (datetime(2025, 12, 31, 14, 59, 59, tzinfo=timezone.utc), False),
    (datetime(2025, 12, 31, 15, tzinfo=timezone.utc), True),
])
def test_korea_midnight_changes_future_date_boundary(clock, instant, valid):
    clock.current = instant
    assert (slot_schema.parse_birth_date("2026-01-01") is not None) is valid
    if valid:
        assert auth._clean_birth_date("2026-01-01") == "2026-01-01"
    else:
        with pytest.raises(auth.AuthError):
            auth._clean_birth_date("2026-01-01")


@pytest.mark.parametrize("day, valid", [(28, True), (1, False)])
def test_leap_birthday_turns_121_on_march_first(clock, day, valid):
    clock.current = datetime(2025, 2 if day == 28 else 3, day, tzinfo=timezone.utc)
    assert (slot_schema.parse_birth_date("1904-02-29") is not None) is valid
    if valid:
        assert auth._clean_birth_date("1904-02-29") == "1904-02-29"
    else:
        with pytest.raises(auth.AuthError):
            auth._clean_birth_date("1904-02-29")


@pytest.mark.parametrize("birth, valid", [
    ("1905-01-02", True), ("1905-01-01", False),
    ("2026-01-01", True), ("2026-01-02", False),
])
def test_signup_patch_chat_share_date_boundary(client, clock, monkeypatch, birth, valid):
    start = Mock(return_value={"session_id": "test", "status": "answered"})
    monkeypatch.setattr(chat_adapter, "start_chat", start)
    response = _signup(client, birth_date=birth)
    assert response.status_code == (201 if valid else 400)
    if not valid:
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert _signup(client).status_code == 201
    patched = client.patch("/api/v1/users/me", json={"birth_date": birth})
    assert patched.status_code == (200 if valid else 400)
    chatted = client.post("/api/v1/chat/messages", json={"message": "test", "known_birth_date": birth})
    assert chatted.status_code == (200 if valid else 400)
    if valid:
        assert patched.json()["birth_date"] == birth
        assert client.patch("/api/v1/users/me", json={}).json()["birth_date"] == birth
        for clear in (None, ""):
            assert client.patch("/api/v1/users/me", json={"birth_date": clear}).json()["birth_date"] == ""
    else:
        assert patched.json()["code"] == chatted.json()["code"] == "VALIDATION_ERROR"
        start.assert_not_called()


def test_graph_default_as_of_and_explicit_override(clock):
    graph = Mock()
    graph.invoke.side_effect = lambda state, **kwargs: state
    state = builder.run_graph(graph, user_input="test", session_id="test")
    assert state["as_of"] == date(2026, 1, 1)
    explicit = builder.run_graph(graph, user_input="test", session_id="test", as_of=date(2025, 12, 31))
    assert explicit["as_of"] == date(2025, 12, 31)


def test_prefill_parser_gate_filter_share_one_date_snapshot(clock, monkeypatch):
    graph = Mock()

    def invoke(state, **kwargs):
        # 프로필 검증과 노드 실행 사이에 자정이 넘어도 같은 as_of를 쓴다.
        clock.current += timedelta(days=1)
        state.update(slot_parser.parse_slots(state))
        return state

    graph.invoke.side_effect = invoke
    monkeypatch.setattr(service, "get_graph", lambda: graph)
    monkeypatch.setattr(service, "get_store", lambda: object())
    monkeypatch.setattr(service, "_llm_request_scope", nullcontext)
    monkeypatch.setattr(service, "_to_chat_response", lambda state, **kwargs: state)
    state = service.ask("test", "date-snapshot", known_birth_date="1905-01-02")
    assert state["as_of"] == date(2026, 1, 1)
    assert state["slots"]["birth_date"] == "1905-01-02"
    assert state["slots"]["age"] == 120
    assert state["slots"]["age_ref_date"] == "2026-01-01"
    assert "birth_date" not in check_slot_completeness(state)["missing_slots"]
    plan = slot_schema.resolve_filter_slots({**state["slots"], "age_subject": "self"}, reference_date=state["as_of"])
    assert plan["hard"]["birth_date"]["age"] == 120


def test_standalone_parser_uses_korea_date(clock):
    state = slot_parser.parse_slots({"user_input": "test", "slots": {"birth_date": "1905-01-02"}})
    assert state["slots"]["age"] == 120
    assert state["slots"]["age_ref_date"] == "2026-01-01"
