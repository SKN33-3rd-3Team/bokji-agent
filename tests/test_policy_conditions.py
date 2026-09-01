from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from src.rag_chatbot.graph import policy_conditions
from src.rag_chatbot.graph.policy_conditions import (
    load_support_conditions,
    matches_profile,
)
from src.rag_chatbot.graph.slot_schema import resolve_filter_slots


_CODES = (
    "JA0101",
    "JA0102",
    "JA0201",
    "JA0202",
    "JA0203",
    "JA0204",
    "JA0205",
    "JA0326",
    "JA0327",
    "JA0328",
    "JA0313",
    "JA0314",
    "JA0315",
    "JA0316",
    "JA0317",
    "JA0318",
    "JA0319",
    "JA0320",
    "JA0322",
    "JA1101",
    "JA1102",
    "JA1103",
)


def _row(source_id: str, *active: str) -> dict[str, str | None]:
    return {
        "서비스ID": source_id,
        **{code: "Y" if code in active else None for code in _CODES},
    }


def _wrapper(*rows: dict, match_count: int = 1, current_count: int = 1) -> dict:
    return {
        "matchCount": match_count,
        "currentCount": current_count,
        "data": list(rows),
    }


def _plan(**slots):
    return resolve_filter_slots(slots)


class SupportConditionsLoaderTests(unittest.TestCase):
    def _load(self, payload: object):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            policy_conditions, "_LOAD_WARNING_EMITTED", False
        ), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            path = Path(temp_dir) / "gov24_support_conditions.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return load_support_conditions(path)

    def test_loads_exact_single_row_wrapper(self) -> None:
        loaded = self._load([_wrapper(_row("service-1", "JA0102", "JA0202"))])

        self.assertEqual(loaded["service-1"]["JA0102"], "Y")
        self.assertIsNone(loaded["service-1"]["JA0101"])

    def test_loads_canonical_flat_rows_alongside_legacy_wrappers(self) -> None:
        loaded = self._load(
            [
                _row("flat", "JA0101"),
                _wrapper(_row("wrapped", "JA0102")),
            ]
        )

        self.assertEqual(set(loaded), {"flat", "wrapped"})
        self.assertEqual(loaded["flat"]["JA0101"], "Y")
        self.assertEqual(loaded["wrapped"]["JA0102"], "Y")

    def test_missing_or_invalid_file_fails_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            policy_conditions, "_LOAD_WARNING_EMITTED", False
        ), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            missing = Path(temp_dir) / "missing.json"
            invalid = Path(temp_dir) / "invalid.json"
            invalid.write_text("not-json", encoding="utf-8")

            self.assertEqual(load_support_conditions(missing), {})
            self.assertEqual(load_support_conditions(invalid), {})

    def test_unavailable_sidecar_warns_once_without_path_or_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "secret-name.json"
            corrupt = Path(temp_dir) / "corrupt.json"
            non_list = Path(temp_dir) / "non-list.json"
            corrupt.write_text("raw-secret-payload", encoding="utf-8")
            non_list.write_text("{}", encoding="utf-8")

            with patch.object(
                policy_conditions, "_LOAD_WARNING_EMITTED", False
            ), warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                self.assertEqual(load_support_conditions(missing), {})
                self.assertEqual(load_support_conditions(corrupt), {})
                self.assertEqual(load_support_conditions(non_list), {})

        self.assertEqual(len(captured), 1)
        message = str(captured[0].message)
        self.assertIn("프로필 후처리를 생략", message)
        self.assertNotIn("secret-name", message)
        self.assertNotIn("raw-secret-payload", message)

    def test_malformed_rows_warn_once_with_counts_only(self) -> None:
        degraded = _row("degraded", "JA0101")
        degraded["JA0201"] = "N"
        duplicate = _row("duplicate", "JA0101")
        payload = [
            _wrapper(degraded),
            _wrapper(duplicate),
            _wrapper(duplicate),
            _wrapper(_row("dropped"), match_count=0),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "conditions.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with patch.object(
                policy_conditions, "_LOAD_WARNING_EMITTED", False
            ), warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                loaded = load_support_conditions(path)

        self.assertEqual(set(loaded), {"degraded"})
        self.assertEqual(len(captured), 1)
        message = str(captured[0].message)
        self.assertIn("accepted=1", message)
        self.assertIn("dropped=1", message)
        self.assertIn("duplicates=1", message)
        self.assertIn("degraded_categories=1", message)
        self.assertNotIn("degraded\"", message)
        self.assertNotIn("duplicate\"", message)

    def test_invalid_value_or_missing_code_disables_only_that_category(self) -> None:
        invalid_value = _row("bad-value")
        invalid_value["JA0101"] = "N"
        missing_code = _row("missing-code")
        missing_code.pop("JA0328")
        missing_employment_code = _row("missing-employment-code")
        missing_employment_code.pop("JA0313")

        loaded = self._load(
            [
                _wrapper(invalid_value),
                _wrapper(missing_code),
                _wrapper(missing_employment_code),
                _wrapper(_row("valid", "JA0328")),
            ]
        )

        self.assertEqual(
            set(loaded),
            {"bad-value", "missing-code", "missing-employment-code", "valid"},
        )
        self.assertNotIn("JA0101", loaded["bad-value"])
        self.assertIn("JA0201", loaded["bad-value"])
        self.assertNotIn("JA0328", loaded["missing-code"])
        self.assertIn("JA0101", loaded["missing-code"])
        self.assertNotIn("JA0326", loaded["missing-employment-code"])
        self.assertIn("JA0101", loaded["missing-employment-code"])

    def test_zero_row_multi_row_and_duplicate_service_fail_open(self) -> None:
        loaded = self._load(
            [
                _wrapper(match_count=0, current_count=0),
                _wrapper(
                    _row("multi"),
                    _row("other"),
                    match_count=2,
                    current_count=2,
                ),
                _wrapper(_row("duplicate", "JA0101")),
                _wrapper(_row("duplicate", "JA0101")),
                _wrapper(_row("multi", "JA0102")),
                _wrapper(_row("valid", "JA0102")),
            ]
        )

        self.assertEqual(set(loaded), {"valid"})

    def test_noncanonical_service_id_is_not_trimmed_or_guessed(self) -> None:
        loaded = self._load(
            [
                _wrapper(_row(" service-1", "JA0101")),
                _wrapper({**_row("service-2"), "서비스ID": 2}),
            ]
        )

        self.assertEqual(loaded, {})

    def test_missing_or_mismatched_counts_drop_that_service(self) -> None:
        row = _row("service-1", "JA0101")
        loaded = self._load(
            [
                {"data": [row]},
                _wrapper(row, match_count=2),
                _wrapper(row, current_count=0),
            ]
        )

        self.assertEqual(loaded, {})


class ProfileMatcherTests(unittest.TestCase):
    def test_gender_codes_are_or_within_category(self) -> None:
        both = _row("service", "JA0101", "JA0102")

        self.assertTrue(matches_profile(both, _plan(gender="male")))
        self.assertTrue(matches_profile(both, _plan(gender="female")))
        self.assertFalse(
            matches_profile(_row("service", "JA0101"), _plan(gender="female"))
        )

    def test_income_uses_interval_overlap(self) -> None:
        expected = {
            "under_30": {"JA0201"},
            "pct_30_50": {"JA0201"},
            "pct_50_75": {"JA0201", "JA0202"},
            "pct_75_100": {"JA0202", "JA0203"},
            "pct_100_150": {"JA0203", "JA0204"},
            "over_150": {"JA0204", "JA0205"},
        }

        for slot, matching_codes in expected.items():
            for code in ("JA0201", "JA0202", "JA0203", "JA0204", "JA0205"):
                with self.subTest(slot=slot, code=code):
                    self.assertEqual(
                        matches_profile(
                            _row("service", code),
                            _plan(income_bracket=slot),
                        ),
                        code in matching_codes,
                    )

    def test_exact_employment_subset_only(self) -> None:
        employed = _row("service", "JA0326")
        job_seeking = _row("service", "JA0327")

        self.assertTrue(matches_profile(employed, _plan(employment_status="employed")))
        self.assertFalse(
            matches_profile(employed, _plan(employment_status="job_seeking"))
        )
        self.assertTrue(
            matches_profile(job_seeking, _plan(employment_status="job_seeking"))
        )
        for ambiguous in ("self_employed", "student", "not_working"):
            with self.subTest(ambiguous=ambiguous):
                self.assertTrue(
                    matches_profile(employed, _plan(employment_status=ambiguous))
                )

    def test_unmapped_active_employment_code_prevents_exclusion(self) -> None:
        mixed = _row("service", "JA0326", "JA0313")

        self.assertTrue(
            matches_profile(mixed, _plan(employment_status="job_seeking"))
        )

    def test_disability_is_positive_registered_only(self) -> None:
        registered_policy = _row("service", "JA0328")

        self.assertTrue(
            matches_profile(
                registered_policy, _plan(disability_status="registered")
            )
        )
        self.assertTrue(
            matches_profile(
                registered_policy, _plan(disability_status="not_registered")
            )
        )

    def test_categories_are_combined_with_and(self) -> None:
        values = _row("service", "JA0102", "JA0202", "JA0326", "JA0328")
        matching = _plan(
            gender="female",
            income_bracket="pct_50_75",
            employment_status="employed",
            disability_status="registered",
        )
        wrong_gender = _plan(
            gender="male",
            income_bracket="pct_50_75",
            employment_status="employed",
            disability_status="registered",
        )

        self.assertTrue(matches_profile(values, matching))
        self.assertFalse(matches_profile(values, wrong_gender))

    def test_invalid_category_fails_open_without_disabling_other_categories(self) -> None:
        invalid_gender = _row("service", "JA0101", "JA0205")
        invalid_gender.pop("JA0102")
        invalid_income = _row("service", "JA0102", "JA0205")
        invalid_income["JA0201"] = "N"
        missing_employment = _row("service", "JA0101", "JA0326")
        missing_employment.pop("JA0313")

        self.assertFalse(
            matches_profile(
                invalid_gender,
                _plan(gender="female", income_bracket="under_30"),
            )
        )
        self.assertTrue(
            matches_profile(
                invalid_income,
                _plan(gender="female", income_bracket="under_30"),
            )
        )
        self.assertFalse(
            matches_profile(
                missing_employment,
                _plan(gender="female", employment_status="job_seeking"),
            )
        )

    def test_missing_or_unknown_category_data_fails_open(self) -> None:
        values = _row("service", "JA0101")
        values.pop("JA0102")
        no_unmapped_codes = _row("service", "JA0326")
        for code in (
            "JA0313",
            "JA0314",
            "JA0315",
            "JA0316",
            "JA0317",
            "JA0318",
            "JA0319",
            "JA0320",
            "JA0322",
            "JA1101",
            "JA1102",
            "JA1103",
        ):
            no_unmapped_codes.pop(code)

        self.assertTrue(matches_profile(values, _plan(gender="female")))
        self.assertTrue(matches_profile(_row("service"), _plan(gender="female")))
        self.assertTrue(
            matches_profile(
                no_unmapped_codes, _plan(employment_status="job_seeking")
            )
        )


if __name__ == "__main__":
    unittest.main()
