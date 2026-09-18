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


def test_completed_turn_resets_query_results_and_keeps_profile(turn_graph, monkeypatch):
    graph, seen = turn_graph
    builder.run_graph(graph, user_input="first", session_id="same", top_k=3,
                      as_of=date(2026, 12, 31), slots={
                          "birth_date": "2000-01-01", "gender": "female",
                          "region_scope": "regional", "region_names": ["서울특별시"],
                          "interests": ["old-interest"], "age_subject": "child",
                      })
    config = {"configurable": {"thread_id": "same"}}
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
