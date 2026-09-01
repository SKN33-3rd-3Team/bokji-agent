import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rag_design.validation_runner import (
    calculate_summary,
    load_questions,
    run_questions,
    write_report,
)


def _answered(session_id, *, answer_status="complete", policy_id=None):
    policies = [{"policy_id": policy_id}] if policy_id else []
    return {
        "status": "answered",
        "session_id": session_id,
        "answer_status": answer_status,
        "final_answer": "answer",
        "policies": policies,
        "final_citations": list(policies),
    }


def _needs_input(session_id, missing_slots):
    return {
        "status": "needs_input",
        "session_id": session_id,
        "question": "추가 정보를 알려주세요",
        "missing_slots": list(missing_slots),
    }


def test_runner_injects_questions_and_writes_reports(tmp_path):
    question_path = tmp_path / "questions.jsonl"
    questions = [
        {
            "question_id": "q1",
            "question": "answerable",
            "expected_policy_ids": ["p1"],
            "should_abstain": False,
        },
        {
            "question_id": "q2",
            "question": "unknown",
            "expected_policy_ids": [],
            "should_abstain": True,
        },
    ]
    question_path.write_text(
        "".join(json.dumps(q) + "\n" for q in questions), encoding="utf-8"
    )
    calls = []

    def fake_ask(question, session_id, *, top_k):
        calls.append((question, session_id, top_k))
        if question == "unknown":
            return {
                "status": "answered",
                "session_id": session_id,
                "answer_status": "abstained",
                "final_answer": "answer",
                "policies": [],
                "final_citations": [],
                "timing": {"phases": {"request_total": 0.02}},
            }
        return {
            "status": "answered",
            "session_id": session_id,
            "answer_status": "partial",
            "final_answer": "answer",
            "policies": [{"policy_id": "p1"}],
            "final_citations": [{"policy_id": "p1"}],
            "timing": {"phases": {"request_total": 0.01}},
        }

    def unexpected_followup(*_args):
        raise AssertionError("follow-up must not run")

    loaded = load_questions(question_path)
    records = run_questions(
        loaded,
        fake_ask,
        unexpected_followup,
        top_k=5,
        workers=1,
        run_nonce="one-shot",
    )
    summary = calculate_summary(records, top_k=5)
    output = tmp_path / "out"
    write_report(output, records, summary, question_path=question_path, top_k=5)

    assert calls == [
        ("answerable", "validation-one-shot-q1", 5),
        ("unknown", "validation-one-shot-q2", 5),
    ]
    assert summary["quality_metrics_valid"] is True
    assert summary["retrieval"]["recall_at_k"] == 1.0
    assert summary["citation"]["precision"] == 1.0
    assert summary["abstention"]["recall"] == 1.0
    assert summary["operations"]["p95_latency_ms"] < 1000
    assert summary["conversation"] == {
        "first_turn_answered_count": 2,
        "first_turn_needs_input_count": 0,
        "first_turn_failed_count": 0,
        "terminal_status_counts": {"answered": 2},
        "answer_status_counts": {"abstained": 1, "partial": 1},
        "first_missing_slot_counts": {},
        "quality_eligible_count": 2,
        "total_turn_count": 2,
        "max_turn_count": 1,
    }
    assert {p.name for p in output.iterdir()} == {
        "results.jsonl",
        "summary.json",
        "metrics.svg",
        "report.md",
    }
    assert json.loads((output / "summary.json").read_text(encoding="utf-8"))[
        "max_turns"
    ] == 4
    assert "Recall@k" in (output / "metrics.svg").read_text(encoding="utf-8")


def test_question_file_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "bad.jsonl"
    row = {
        "question_id": "same",
        "question": "q",
        "expected_policy_ids": [],
        "should_abstain": True,
    }
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8"
    )
    with unittest.TestCase().assertRaisesRegex(ValueError, "duplicate question_id"):
        load_questions(path)


def test_question_file_rejects_invalid_slot_answers(tmp_path):
    path = tmp_path / "bad-followup.jsonl"
    row = {
        "question_id": "q1",
        "question": "q",
        "expected_policy_ids": [],
        "should_abstain": True,
        "slot_answers": {"region": ""},
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with unittest.TestCase().assertRaisesRegex(ValueError, "slot_answers"):
        load_questions(path)


def test_runner_executes_in_parallel_and_preserves_result_order():
    questions = [
        {
            "question_id": f"q{i}",
            "question": f"question-{i}",
            "expected_policy_ids": [],
            "should_abstain": True,
        }
        for i in range(6)
    ]
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_ask(question, session_id, *, top_k):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {
            "status": "answered",
            "session_id": session_id,
            "answer_status": "abstained",
            "final_answer": "answer",
            "policies": [],
            "final_citations": [],
        }

    records = run_questions(questions, fake_ask, lambda *_args: {}, workers=3)

    assert max_active >= 2
    assert [row["question_id"] for row in records] == [f"q{i}" for i in range(6)]


def test_runner_continues_followups_serially_on_same_session_with_two_workers():
    questions = [
        {
            "question_id": f"q{i}",
            "question": f"question-{i}",
            "expected_policy_ids": [f"p{i}"],
            "should_abstain": False,
            "slot_answers": {
                "region": f"fixture-{i}",
                "gender": f"unused-{i}",
            },
        }
        for i in range(2)
    ]
    lock = threading.Lock()
    events = []
    active = 0
    max_active = 0

    def fake_ask(question, session_id, *, top_k):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            events.append((session_id, "ask", question))
        time.sleep(0.02)
        with lock:
            active -= 1
        return {
            "status": "needs_input",
            "session_id": session_id,
            "question": "지역을 알려주세요",
            "missing_slots": ["region"],
        }

    def fake_followup(session_id, user_input):
        with lock:
            events.append((session_id, "followup", user_input))
        index = session_id.rsplit("-q", 1)[1]
        return {
            "status": "answered",
            "session_id": session_id,
            "answer_status": "complete",
            "final_answer": "answer",
            "policies": [{"policy_id": f"p{index}"}],
            "final_citations": [{"policy_id": f"p{index}"}],
        }

    records = run_questions(
        questions,
        fake_ask,
        fake_followup,
        workers=2,
        max_turns=3,
        run_nonce="parallel",
    )

    assert max_active == 2
    for index, row in enumerate(records):
        session_id = f"validation-parallel-q{index}"
        assert [event[1:] for event in events if event[0] == session_id] == [
            ("ask", f"question-{index}"),
            ("followup", f"fixture-{index}"),
        ]
        assert row["first_turn_status"] == "needs_input"
        assert row["terminal_status"] == "answered"
        assert row["last_response_status"] == "answered"
        assert row["first_missing_slots"] == ["region"]
        assert row["requested_missing_slots"] == ["region"]
        assert row["turn_count"] == 2
        assert row["error"] is None

    summary = calculate_summary(records, top_k=5)
    assert summary["conversation"] == {
        "first_turn_answered_count": 0,
        "first_turn_needs_input_count": 2,
        "first_turn_failed_count": 0,
        "terminal_status_counts": {"answered": 2},
        "answer_status_counts": {"complete": 2},
        "first_missing_slot_counts": {"region": 2},
        "quality_eligible_count": 2,
        "total_turn_count": 4,
        "max_turn_count": 2,
    }


def test_runner_marks_missing_slot_fixture_as_explicit_failure():
    question = {
        "question_id": "q1",
        "question": "question",
        "expected_policy_ids": ["p1"],
        "should_abstain": False,
        "slot_answers": {"region": "서울입니다."},
    }

    def fake_ask(_question, session_id, *, top_k):
        return {
            "status": "needs_input",
            "session_id": session_id,
            "question": "지역을 알려주세요",
            "missing_slots": ["region", "gender"],
        }

    records = run_questions([question], fake_ask, lambda *_args: {}, workers=1)

    assert records[0]["error"] == "MissingSlotFixture"
    assert records[0]["terminal_status"] == "failed"
    assert records[0]["answer_status"] is None
    assert records[0]["turn_count"] == 1
    summary = calculate_summary(records, top_k=5)
    assert summary["quality_metrics_valid"] is False
    assert summary["retrieval"]["evaluated_queries"] == 0
    assert summary["conversation"]["quality_eligible_count"] == 0
    assert summary["operations"]["error_rate"] == 1.0


def test_runner_guards_repeated_requests_and_max_turns():
    base = {
        "question_id": "q1",
        "question": "question",
        "expected_policy_ids": [],
        "should_abstain": True,
        "slot_answers": {"region": "서울입니다.", "gender": "여성입니다."},
    }

    def needs_region(_question, session_id, *, top_k):
        return {
            "status": "needs_input",
            "session_id": session_id,
            "question": "지역을 알려주세요",
            "missing_slots": ["region"],
        }

    followup_inputs = []

    def repeats_reordered_slots(session_id, user_input):
        followup_inputs.append(user_input)
        return {
            "status": "needs_input",
            "session_id": session_id,
            "question": "같은 정보를 다시 알려주세요",
            "missing_slots": ["gender", "region"],
        }

    repeated = run_questions(
        [base],
        lambda _question, session_id, *, top_k: _needs_input(
            session_id, ["region", "gender"]
        ),
        repeats_reordered_slots,
        workers=1,
        max_turns=4,
        run_nonce="repeat",
    )[0]
    assert followup_inputs == ["서울입니다. 여성입니다."]
    assert repeated["error"] == "RepeatedNeedsInput"
    assert repeated["turn_count"] == 2

    def asks_gender(session_id, _user_input):
        return {
            "status": "needs_input",
            "session_id": session_id,
            "question": "성별을 알려주세요",
            "missing_slots": ["gender"],
        }

    bounded = run_questions(
        [base], needs_region, asks_gender, workers=1, max_turns=2
    )[0]
    assert bounded["error"] == "MaxTurnsExceeded"
    assert bounded["requested_missing_slots"] == ["region", "gender"]
    assert bounded["turn_count"] == 2


def test_runner_guards_unexpected_status_session_mismatch_and_incomplete_answer():
    question = {
        "question_id": "q1",
        "question": "question",
        "expected_policy_ids": [],
        "should_abstain": True,
    }

    def response(**overrides):
        return {
            "status": "answered",
            "session_id": "validation-guard-q1",
            "answer_status": "abstained",
            "final_answer": "answer",
            "policies": [],
            "final_citations": [],
            **overrides,
        }

    def run_with(payload):
        return run_questions(
            [question],
            lambda *_args, **_kwargs: payload,
            lambda *_args: {},
            workers=1,
            run_nonce="guard",
        )[0]

    assert run_with(response(status="pending"))["error"] == "UnexpectedStatus"
    assert run_with(response(session_id="other"))["error"] == "SessionMismatch"
    incomplete = run_with(response(answer_status=None))
    assert incomplete["error"] == "IncompleteAnsweredResponse"
    assert incomplete["terminal_status"] == "failed"
    assert incomplete["retrieved_policy_ids"] == []

    missing_final_answer = response()
    del missing_final_answer["final_answer"]
    for payload in (response(final_answer=""), missing_final_answer):
        incomplete = run_with(payload)
        assert incomplete["error"] == "IncompleteAnsweredResponse"
        assert calculate_summary([incomplete], top_k=5)["quality_metrics_valid"] is False


def test_dev_questions_have_explicit_per_slot_fixtures():
    path = Path(__file__).resolve().parents[1] / "data/evaluation/dev_questions.jsonl"
    questions = load_questions(path)
    required_slots = {
        "region",
        "birth_date",
        "gender",
        "income_bracket",
        "disability_status",
        "employment_status",
    }

    assert len(questions) == 100
    assert all(set(question["slot_answers"]) == required_slots for question in questions)
    by_id = {question["question_id"]: question for question in questions}
    child = by_id["dev-child-education-001"]["slot_answers"]
    assert child["birth_date"].startswith("대상 아동의 생년월일")
    assert child["employment_status"].startswith("보호자의 취업 상태")
    assert "모름" in by_id["dev-marine-defense-004"]["slot_answers"][
        "disability_status"
    ]
    assert "모름" in by_id["dev-rent-guarantee-limit-016"]["slot_answers"][
        "employment_status"
    ]


def test_runner_rejects_invalid_worker_turn_and_nonce_values():
    with unittest.TestCase().assertRaisesRegex(ValueError, "workers"):
        run_questions([], lambda *args, **kwargs: {}, lambda *_args: {}, workers=0)
    with unittest.TestCase().assertRaisesRegex(ValueError, "max_turns"):
        run_questions([], lambda *args, **kwargs: {}, lambda *_args: {}, max_turns=0)
    with unittest.TestCase().assertRaisesRegex(ValueError, "run_nonce"):
        run_questions(
            [], lambda *args, **kwargs: {}, lambda *_args: {}, run_nonce=""
        )


def test_each_run_gets_an_isolated_nonce_when_started_concurrently():
    question = {
        "question_id": "same",
        "question": "question",
        "expected_policy_ids": [],
        "should_abstain": True,
    }
    sessions = []
    lock = threading.Lock()

    def fake_ask(_question, session_id, *, top_k):
        with lock:
            sessions.append(session_id)
        return _answered(session_id, answer_status="abstained")

    def run_once(_index):
        return run_questions(
            [question], fake_ask, lambda *_args: {}, workers=1
        )[0]["session_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        returned = list(pool.map(run_once, range(2)))

    assert len(set(returned)) == 2
    assert set(returned) == set(sessions)
    deterministic = run_questions(
        [question],
        fake_ask,
        lambda *_args: {},
        workers=1,
        run_nonce="fixed",
    )[0]
    assert deterministic["session_id"] == "validation-fixed-same"


def test_mixed_failure_invalidates_headline_quality_outputs(tmp_path):
    question_path = tmp_path / "mixed.jsonl"
    questions = [
        {
            "question_id": "success",
            "question": "success",
            "expected_policy_ids": ["p1"],
            "should_abstain": False,
        },
        {
            "question_id": "failure",
            "question": "failure",
            "expected_policy_ids": ["p2"],
            "should_abstain": False,
        },
    ]
    question_path.write_text(
        "".join(json.dumps(row) + "\n" for row in questions), encoding="utf-8"
    )

    def fake_ask(question, session_id, *, top_k):
        if question == "failure":
            return _needs_input(session_id, ["region"])
        return _answered(session_id, policy_id="p1")

    records = run_questions(
        questions,
        fake_ask,
        lambda *_args: {},
        workers=1,
        run_nonce="mixed",
    )
    summary = calculate_summary(records, top_k=5)
    output = tmp_path / "out"
    write_report(output, records, summary, question_path=question_path, top_k=5)

    assert records[1]["error"] == "MissingSlotFixture"
    assert summary["quality_metrics_valid"] is False
    assert summary["conversation"]["quality_eligible_count"] == 1
    assert summary["conversation"]["first_turn_answered_count"] == 1
    assert summary["conversation"]["first_turn_needs_input_count"] == 1
    assert summary["conversation"]["first_missing_slot_counts"] == {"region": 1}
    assert summary["retrieval"]["recall_at_k"] == 1.0
    metadata = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    report = (output / "report.md").read_text(encoding="utf-8")
    svg = (output / "metrics.svg").read_text(encoding="utf-8")
    assert metadata["quality_metrics_valid"] is False
    assert "| 검색 | Recall@5 | 게시 불가 |" in report
    assert "비교 가능한 Baseline으로 게시할 수 없습니다" in report
    assert "Quality metrics not publishable" in svg
    assert "Recall@k" not in svg


def test_latency_uses_whole_case_wall_clock_for_all_worker_counts():
    question = {
        "question_id": "q1",
        "question": "question",
        "expected_policy_ids": [],
        "should_abstain": False,
        "slot_answers": {"region": "서울입니다."},
    }

    def fake_ask(_question, session_id, *, top_k):
        time.sleep(0.01)
        return {
            **_needs_input(session_id, ["region"]),
            "timing": {"phases": {"request_total": 123.0}},
        }

    def fake_followup(session_id, _answer):
        time.sleep(0.01)
        return {
            **_answered(session_id),
            "timing": {"phases": {"request_total": 123.0}},
        }

    for workers in (1, 2):
        record = run_questions(
            [question],
            fake_ask,
            fake_followup,
            workers=workers,
            run_nonce=f"latency-{workers}",
        )[0]
        assert 15 <= record["latency_ms"] < 1000


def test_malformed_needs_input_and_call_exceptions_are_explicit_failures():
    question = {
        "question_id": "q1",
        "question": "question",
        "expected_policy_ids": [],
        "should_abstain": False,
        "slot_answers": {"region": "서울입니다."},
    }

    malformed = run_questions(
        [question],
        lambda _question, session_id, *, top_k: _needs_input(
            session_id, ["region", "region"]
        ),
        lambda *_args: {},
        workers=1,
        run_nonce="malformed",
    )[0]
    assert malformed["error"] == "InvalidNeedsInput"

    malformed_followup = run_questions(
        [question],
        lambda _question, session_id, *, top_k: _needs_input(
            session_id, ["region"]
        ),
        lambda session_id, _answer: {
            "status": "needs_input",
            "session_id": session_id,
            "question": "지역을 다시 알려주세요",
            "missing_slots": ["region", "region"],
        },
        workers=1,
        run_nonce="malformed-followup",
    )[0]
    assert malformed_followup["error"] == "InvalidNeedsInput"
    assert malformed_followup["turn_count"] == 2

    def raises_on_ask(*_args, **_kwargs):
        raise RuntimeError("ask failed")

    ask_error = run_questions(
        [question], raises_on_ask, lambda *_args: {}, workers=1
    )[0]
    assert ask_error["error"] == "RuntimeError"
    assert ask_error["turn_count"] == 1

    def raises_on_followup(*_args):
        raise ValueError("follow-up failed")

    followup_error = run_questions(
        [question],
        lambda _question, session_id, *, top_k: _needs_input(
            session_id, ["region"]
        ),
        raises_on_followup,
        workers=1,
        run_nonce="followup-error",
    )[0]
    assert followup_error["error"] == "ValueError"
    assert followup_error["turn_count"] == 2
