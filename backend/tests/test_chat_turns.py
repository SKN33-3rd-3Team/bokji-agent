"""완료된 상담의 새 턴과 실제 체크포인트/HTTP 경계를 검증한다."""

from copy import deepcopy
from datetime import date
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.app.core.errors import ApiError
from backend.app.services import chat_adapter
from src.rag_chatbot import service
from src.rag_chatbot.graph import builder
from src.rag_chatbot.graph.nodes.slot_parser import parse_slots
from src.rag_chatbot.graph.slot_schema import resolve_filter_slots
from src.rag_chatbot.graph.state import GraphState


@pytest.fixture
def turn_graph(monkeypatch):
    seen = []

    def step(state):
        seen.append(deepcopy(state))
        if state["user_input"] == "fail":
            raise RuntimeError("turn failed")
        parsed = parse_slots(state)
        answer = state["user_input"]
        if answer == "collect":
            answer = interrupt("추가 정보를 알려주세요")
        return {
            **parsed, "answer_status": "complete", "final_answer": answer,
            "calc_choice_answers": {"old-policy": "old-choice"},
            "slot_ask_counts": {"region": 2}, "doc_retry_count": 1,
            "law_retry_count": 1, "node_trace": ["old-node"],
            "calc_missing_slots": ["children_count"],
            "calc_missing_choices": [{"policy_id": "old-policy", "labels": ["old-choice"]}],
        }

    graph = StateGraph(GraphState)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)
    compiled = graph.compile(checkpointer=MemorySaver())
    monkeypatch.setattr(service, "_runtime_cache", {
        "graph": compiled, "store": Mock(search=Mock(return_value=[])), "llm_client": None,
    })
    return compiled, seen


@pytest.mark.parametrize("age_subject", ["self", None])
def test_completed_turn_resets_query_results_and_keeps_profile(turn_graph, monkeypatch, age_subject):
    graph, seen = turn_graph
    builder.run_graph(graph, user_input="first", session_id="same", top_k=3,
                      as_of=date(2026, 12, 31), slots={
                          "birth_date": "2000-01-01", "gender": "female",
                          "region_scope": "regional", "region_names": ["서울특별시"],
                          "interests": ["old-interest"], "age_subject": "self",
                      })
    config = {"configurable": {"thread_id": "same"}}
    if age_subject is None:
        confirmed = dict(graph.get_state(config).values["slots"])
        confirmed.pop("age_subject")
        graph.update_state(config, {"slots": confirmed})
    # Simulate all downstream fields left by a prior completed turn.
    graph.update_state(config, {
        "assembled_result": {"policies": {"old-policy": {}}},
        "claim_plan": [{"policy_id": "old-policy"}],
        "benefit_amounts": [{"policy_id": "old-policy", "amount": 100}],
        "duplicate_verdicts": [{"policy_id": "old-policy", "status": "조건부"}],
        "eligibility_verdicts": [{"policy_id": "old-policy", "verdict": "충족"}],
        "draft_answer": "old-answer", "final_citations": [{"policy_id": "old-policy"}],
        "missing_document_claim_ids": ["old-claim"], "missing_law_claim_ids": ["old-claim"],
    })
    monkeypatch.setattr(builder, "korea_today", lambda: date(2027, 1, 1))
    result = service.answer_followup("same", "second")
    assert result["final_answer"] == "second"
    assert result["session_id"] == "same"
    assert result["policies"] == []
    fresh = seen[-1]
    assert fresh["query_id"] == "same" and fresh["policy_top_k"] == 3
    assert fresh["as_of"] == date(2027, 1, 1)
    assert fresh["initial_user_input"] == "second"
    assert fresh["slot_ask_counts"] == {} and fresh["calc_choice_answers"] == {}
    for field in ("calc_missing_slots", "calc_missing_choices", "claim_plan", "benefit_amounts",
                  "duplicate_verdicts", "eligibility_verdicts", "node_trace", "final_citations",
                  "missing_document_claim_ids", "missing_law_claim_ids"):
        assert fresh[field] == [], field
    assert fresh["assembled_result"] == {} and fresh["draft_answer"] == ""
    assert fresh["doc_retry_count"] == fresh["law_retry_count"] == 0
    slots = graph.get_state(config).values["slots"]
    assert slots["birth_date"] == "2000-01-01" and slots["age"] == 27
    assert slots["gender"] == "female" and slots["region_names"] == ["서울특별시"]
    assert not slots.get("interests") and slots["age_subject"] == "self"
    assert resolve_filter_slots(slots, reference_date=date(2027, 1, 1))["hard"]["birth_date"]["age"] == 27


@pytest.mark.parametrize("message, subject", [
    ("우리 아이는 2015-01-01생입니다", "child"),
    ("어머니는 1960-01-01생입니다", "household_member"),
    ("가구원은 2000-01-01생입니다", "household_member"),
    ("저는 우리 아이 2015-01-01생 지원을 문의합니다", "unknown"),
])
def test_completed_turn_discards_non_self_birth_date(turn_graph, monkeypatch, message, subject):
    graph, seen = turn_graph
    reference_date = date(2026, 9, 19)
    monkeypatch.setattr(builder, "korea_today", lambda: reference_date)
    first = builder.run_graph(graph, user_input=message, session_id="same")
    config = {"configurable": {"thread_id": "same"}}
    assert first["answer_status"] == "complete" and not graph.get_state(config).next
    assert first["slots"]["age_subject"] == subject
    assert first["slots"]["birth_date"]
    assert "birth_date" not in resolve_filter_slots(first["slots"], reference_date=reference_date)["hard"]

    result = builder.resume_graph(graph, session_id="same", user_input="본인 청년 지원을 알려주세요")
    assert result["answer_status"] == "complete" and len(seen) == 2
    slots = graph.get_state(config).values["slots"]
    assert slots["age_subject"] == "self"
    assert "birth_date" not in resolve_filter_slots(slots, reference_date=reference_date)["hard"]
    assert not slots.get("birth_date")
    assert slots["age"] is slots["age_year_based"] is slots["age_ref_date"] is None


@pytest.mark.parametrize("replace_with_child_dob", [False, True])
def test_completed_turn_preserves_only_unchanged_profile_birth_date(client, monkeypatch, replace_with_child_dob):
    reference_date = date(2026, 9, 19)
    store = Mock(search=Mock(return_value=[]))
    graph = builder.build_graph(store)
    monkeypatch.setattr(service, "_runtime_cache", {
        "graph": graph, "store": store, "llm_client": None,
    })
    monkeypatch.setattr(service, "korea_today", lambda: reference_date)
    monkeypatch.setattr(builder, "korea_today", lambda: reference_date)
    assert client.post("/api/v1/auth/signup", json={
        "email": "profile-turns@example.com", "name": "Tester", "password": "Passw0rd!123",
        "password_confirm": "Passw0rd!123", "terms_agreed": True, "privacy_agreed": True,
        "birth_date": "1990-01-01", "gender": "female", "region": "서울특별시",
        "income_bracket": "under_30", "disability_status": "not_registered",
    }).status_code == 201
    defaults = client.get("/api/v1/users/me/chat-defaults")
    assert defaults.status_code == 200
    known = defaults.json()
    known.pop("employment_status_available")
    assert known["known_birth_date"] == "1990-01-01"
    message = "우리 아이 지원 제도를 알려주세요. 재직 중입니다."
    if replace_with_child_dob:
        message += " 우리 아이는 2015-01-01생입니다."
    result = service.ask(message, "profile-turn", **known)
    config = {"configurable": {"thread_id": "profile-turn"}}
    if replace_with_child_dob:
        conflicted = graph.get_state(config).values
        assert result["status"] == "needs_input"
        assert conflicted["slot_conflicts"]["birth_date"] == {
            "profile": "1990-01-01", "chat": "2015-01-01",
        }
        assert "birth_date" not in conflicted["slots"]["profile_sourced"]
        result = service.answer_followup("profile-turn", "2015-01-01생입니다")
    before = graph.get_state(config)
    assert result["status"] == "answered" and not before.next
    assert before.values["slots"]["age_subject"] == "child"
    assert before.values["slots"]["birth_date"] == ("2015-01-01" if replace_with_child_dob else "1990-01-01")
    assert ("birth_date" in before.values["slots"]["profile_sourced"]) is not replace_with_child_dob

    result = service.answer_followup("profile-turn", "본인 청년 지원을 알려주세요")
    after = graph.get_state(config).values
    slots = after["slots"]
    assert slots["age_subject"] == "self"
    age_filter = resolve_filter_slots(slots, reference_date=reference_date)["hard"].get("birth_date")
    if replace_with_child_dob:
        assert result["status"] == "needs_input" and result["missing_slots"] == ["birth_date"]
        assert not slots.get("birth_date") and age_filter is None
    else:
        assert result["status"] == "answered" and after["missing_slots"] == []
        assert slots["birth_date"] == "1990-01-01" and age_filter["age"] == 36


def test_interrupted_turn_resumes_before_starting_new_turn(turn_graph):
    graph, seen = turn_graph
    first = builder.run_graph(graph, user_input="collect", session_id="same")
    assert first["__interrupt__"]
    response = service.answer_followup("same", "region answer")
    assert response["final_answer"] == "region answer"
    assert seen[-1]["user_input"] == "collect"  # actual Command resumes the old task
    assert service.answer_followup("same", "new question")["final_answer"] == "new question"
    assert len(seen) == 3


def test_missing_or_failed_checkpoint_cannot_become_a_new_turn(turn_graph):
    graph, seen = turn_graph
    with pytest.raises(ValueError) as missing:
        service.answer_followup("absent", "question")
    assert not isinstance(missing.value, builder.FailedCheckpointError)
    with pytest.raises(RuntimeError):
        builder.run_graph(graph, user_input="fail", session_id="failed")
    with pytest.raises(builder.FailedCheckpointError) as failed:
        service.answer_followup("failed", "do not restart")
    assert isinstance(failed.value, ValueError)  # existing shared callers remain compatible
    assert len(seen) == 1


def test_unrelated_value_error_keeps_generic_guidance():
    def fail():
        raise ValueError("The previous graph execution failed; start a new session")

    with pytest.raises(ApiError) as caught:
        chat_adapter._run(fail)
    assert (caught.value.status_code, caught.value.code) == (500, "GRAPH_EXECUTION_ERROR")
    assert caught.value.message == "일시적인 오류가 발생했습니다. 다시 시도해주세요."


def test_http_completed_turn_ownership_and_failure_mapping(client, turn_graph, monkeypatch):
    graph, seen = turn_graph
    signup = client.post("/api/v1/auth/signup", json={
        "email": "turns@example.com", "name": "Tester", "password": "Passw0rd!123",
        "password_confirm": "Passw0rd!123", "terms_agreed": True, "privacy_agreed": True,
    })
    assert signup.status_code == 201
    first = client.post("/api/v1/chat/messages", json={"message": "first"})
    sid = first.json()["session_id"]
    response = client.post(f"/api/v1/chat/sessions/{sid}/followup", json={"message": "second"})
    assert response.status_code == 200 and response.json()["final_answer"] == "second"
    assert len(seen) == 2
    failed = client.post(f"/api/v1/chat/sessions/{sid}/followup", json={"message": "fail"})
    assert (failed.status_code, failed.json()["code"]) == (500, "GRAPH_EXECUTION_ERROR")
    assert failed.json()["message"] == "일시적인 오류가 발생했습니다. 다시 시도해주세요."
    config = {"configurable": {"thread_id": sid}}
    assert any(task.error for task in graph.get_state(config).tasks)
    checkpoints = list(graph.checkpointer.list(config))
    owner = chat_adapter.chat_session_store._sessions[sid]
    blocked = client.post(f"/api/v1/chat/sessions/{sid}/followup", json={"message": "retry"})
    assert (blocked.status_code, blocked.json()["code"]) == (500, "GRAPH_EXECUTION_ERROR")
    assert blocked.json()["message"] == "이 상담을 계속할 수 없습니다. 새 상담을 시작해 주세요."
    assert len(seen) == 3
    assert list(graph.checkpointer.list(config)) == checkpoints
    assert chat_adapter.chat_session_store._sessions[sid] is owner
    chat_adapter.chat_session_store.create("other", user_id=999)
    denied = client.post("/api/v1/chat/sessions/other/followup", json={"message": "question"})
    assert (denied.status_code, denied.json()["code"]) == (404, "SESSION_NOT_FOUND")
    assert len(seen) == 3

    other = TestClient(client.app)
    assert other.post("/api/v1/auth/signup", json={
        "email": "other-turns@example.com", "name": "Other", "password": "Passw0rd!123",
        "password_confirm": "Passw0rd!123", "terms_agreed": True, "privacy_agreed": True,
    }).status_code == 201
    read_state = Mock(wraps=graph.get_state)
    monkeypatch.setattr(graph, "get_state", read_state)
    for requester, expected in ((other, (404, "SESSION_NOT_FOUND")),
                                (TestClient(client.app), (401, "UNAUTHORIZED"))):
        denied = requester.post(f"/api/v1/chat/sessions/{sid}/followup", json={"message": "retry"})
        assert (denied.status_code, denied.json()["code"]) == expected
        assert "이 상담을 계속할 수 없습니다" not in denied.json()["message"]
    read_state.assert_not_called()

    # A new API-10 request recovers without requiring DELETE or reusing the failed ID.
    fresh = client.post("/api/v1/chat/messages", json={"message": "new session"})
    assert fresh.status_code == 200 and fresh.json()["final_answer"] == "new session"
    assert fresh.json()["session_id"] != sid and len(seen) == 4
    assert chat_adapter.chat_session_store._sessions[sid] is owner
    assert list(graph.checkpointer.list(config)) == checkpoints
