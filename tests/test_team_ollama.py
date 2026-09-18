"""팀 평가 동결·제품 호출·분리 검증. HTTP/GPU를 사용하지 않는다."""
import json
from unittest.mock import patch

import pytest

from scripts import eval_team_ollama as team
from rag_chatbot.graph.nodes import slot_parser


class MockLLM:
    def __init__(self, **kwargs):
        self.options = team.OPTIONS
        self.last_response = {"done": True, "eval_count": 1}

    def complete(self, prompt, *, system=None, max_tokens=None):
        return '{}'


@pytest.fixture
def frozen(tmp_path):
    with patch.object(team, "api", side_effect=AssertionError("prepare must be offline")):
        team.prepare(tmp_path)
    return tmp_path


def test_prepare_preserves_source_and_evidence_boundaries(frozen):
    cases = json.loads((frozen / "cases.json").read_text(encoding="utf-8"))
    original = json.loads((team.ROOT / team.SOURCE).read_text(encoding="utf-8-sig"))
    assert json.loads((frozen / "source.json").read_text(encoding="utf-8")) == original
    assert len(cases) == 8
    for case, source_row in zip(cases, original["질문세트"][1:9]):
        assert case["question"] == source_row[2]
        assert case["team_expected_reference_only"] == source_row[4]
        assert case["actual_search_result"] is False
        entry = next(iter(case["state"]["assembled_result"]["policies"].values()))
        assert entry["eligibility"]["verdict"] == "미확인"
        assert case["question"] not in entry["eligibility"]["reasons"]
        assert bool(case["evidence"]) == (case["id"] in (1, 3))
        assert case["policy_id_is_synthetic"] == (case["id"] not in (1, 3))
    assert cases[1]["baseline_n1"]["followup"]
    assert cases[1]["n13_style_scored"] is False
    with pytest.raises(FileExistsError):
        team.prepare(frozen)


def test_product_n1_and_fixed_n13_are_separate(frozen):
    cases = json.loads((frozen / "cases.json").read_text(encoding="utf-8"))
    client = team.CaptureClient(MockLLM())
    with patch.object(slot_parser, "extract_slots", wraps=slot_parser.extract_slots) as extract, \
         patch.object(team, "generate_answer", wraps=team.generate_answer) as answer:
        row = team.evaluate(cases[0], client)
    assert extract.call_args.args[0] == cases[0]["question"]
    assert extract.call_args.kwargs["reference_date"] == team.AS_OF
    assert extract.call_args.kwargs["llm_client"] is client
    assert answer.call_args.args[0] == cases[0]["state"]
    assert len(row["n1"]["calls"]) == len(row["n13"]["calls"]) == 1
    assert row["n1"]["calls"][0]["raw"] == '{}'
    assert row["n13"]["final"] == row["n13"]["baseline"]
    assert "prompt" not in row["n1"]["calls"][0]
    assert row["n1"]["ms"] >= 0


def test_question_two_uses_real_rule_followup_only(frozen):
    case = json.loads((frozen / "cases.json").read_text(encoding="utf-8"))[1]
    with patch.object(team, "generate_answer", side_effect=AssertionError("no N13 for #2")):
        row = team.evaluate(case, team.CaptureClient(MockLLM()))
    assert row["n13"]["calls"] == []
    assert row["n13"]["style_scored"] is False
    assert row["n13"]["final"] == team.generate_followup_question(0, row["n1"]["missing_slots"])


def test_run_mock_writes_eight_rows_and_model_provenance(frozen):
    model = team.MODELS["bllossom"]
    responses = {"/api/tags": {"models": [{"name": model, "digest": "mock-digest"}]},
                 "/api/show": {"details": {"quantization_level": "Q4_K_M"}, "capabilities": ["completion"]},
                 "/api/version": {"version": "mock-version"},
                 "/api/ps": {"models": [{"name": model}]}, "/api/generate": {"done": True}}
    with patch.object(team, "api", side_effect=lambda path, body=None: responses[path]) as http, \
         patch.object(team, "BASE_URL", "http://127.0.0.1:11435"), \
         patch("rag_chatbot.llm.ollama.OllamaClient", side_effect=MockLLM) as constructor:
        team.run("bllossom", frozen)
    assert constructor.call_args.kwargs["base_url"] == "http://127.0.0.1:11435"
    rows = [json.loads(line) for line in (frozen / "bllossom.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 8
    assert sum(len(row["n1"]["calls"]) for row in rows) == 8
    assert sum(len(row["n13"]["calls"]) for row in rows) == 7
    metadata = json.loads((frozen / "bllossom_metadata.json").read_text(encoding="utf-8"))
    assert metadata["digest"] == "mock-digest"
    assert metadata["base_url"] == "http://127.0.0.1:11435"
    assert metadata["options"] == team.OPTIONS
    assert metadata["warmup"]["scored"] is False
    assert metadata["warmup"]["max_tokens"] == 32
    assert len(metadata["warmup"]["calls"]) == 1
    assert metadata["running_models_after_warmup"] == [{"name": model}]
    assert "running_models_after_final_unload" in metadata
    assert all("running_models" in row for row in rows)
    unloads = [call for call in http.call_args_list if call.args[0] == "/api/generate"]
    assert len(unloads) == 2
    assert all(call.args[1] == {"model": model, "keep_alive": 0} for call in unloads)
    assert metadata["manifest_sha256"] == team.digest((frozen / "manifest.json").read_bytes())


def test_changed_freeze_rejected_before_http(frozen):
    (frozen / "cases.json").write_text("[]", encoding="utf-8")
    with patch.object(team, "api", side_effect=AssertionError("no HTTP")):
        with pytest.raises(ValueError, match="동결 자료"):
            team.run("qwen", frozen)


def test_rubric_frozen_with_separate_weighted_dimensions(frozen):
    rubric_path = frozen / "rubric.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    manifest = json.loads((frozen / "manifest.json").read_text(encoding="utf-8"))
    assert set(rubric["scale"]) == {"1", "2", "3", "4", "5"}
    assert rubric["question_weights"] == {str(n): 2 if n == 6 else 1 for n in range(1, 9)}
    assert rubric["excluded_question_ids"] == {"slots": [], "expression": [2]}
    cases = json.loads((frozen / "cases.json").read_text(encoding="utf-8"))
    assert sum(c["review_weight"] for c in cases) == 9
    assert sum(c["review_weight"] for c in cases if c["n13_style_scored"]) == 8
    review = team.evaluate(cases[5], team.CaptureClient(MockLLM()))["review"]
    assert review["weight"] == 2
    assert review["slots_score"] is None and review["expression_score"] is None
    assert manifest["frozen_sha256"]["rubric.json"] == team.digest(rubric_path.read_bytes())
    rubric_path.write_text("{}", encoding="utf-8")
    with patch.object(team, "api", side_effect=AssertionError("no HTTP")):
        with pytest.raises(ValueError, match="동결 자료"):
            team.run("qwen", frozen)


def test_warmup_failure_still_unloads_and_preserves_calls(frozen):
    from rag_chatbot.llm import LLMCallError
    model = team.MODELS["qwen"]
    responses = {"/api/tags": {"models": [{"name": model, "digest": "mock"}]},
                 "/api/show": {"details": {"quantization_level": "Q4_K_M"}},
                 "/api/version": {}, "/api/ps": {"models": []}, "/api/generate": {}}
    with patch.object(team, "BASE_URL", "http://127.0.0.1:11435"), \
         patch.object(team, "api", side_effect=lambda path, body=None: responses[path]) as http, \
         patch("rag_chatbot.llm.ollama.OllamaClient", MockLLM), \
         patch.object(MockLLM, "complete", side_effect=LLMCallError("warmup failed")) as complete:
        with pytest.raises(LLMCallError):
            team.run("qwen", frozen)
    assert complete.call_args.kwargs["max_tokens"] == 32
    http.assert_any_call("/api/generate", {"model": model, "keep_alive": 0})
    metadata = json.loads((frozen / "qwen_metadata.json").read_text(encoding="utf-8"))
    assert "error" in metadata["warmup"]["calls"][0]
    assert not (frozen / "qwen.jsonl").exists()


def test_unload_rejects_non_experiment_endpoint(frozen):
    with patch.object(team, "BASE_URL", "http://127.0.0.1:11434"), \
         patch.object(team, "api", side_effect=AssertionError("no HTTP")):
        with pytest.raises(ValueError, match="11435"):
            team.run("qwen", frozen)
