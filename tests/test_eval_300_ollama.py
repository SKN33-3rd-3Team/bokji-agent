"""No network or inference: persistence and independent production track contracts."""
import json
from unittest.mock import Mock

import pytest

from scripts import eval_300_ollama as runner


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    policies = {"p": {"name": "fixed policy"}}
    scenarios = [{"situation_id": f"S{i:03d}", "policy_id": "p"} for i in range(1, 101)]
    cases = []
    for scenario in scenarios:
        for suffix in "ABC":
            cases.append(dict(scenario, id=scenario["situation_id"] + "-" + suffix,
                              policy=policies["p"], category="general", style=suffix,
                              question="질문", facts=[], required_points=[], forbidden_points=[],
                              missing_info=[], reference_quotes=[], reference_answer="답",
                              n1_review={}, expected_kind="answer", suite="expanded_followup"))
    for name, value in zip(runner.FROZEN, (cases, scenarios, policies, {}, {})):
        runner.dump(tmp_path / name, value, exclusive=True)
    runner.prepare(tmp_path)
    monkeypatch.setenv("OLLAMA_BASE_URL", runner.BASE_URL)
    return tmp_path


def test_prepare_exclusive_and_hashes(frozen):
    before = {p.name: p.read_bytes() for p in frozen.iterdir()}
    with pytest.raises(FileExistsError):
        runner.prepare(frozen)
    assert before == {p.name: p.read_bytes() for p in frozen.iterdir()}
    manifest, _, _ = runner.validate_manifest(frozen)
    assert "rubric.json" in manifest["frozen_sha256"]
    assert "scripts/eval_team_ollama.py" not in manifest["source_sha256"]
    assert len(manifest["git_commit"]) == 40
    for source in ("scripts/eval_local_ollama.py",
                   "src/rag_chatbot/graph/slot_schema.py", "src/rag_chatbot/graph/llm_gateway.py",
                   "rag_design/contracts.py", "scripts/eval_300_ollama.py",
                   "scripts/prepare_300_questions.py"):
        assert source in manifest["source_sha256"]
    (frozen / "rubric.json").write_text('{"changed": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen hash"):
        runner.validate_manifest(frozen)


def test_source_inventory_mismatch(frozen, monkeypatch):
    actual = runner.source_hashes()
    monkeypatch.setattr(runner, "source_hashes", lambda: {**actual, "new.py": "x"})
    with pytest.raises(ValueError, match="inventory"):
        runner.validate_manifest(frozen)


def test_invalid_dataset_does_not_create_manifest(tmp_path):
    runner.dump(tmp_path / "cases.json", [])
    runner.dump(tmp_path / "scenarios.json", [])
    runner.dump(tmp_path / "policies.json", {})
    with pytest.raises(ValueError, match="300"):
        runner.prepare(tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_independent_tracks_and_fixed_date(monkeypatch):
    client = runner.CaptureClient(Mock(last_response={}, complete=Mock(return_value='{}')))
    states = []

    def parse(state, llm_client=None):
        states.append(state)
        if llm_client:
            llm_client.complete("n1 secret prompt")
        return {"slots": {"marker": "N1-only"}}

    def detail(policy, question, *, llm_client):
        assert policy == {"name": "policy"}
        llm_client.complete("detail secret prompt")
        return {"kind": "guidance", "text": "확인 필요"}

    monkeypatch.setattr(runner, "parse_slots", parse)
    monkeypatch.setattr(runner, "check_slot_completeness", lambda state: {"missing_slots": []})
    monkeypatch.setattr(runner, "respond_to_policy_question", detail)
    case = dict(id="S001-A", situation_id="S001", category="general", style="A", question="질문",
                policy_id="p", policy={"name": "policy"}, expected_kind="answer", suite="expanded_followup")
    row = runner.evaluate(case, client)
    assert len(row["n1"]["calls"]) == len(row["detail"]["calls"]) == 1
    assert row["n1"]["calls"][0]["prompt_sha256"] != row["detail"]["calls"][0]["prompt_sha256"]
    assert all(s["as_of"].isoformat() == "2026-09-16" for s in states)
    assert "secret prompt" not in json.dumps(row)


def fake_row(case):
    return dict(id=case["id"], started_at="now", n1=dict(slots={}, missing_slots=[], followup=None,
                baseline={}, calls=[], elapsed_s=0), detail=dict(final={}, generation=None,
                quotes_valid=False, outcome_reason="generation_failure", calls=[], elapsed_s=0),
                transport_failure=False)


@pytest.fixture
def backend(frozen, monkeypatch):
    manifest = runner.read(frozen / "manifest.json")
    expected = manifest["expected"]["qwen"]
    def api(path, body=None):
        return {"/api/ps": {"models": []}, "/api/tags": {"models": [dict(name=runner.MODELS["qwen"], digest=expected["digest"])]},
                "/api/show": {"details": expected["details"]}, "/api/version": {"version": expected["version"]},
                "/api/generate": {}}[path]
    network = Mock(side_effect=api)
    monkeypatch.setattr(runner, "api", network)
    monkeypatch.setattr(runner, "OllamaClient", lambda **kw: Mock(last_response={}, complete=Mock(return_value="ready")))
    monkeypatch.setattr(runner, "physical_gpu", lambda: None)
    monkeypatch.setattr(runner, "evaluate", lambda case, client: fake_row(case))
    return network


def test_interrupted_resume_preserves_rows_and_sessions(frozen, backend, monkeypatch):
    count = 0
    def evaluate(case, client):
        nonlocal count
        count += 1
        if count == 3:
            raise KeyboardInterrupt
        return fake_row(case)
    monkeypatch.setattr(runner, "evaluate", evaluate)
    with pytest.raises(KeyboardInterrupt):
        runner.run("qwen", frozen)
    path = frozen / "runs/qwen.jsonl"
    prefix = path.read_bytes()
    session = (frozen / "runs/qwen_session_001.json").read_bytes()
    with pytest.raises(FileExistsError):
        runner.run("qwen", frozen)
    monkeypatch.setattr(runner, "evaluate", lambda case, client: fake_row(case))
    runner.run("qwen", frozen, resume=True)
    assert path.read_bytes().startswith(prefix)
    assert len(path.read_bytes().splitlines()) == 300
    assert (frozen / "runs/qwen_session_001.json").read_bytes() == session
    assert (frozen / "runs/qwen_session_002.json").exists()
    assert all(call.args[1]["model"] == runner.MODELS["qwen"] for call in backend.call_args_list
               if call.args[0] == "/api/generate")


@pytest.mark.parametrize("damage", ["tail", "duplicate", "order", "digest", "schema", "malformed"])
def test_resume_rejects_without_modifying(frozen, backend, damage):
    runner.run("qwen", frozen)
    path = frozen / "runs/qwen.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if damage == "tail":
        raw = b"".join(lines) + b'{"id":'
    elif damage == "duplicate":
        raw = lines[0] + lines[0]
    elif damage == "order":
        raw = lines[1]
    elif damage == "malformed":
        raw = b"not-json\n"
    else:
        row = json.loads(lines[0])
        if damage == "digest":
            row["model_digest"] = "wrong"
        else:
            row["n1"] = {}
        raw = (json.dumps(row) + "\n").encode()
    path.write_bytes(raw)
    backend.reset_mock()
    with pytest.raises(ValueError):
        runner.run("qwen", frozen, resume=True)
    assert path.read_bytes() == raw
    backend.assert_not_called()


@pytest.mark.parametrize("failure_index", [1, 300])
def test_transport_saved_then_halted(frozen, backend, monkeypatch, failure_index):
    count = 0
    def evaluate(case, client):
        nonlocal count
        count += 1
        return dict(fake_row(case), transport_failure=count == failure_index)
    monkeypatch.setattr(runner, "evaluate", evaluate)
    with pytest.raises(RuntimeError, match="saved"):
        runner.run("qwen", frozen)
    prefix = (frozen / "runs/qwen.jsonl").read_bytes()
    assert len(prefix.splitlines()) == failure_index - 1
    failure = frozen / "runs/qwen_session_001_transport_failure.jsonl"
    failed_record = failure.read_bytes()
    assert len(failed_record.splitlines()) == 1
    assert (frozen / "runs/qwen_session_001.json").exists()
    monkeypatch.setattr(runner, "evaluate", lambda case, client: fake_row(case))
    runner.run("qwen", frozen, resume=True)
    completed = (frozen / "runs/qwen.jsonl").read_bytes().splitlines()
    assert len(completed) == 300
    assert (frozen / "runs/qwen.jsonl").read_bytes().startswith(prefix)
    assert json.loads(completed[failure_index - 1])["id"] == json.loads(failed_record)["id"]
    assert failure.read_bytes() == failed_record


def test_second_writer_refused_without_touching_lock(frozen, backend):
    path = frozen / "active_run.lock"
    path.write_text("owned by a running evaluation", encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner.run("qwen", frozen, resume=True)
    assert path.read_text(encoding="utf-8") == "owned by a running evaluation"
    backend.assert_not_called()


def test_other_model_refused_without_unload(frozen, backend):
    backend.side_effect = lambda *args: {"models": [{"name": "someone-else"}]}
    with pytest.raises(ValueError, match="Other model"):
        runner.run("qwen", frozen)
    assert len(backend.call_args_list) == 1
    assert not (frozen / "runs").exists()


def test_format_and_length_are_scoreable():
    assert not runner.transport_failure({"raw": "bad json", "done": True})
    assert not runner.transport_failure({"error": "length", "done_reason": "length"})
    assert runner.transport_failure({"error": "LLMCallError: Ollama connection or timeout error"})
