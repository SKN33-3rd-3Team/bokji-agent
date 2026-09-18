"""Regression checks for preserved runs and missing operational measurements."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.run_model_evaluation import _create_run_directory
from scripts.pool_evaluation_runs import pool, build_report


class EvaluationArtifactsTests(unittest.TestCase):
    def test_concurrent_runs_preserve_previous_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ThreadPoolExecutor(max_workers=8) as executor:
                runs = list(executor.map(_create_run_directory, [output] * 32))
            self.assertEqual(len({run_id for run_id, _ in runs}), 32)
            for run_id, path in runs:
                (path / "summary.json").write_text(run_id, encoding="utf-8")
            _create_run_directory(output)
            for run_id, path in runs:
                self.assertEqual((path / "summary.json").read_text(encoding="utf-8"), run_id)

    def test_missing_measurements_are_not_success(self):
        for summary in ({}, {"operations": None, "llm_status": None},
                        {"operations": {"sample_count": 10}}):
            result = pool([summary])
            self.assertIsNone(result["success_rate"])
            self.assertEqual(result["op_measured_samples"], 0)
            self.assertIn("N/A", build_report("test", [summary], [Path("old.json")]))

    def test_failed_runs_count_but_unmeasured_runs_do_not(self):
        summaries = [
            {"quality_metrics_valid": False, "operations": {"sample_count": 10, "error_rate": .5},
             "llm_status": {"calls": 10, "failures": 3}},
            {"operations": {"sample_count": 30, "error_rate": 0},
             "llm_status": {"calls": 30, "failures": 0}},
            {"operations": {"sample_count": 100}, "llm_status": {"calls": 100}},
        ]
        result = pool(summaries)
        self.assertEqual(result["success_rate"], .875)
        self.assertEqual(result["op_measured_samples"], 40)
        self.assertEqual(result["llm_calls"], 40)
        self.assertEqual(result["llm_failures"], 3)
        self.assertEqual(result["llm_measured_sets"], 2)


if __name__ == "__main__":
    unittest.main()
