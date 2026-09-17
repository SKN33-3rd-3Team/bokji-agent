"""Protect frozen inputs and distinguish model abstention from service fallback."""
import json
from collections import Counter

import pytest

from scripts import eval_local_ollama as evaluation
from rag_chatbot.llm import LLMCallError


def test_prepare_freezes_all_cases_and_refuses_overwrite(tmp_path):
    evaluation.prepare(tmp_path)
    cases = json.loads((tmp_path / "cases.json").read_text(encoding="utf-8"))
    assert len({c["id"] for c in cases}) == 45
    assert Counter(c["suite"] for c in cases) == {
        "legacy_followup": 29, "extra_followup": 6, "n13": 10,
    }
    legacy = [c for c in cases if c["suite"] == "legacy_followup"]
    assert Counter(c["expected_kind"] for c in legacy) == {"answer": 18, "guidance": 11}
    before = (tmp_path / "cases.json").read_bytes()
    with pytest.raises(FileExistsError):
        evaluation.prepare(tmp_path)
    assert (tmp_path / "cases.json").read_bytes() == before


def test_guidance_from_failure_is_recorded_separately(monkeypatch):
    class BrokenClient:
        last_response = {}

        def complete(self, *args, **kwargs):
            raise LLMCallError("offline")

    monkeypatch.setattr(evaluation, "api", lambda *args: {"models": []})
    monkeypatch.setattr("rag_chatbot.light_followup.time.sleep", lambda _: None)
    row = evaluation.evaluate({
        "id": "failure", "suite": "legacy_followup", "question": "수리 비용은?",
        "policy_id": "p", "policy": {"title": "교육", "detail": {"support_details": "교육을 제공합니다."}},
        "expected_kind": "guidance",
    }, evaluation.CaptureClient(BrokenClient()))
    assert row["legacy_pass"] is True  # Existing metric alone hides the error.
    assert row["outcome_reason"] == "generation_failure"
    assert len(row["calls"]) == 2
    assert all("error" in c for c in row["calls"])
    assert evaluation.summary([row])["call_errors"] == 2


def test_modified_cases_stop_before_model_contact(tmp_path, monkeypatch):
    evaluation.prepare(tmp_path)
    (tmp_path / "cases.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(evaluation, "api", lambda *args: pytest.fail("must not contact model"))
    with pytest.raises(SystemExit, match="Frozen cases changed"):
        evaluation.run("qwen", tmp_path)


@pytest.mark.parametrize("verification,reason", [(None, "verifier_failure"), (False, "self_verifier_rejected")])
def test_metrics_follow_valid_retry_and_distinguish_verifier_failure(verification, reason):
    case = {"suite": "legacy_followup", "policy_id": "p", "expected_kind": "answer",
            "policy": {"title": "교육", "detail": {"support_details": "무상교육"}}}
    valid = {"answerable": True, "answer": "무상교육입니다.", "evidence_quotes": ["무상교육"]}
    calls = [{"parsed": {"answerable": "true"}}, {"parsed": valid},
             {"parsed": {} if verification is None else {"consistent": verification}}]
    metric = evaluation.followup_metrics(case, {"kind": "guidance"}, calls)
    assert metric["generation"] == valid
    assert metric["quotes_valid"] is True
    assert metric["outcome_reason"] == reason
