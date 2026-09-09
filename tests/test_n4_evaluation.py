"""Evaluator contracts only; benchmark always uses the real archived N4/provider."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "evaluate_n4", Path(__file__).resolve().parents[1] / "scripts/evaluate_n4.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def case(identifier):
    return {"question_id": identifier, "expected_policy_ids": ["gold"],
            "strata": {"age_subject": "child"}, "quality_flags": ["not_eligibility"],
            "label_validity": "single_target"}


def row(identifier, policies, latency=10, error=None):
    return {"question_id": identifier, "policy_ids": policies, "latency_ms": latency, "error": error}


class N4EvaluationTests(unittest.TestCase):
    def test_single_target_metrics_match_existing_helper_including_errors(self):
        from rag_design.evaluation import RetrievalCase, retrieval_metrics

        cases = [case(str(i)) for i in range(8)]
        rows = [row(str(rank - 1), [f"other{i}" for i in range(rank - 1)] + ["gold"])
                for rank in range(1, 6)]
        rows += [row("5", ["other"] * 5 + ["gold"]), row("6", ["gold"], 90, "error")]
        rows = evaluation.complete_rows(rows, cases, "timeout")
        reference = retrieval_metrics([
            RetrievalCase(frozenset(c["expected_policy_ids"]),
                          tuple([] if r["error"] else r["policy_ids"]))
            for c, r in zip(cases, rows)], k=5)
        observed = evaluation.summarize(rows, cases)
        self.assertEqual(reference.evaluated_queries, 8)
        self.assertEqual(observed["observations"], reference.evaluated_queries)
        self.assertEqual(observed["recall_at_5"], reference.recall_at_k)
        self.assertAlmostEqual(observed["mrr_at_5"], reference.mrr_at_k)
        self.assertEqual(observed["errors"], 2)
        for rank in range(1, 6):
            with self.subTest(rank=rank):
                self.assertEqual(evaluation.score(["gold"], rows[rank - 1]["policy_ids"]),
                                 (1, 1 / rank))

    def test_primary_latency_is_success_only_error_elapsed_retained_timeout_censored(self):
        cases = [case("one"), case("two"), case("three")]
        rows = evaluation.complete_rows(
            [row("one", ["gold"], 10), row("two", [], 90, "RuntimeError")],
            cases, "not_completed_timeout")
        result = evaluation.summarize(rows, cases)
        self.assertEqual(result["recall_at_5"], 1 / 3)
        self.assertEqual(result["successful"], 1)
        self.assertEqual(result["errors"], 2)
        # Frozen protocol: failed elapsed is raw evidence, not a successful N4 latency.
        self.assertEqual(result["latency_samples"], 1)
        self.assertEqual(result["p50_ms"], 10)
        self.assertEqual(result["p95_ms"], 10)
        self.assertEqual(rows[1]["latency_ms"], 90)
        self.assertIsNone(rows[2]["latency_ms"])
        self.assertTrue(rows[2]["censored"])
        self.assertIn("diagnostic if incomplete", result["latency_scope"])

    def test_sidecar_copy_is_hash_verified_isolated_and_create_only(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text('[{"service":"original"}]')
            expected = evaluation.sha256(source)
            destination = Path(directory) / "child.json"
            copied = evaluation.copy_sidecar(source, destination, expected)
            self.assertEqual(copied, destination.resolve())
            self.assertEqual(evaluation.sha256(copied), expected)
            copied.write_text("[]")
            self.assertEqual(evaluation.sha256(source), expected)
            with self.assertRaises(FileExistsError):
                evaluation.copy_sidecar(source, destination, expected)
            with self.assertRaisesRegex(ValueError, "must be isolated"):
                evaluation.copy_sidecar(source, source, expected)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                evaluation.copy_sidecar(source, Path(directory) / "bad.json", "wrong")

    def test_orchestrator_excludes_warmups_and_keeps_95_unique_285_repeated(self):
        cases = [case(f"q{i:03}") for i in range(95)]
        for item in cases:
            item["n4_input"] = {"slots": {"age_subject": "child"}}
        frozen_cases = json.dumps(cases, sort_keys=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "freeze.json"
            manifest.write_text("{}")
            freeze = {"warmup_ids": [c["question_id"] for c in cases[:5]],
                      "index_path": str(root / "source-index"), "index_files_sha256": {},
                      "model_path": str(root / "cache/repo/snapshots/commit"),
                      "model_files_sha256": {}}
            invoked = []

            def fake_child(command, **kwargs):
                # Only child process execution is substituted; real parent orchestration,
                # partial parsing, completion accounting and aggregation remain exercised.
                job_path = Path(command[-1])
                job = evaluation.read_json(job_path)
                invoked.append(job["case_ids"])
                records = [dict(row(q, ["gold"], 100000), phase="warmup")
                           for q in freeze["warmup_ids"]]
                records += [dict(row(q, ["gold"], i + 1), phase="measured")
                            for i, q in enumerate(job["case_ids"])]
                (job_path.parent / "partial.jsonl").write_text(
                    "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            args = SimpleNamespace(output_dir=root / "run", manifest=manifest,
                                   smoke=False, timeout_seconds=1)
            with patch.object(evaluation, "load_inputs", return_value=(freeze, cases)), \
                 patch.object(evaluation, "verify_files"), \
                 patch.object(evaluation, "archive_revision", return_value={"files_sha256": {}}), \
                 patch.object(evaluation.importlib.metadata, "version", return_value="test"), \
                 patch.object(evaluation.subprocess, "run", side_effect=fake_child):
                self.assertEqual(evaluation.run(args), 0)
            summary = evaluation.read_json(root / "run/summary.json")
            self.assertTrue(summary["benchmark_accepted"])
            self.assertEqual(len(invoked), 6)
            self.assertTrue(all(ids == [c["question_id"] for c in cases] for ids in invoked))
            for result in summary["revisions"].values():
                self.assertEqual(result["observations"], 285)
                self.assertEqual(result["distinct_questions"], 95)
                self.assertEqual(result["latency_samples"], 285)
                self.assertEqual(result["p50_ms"], 48)
                self.assertAlmostEqual(result["p95_ms"], 90.8)
                self.assertEqual(result["recall_at_5"], 1)
                self.assertEqual([r["observations"] for r in result["per_round"]], [95] * 3)
            self.assertEqual(json.dumps(cases, sort_keys=True), frozen_cases)

    def test_archive_import_content_is_checked_even_with_correct_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "policy_search.py"
            source.write_text("original")
            hashes = evaluation.file_hashes(root)
            modules = {"rag_chatbot.graph.nodes.policy_search": SimpleNamespace(__file__=str(source))}
            with patch.dict(evaluation.sys.modules, modules, clear=True):
                self.assertIn("rag_chatbot.graph.nodes.policy_search",
                              evaluation.verify_imports(root, hashes))
                source.write_text("modified")
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    evaluation.verify_imports(root, hashes)

    def test_type7_interpolation_not_nearest_rank(self):
        self.assertEqual(evaluation.quantile([0, 10, 20, 30], .5), 15)
        self.assertAlmostEqual(evaluation.quantile([0, 10, 20, 30], .95), 28.5)
        self.assertEqual(evaluation.quantile([7], .95), 7)
        self.assertIsNone(evaluation.quantile([], .5))
        for invalid in (float("nan"), float("inf"), -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                evaluation.quantile([invalid], .95)

    def test_top5_policy_recall_and_reciprocal_rank(self):
        self.assertEqual(evaluation.score(["gold"], ["other", "gold"]), (1, .5))
        self.assertEqual(evaluation.score(["gold"], ["other"] * 5 + ["gold"]), (0, 0))
        self.assertEqual(evaluation.score(["gold", "second"], ["gold", "gold"]), (.5, 1))

    def test_missing_and_failed_cases_stay_in_denominator(self):
        cases = [case("one"), case("two"), case("three")]
        rows = evaluation.complete_rows([row("one", ["gold"]),
                    row("two", ["gold"], 90, "RuntimeError")], cases, "timeout")
        self.assertTrue(rows[2]["censored"])
        self.assertIsNone(rows[2]["latency_ms"])
        result = evaluation.summarize(rows, cases)
        self.assertEqual(result["recall_at_5"], 1 / 3)
        self.assertEqual(result["errors"], 2)
        self.assertEqual(result["latency_samples"], 1)
        self.assertEqual(result["p95_ms"], 10)

    def test_duplicate_or_unknown_results_rejected(self):
        for partial in ([row("one", []), row("one", [])], [row("unknown", [])]):
            with self.subTest(partial=partial), self.assertRaises(ValueError):
                evaluation.complete_rows(partial, [case("one")], "missing")
        with self.assertRaisesRegex(ValueError, "order changed"):
            evaluation.complete_rows([row("two", []), row("one", [])],
                                     [case("one"), case("two")], "missing")

    def test_interrupted_jsonl_keeps_completed_rows_and_signals_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.jsonl"
            path.write_bytes(b'{"question_id":"one"}\n{"question_id":')
            rows, corrupt = evaluation.read_partial(path)
            self.assertTrue(corrupt)
            self.assertEqual(rows, [{"question_id": "one"}])

    def test_round_mean_and_pooled_latency_preserve_repeat_denominators(self):
        rounds = []
        for number, revision in enumerate("ABBAAB", 1):
            rounds.append({"round": number, "revision": revision,
                           "rows": [row("one", ["gold"] if number == 1 else [], number * 10)]})
        result = evaluation.aggregate(rounds, [case("one")])["revisions"]["A"]
        self.assertEqual(result["observations"], 3)
        self.assertEqual(result["distinct_questions"], 1)
        self.assertEqual(result["recall_at_5"], 1 / 3)
        self.assertEqual(result["recall_at_5_range"], [0, 1])
        self.assertEqual(result["p50_ms"], 40)
        self.assertEqual(result["p95_ms"], 49)
        group = result["groups_overlapping"]["quality_flag=not_eligibility"]
        self.assertEqual(group["observations"], 3)
        self.assertEqual(group["distinct_questions"], 1)

    def test_file_inventory_detects_content_changes_and_additions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one").write_text("frozen")
            frozen = evaluation.file_hashes(root)
            evaluation.verify_files(root, frozen)
            (root / "one").write_text("changed")
            with self.assertRaises(ValueError):
                evaluation.verify_files(root, frozen)
            (root / "one").write_text("frozen")
            (root / "extra").write_text("extra")
            with self.assertRaises(ValueError):
                evaluation.verify_files(root, frozen)

    def test_outputs_are_create_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            evaluation.write_json(path, {"first": True})
            with self.assertRaises(FileExistsError):
                evaluation.write_json(path, {})
            self.assertEqual(evaluation.read_json(path), {"first": True})

    def test_import_from_shared_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            module = SimpleNamespace(__file__=__file__)
            with patch.dict(evaluation.sys.modules, {"rag_design.outside": module}):
                with self.assertRaisesRegex(ValueError, "escaped archive"):
                    evaluation.verify_imports(Path(directory).resolve(), {})

    def test_loaded_commit_must_match_even_when_cache_ref_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "models--intfloat--multilingual-e5-base"
            snapshot = repo / "snapshots" / evaluation.MODEL_COMMIT
            snapshot.mkdir(parents=True)
            (repo / "refs").mkdir()
            (repo / "refs/main").write_text(evaluation.MODEL_COMMIT)
            transformer = SimpleNamespace(auto_model=SimpleNamespace(
                config=SimpleNamespace(_commit_hash="wrong")))
            provider = SimpleNamespace(_load=lambda: [transformer])
            with self.assertRaisesRegex(ValueError, "Loaded model commit unproven"):
                evaluation.model_proof(provider, {"model_path": str(snapshot)})


if __name__ == "__main__":
    unittest.main()
