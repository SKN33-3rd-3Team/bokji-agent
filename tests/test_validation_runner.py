import json
import unittest

from rag_design.validation_runner import calculate_summary, load_questions, run_questions, write_report


def test_runner_injects_questions_and_writes_reports(tmp_path):
    question_path = tmp_path / "questions.jsonl"
    questions = [
        {"question_id": "q1", "question": "answerable", "expected_policy_ids": ["p1"], "should_abstain": False},
        {"question_id": "q2", "question": "unknown", "expected_policy_ids": [], "should_abstain": True},
    ]
    question_path.write_text("".join(json.dumps(q) + "\n" for q in questions), encoding="utf-8")
    calls = []

    def fake_ask(question, session_id, *, top_k):
        calls.append((question, session_id, top_k))
        if question == "unknown":
            return {"answer_status": "abstained", "policies": [], "final_citations": [], "timing": {"phases": {"request_total": 0.02}}}
        return {"answer_status": "complete", "policies": [{"policy_id": "p1"}], "final_citations": [{"policy_id": "p1"}], "timing": {"phases": {"request_total": 0.01}}}

    loaded = load_questions(question_path)
    records = run_questions(loaded, fake_ask, top_k=5)
    summary = calculate_summary(records, top_k=5)
    output = tmp_path / "out"
    write_report(output, records, summary, question_path=question_path, top_k=5)

    assert calls == [("answerable", "validation-q1", 5), ("unknown", "validation-q2", 5)]
    assert summary["retrieval"]["recall_at_k"] == 1.0
    assert summary["citation"]["precision"] == 1.0
    assert summary["abstention"]["recall"] == 1.0
    assert summary["operations"]["p95_latency_ms"] == 20.0
    assert {p.name for p in output.iterdir()} == {"results.jsonl", "summary.json", "metrics.svg", "report.md"}


def test_question_file_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "bad.jsonl"
    row = {"question_id": "same", "question": "q", "expected_policy_ids": [], "should_abstain": True}
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with unittest.TestCase().assertRaisesRegex(ValueError, "duplicate question_id"):
        load_questions(path)
