"""계산 질문의 구조화 계약과 실제 interrupt/resume 입력 검증."""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.app.services import chat_adapter
from src.rag_chatbot import service
from src.rag_chatbot.graph import builder
from src.rag_chatbot.graph.nodes.benefit_calculator import _select_tier_amount
from src.rag_chatbot.graph.state import GraphState


@pytest.fixture
def calculation(client, monkeypatch):
    fields = ("children_count", "household_size", "marital_status", "pregnancy_status")
    choices = [{"policy_id": "birth", "labels": ["자연분만", "제왕절개"]},
               {"policy_id": "care", "labels": ["방문", "입원"]}]
    calls = []

    def calculate(state):
        calls.append(deepcopy(state))
        slots = state.get("slots") or {}
        missing = [field for field in fields if field not in slots]
        missing_choices = [c for c in choices if c["policy_id"] not in state["calc_choice_answers"]]
        result = {"calc_missing_slots": missing, "calc_missing_choices": missing_choices}
        if not missing and not missing_choices:
            # The real tier selector consumes the numeric answer after actual N10a resume.
            amount, _, _ = _select_tier_amount({"variable": "children_count", "tiers": [
                {"label": "두 자녀", "match_min": 2, "match_max": 2, "amount": 200},
            ]}, slots, state["slot_ask_counts"])
            result.update(answer_status="complete", final_answer=f"amount={amount}")
        return result

    graph = StateGraph(GraphState)
    graph.add_node("calculate", calculate)
    graph.add_node("request_calc_info", builder._await_calc_info_input)
    graph.add_edge(START, "calculate")
    graph.add_conditional_edges("calculate", lambda s: "request_calc_info" if (
        s["calc_missing_slots"] or s["calc_missing_choices"]
    ) else END)
    graph.add_edge("request_calc_info", "calculate")
    compiled = graph.compile(checkpointer=MemorySaver())
    monkeypatch.setattr(service, "_runtime_cache", {
        "graph": compiled, "store": object(), "llm_client": None,
    })
    signup = client.post("/api/v1/auth/signup", json={
        "email": "calc@example.com", "name": "Tester", "password": "Passw0rd!123",
        "password_confirm": "Passw0rd!123", "terms_agreed": True, "privacy_agreed": True,
    })
    assert signup.status_code == 201
    first = client.post("/api/v1/chat/messages", json={"message": "지원금"})
    assert first.status_code == 200 and first.json()["status"] == "needs_input"
    return compiled, calls, first.json()


def _answers(first):
    return {"interrupt_id": first["interrupt_id"],
            "slots": {"children_count": 2, "household_size": 4,
                      "marital_status": "married", "pregnancy_status": "postpartum"},
            "choices": {"birth": "제왕절개", "care": "입원"}}


def test_calculation_response_exposes_categorical_and_numeric_inputs(calculation):
    _, _, first = calculation
    assert first["interrupt_id"]
    assert first["calc_missing_slots"] == [
        "children_count", "household_size", "marital_status", "pregnancy_status",
    ]
    inputs = {item["slot"]: item for item in first["calc_slot_inputs"]}
    assert inputs["children_count"]["input_type"] == "number"
    assert (inputs["children_count"]["minimum"], inputs["children_count"]["maximum"]) == (0, 20)
    assert (inputs["household_size"]["minimum"], inputs["household_size"]["maximum"]) == (1, 30)
    assert inputs["marital_status"]["input_type"] == "select"
    assert {o["value"]: o["label"] for o in inputs["marital_status"]["options"]} == {
        "single": "미혼", "married": "기혼", "divorced": "이혼", "bereaved": "사별",
    }
    assert first["calc_missing_choices"] == [
        {"policy_id": "birth", "labels": ["자연분만", "제왕절개"], "policy_title": "birth"},
        {"policy_id": "care", "labels": ["방문", "입원"], "policy_title": "care"},
    ]
    for field in ("interrupt_id", "calc_missing_slots", "calc_missing_choices", "calc_slot_inputs"):
        assert first["output_json"][field] == first[field]
    assert "age" not in inputs  # age remains birth-date derived


def test_structured_resume_applies_exact_answers_and_amount(client, calculation, monkeypatch):
    graph, calls, first = calculation
    for parser in ("merge_calc_slot_answer", "merge_calc_choice_answer"):
        monkeypatch.setattr(builder, parser, Mock(side_effect=AssertionError("no free-text parsing")))
    sid = first["session_id"]
    response = client.post(f"/api/v1/chat/sessions/{sid}/followup", json={"calc_answers": _answers(first)})
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["final_answer"] == "amount=200"
    state = graph.get_state({"configurable": {"thread_id": sid}}).values
    assert state["slots"] == _answers(first)["slots"]
    assert state["calc_choice_answers"] == {"birth": "제왕절개", "care": "입원"}
    assert state["initial_user_input"] == "지원금"
    assert state["slot_ask_counts"]["children_count"] == 1
    assert len(calls) == 2
    stale = client.post(f"/api/v1/chat/sessions/{sid}/followup", json={"calc_answers": _answers(first)})
    assert (stale.status_code, stale.json()["code"]) == (400, "VALIDATION_ERROR")
    assert len(calls) == 2


def test_invalid_structured_answers_never_change_checkpoint(client, calculation):
    graph, calls, first = calculation
    sid = first["session_id"]
    config = {"configurable": {"thread_id": sid}}
    before = list(graph.checkpointer.list(config))
    invalid = [
        {"slots": {"children_count": value}} for value in (-1, 21, True, 1.5, "2", None)
    ] + [
        {"slots": {"household_size": 0}}, {"slots": {"household_size": 31}},
        {"slots": {"age": 30}}, {"slots": {"gender": "female"}},
        {"slots": {"marital_status": "unknown"}}, {"slots": {"marital_status": "기혼"}},
        {"choices": {"absent-policy": "입원"}}, {"choices": {"birth": "입원"}},
        {"interrupt_id": "stale"}, {"slots": {}, "choices": {}},
        {"unexpected": True},
    ]
    for invalid_fields in invalid:
        answers = {**_answers(first), **invalid_fields}
        response = client.post(f"/api/v1/chat/sessions/{sid}/followup", json={"calc_answers": answers})
        assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_ERROR"), invalid_fields
        assert list(graph.checkpointer.list(config)) == before
    for payload in ({}, {"message": " "}, {"message": "text", "calc_answers": _answers(first)}):
        assert client.post(f"/api/v1/chat/sessions/{sid}/followup", json=payload).status_code == 400
    assert len(calls) == 1


def test_partial_answers_require_current_interrupt_and_preserve_policy_binding(client, calculation):
    graph, _, first = calculation
    sid = first["session_id"]
    url = f"/api/v1/chat/sessions/{sid}/followup"
    response = client.post(url, json={"calc_answers": {
        "interrupt_id": first["interrupt_id"], "choices": {"birth": "자연분만"},
    }})
    assert response.status_code == 200
    second = response.json()
    assert second["status"] == "needs_input" and second["interrupt_id"] != first["interrupt_id"]
    assert [c["policy_id"] for c in second["calc_missing_choices"]] == ["care"]
    before = graph.get_state({"configurable": {"thread_id": sid}})
    for answers in (_answers(first), {**_answers(second), "choices": {"birth": "제왕절개"}}):
        assert client.post(url, json={"calc_answers": answers}).status_code == 400
        assert graph.get_state({"configurable": {"thread_id": sid}}) == before
    response = client.post(url, json={"calc_answers": {**_answers(second), "choices": {"care": "입원"}}})
    assert response.status_code == 200 and response.json()["status"] == "answered"
    assert graph.get_state({"configurable": {"thread_id": sid}}).values["calc_choice_answers"] == {
        "birth": "자연분만", "care": "입원",
    }


def test_legacy_free_text_resumes_calculation(client, calculation):
    _, _, first = calculation
    response = client.post(f"/api/v1/chat/sessions/{first['session_id']}/followup", json={
        "message": "기혼, 출산했어요. 자녀 2명, 4인 가구. 제왕절개, 입원",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "answered" and response.json()["final_answer"] == "amount=200"


def test_structured_input_cannot_resume_profile_question_or_other_owner(client, calculation, monkeypatch):
    graph, calls, first = calculation
    profile = StateGraph(GraphState)
    profile.add_node("prepare", lambda _: {"missing_slots": ["region"]})
    profile.add_node("request_missing_slots", builder._await_missing_slot_input)
    profile.add_edge(START, "prepare")
    profile.add_edge("prepare", "request_missing_slots")
    profile.add_edge("request_missing_slots", END)
    other = profile.compile(checkpointer=MemorySaver())
    builder.run_graph(other, session_id="profile", user_input="")
    monkeypatch.setitem(service._runtime_cache, "graph", other)
    user_id = chat_adapter.chat_session_store._sessions[first["session_id"]].user_id
    chat_adapter.chat_session_store.create("profile", user_id=user_id)
    before = other.get_state({"configurable": {"thread_id": "profile"}})
    answers = _answers(first)
    answers["interrupt_id"] = before.tasks[0].interrupts[0].id
    rejected = client.post("/api/v1/chat/sessions/profile/followup", json={"calc_answers": answers})
    assert (rejected.status_code, rejected.json()["code"]) == (400, "VALIDATION_ERROR")
    assert other.get_state({"configurable": {"thread_id": "profile"}}) == before
    chat_adapter.chat_session_store.create("unowned", user_id=999)
    denied = client.post("/api/v1/chat/sessions/unowned/followup", json={"calc_answers": _answers(first)})
    assert (denied.status_code, denied.json()["code"]) == (404, "SESSION_NOT_FOUND")
    assert len(calls) == 1
