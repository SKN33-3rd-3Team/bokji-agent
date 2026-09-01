from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from rag_chatbot.collectors.gov_24 import gov24, to_document  # noqa: E402
from rag_chatbot.collectors.gov_24.region_utils import (  # noqa: E402
    extract_region,
    load_sigungu_code_table,
)


class RegionContractTests(unittest.TestCase):
    def test_national_regional_nested_and_unknown_contract(self) -> None:
        self.assertEqual(
            extract_region("교육부")["region_names"],
            ["전국"],
        )

        regional = extract_region("경기도 수원시 장안구청")
        self.assertEqual(regional["region_scope"], "regional")
        self.assertEqual(
            regional["region_names"],
            ["경기도", "경기도 수원시", "경기도 수원시 장안구"],
        )

        unknown = extract_region("한국주택금융공사")
        self.assertEqual(unknown["region_scope"], "unknown")
        self.assertEqual(unknown["region_names"], [])

    def test_current_codes_are_used_and_legacy_names_fail_closed(self) -> None:
        for organization, code in (
            ("강원특별자치도 춘천시청", "51"),
            ("전북특별자치도 전주시청", "52"),
            ("전남광주통합특별시 순천시청", "12"),
        ):
            with self.subTest(organization=organization):
                self.assertEqual(extract_region(organization)["sido_code"], code)

        for legacy_name in ("강원도 춘천시청", "전라북도 전주시청", "전라남도 순천시청"):
            with self.subTest(legacy_name=legacy_name):
                legacy = extract_region(legacy_name)
                self.assertEqual(legacy["region_scope"], "unknown")
                self.assertIsNone(legacy["sido_code"])

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "codes.csv"
            csv_path.write_text(
                "시도명,시군구명,법정동코드\n"
                "강원특별자치도,춘천시,51110\n"
                "강원도,춘천시,42110\n"
                "전북특별자치도,전주시,52110\n",
                encoding="utf-8",
            )
            table = load_sigungu_code_table(csv_path)
        self.assertEqual(
            table,
            {
                ("강원특별자치도", "춘천시"): "51110",
                ("전북특별자치도", "전주시"): "52110",
            },
        )


class UrlAndSecretSafetyTests(unittest.TestCase):
    @staticmethod
    def _item(source_url: str) -> dict:
        return {
            "서비스ID": "service-1",
            "상세조회URL": source_url,
            "서비스명": "테스트 지원",
            "서비스분야": "생활안정",
            "지원대상": "지원 대상입니다.",
            "지원내용": "지원합니다.",
            "소관기관명": "교육부",
        }

    def test_document_url_is_canonical_and_has_no_credential(self) -> None:
        credential = "query-credential"
        item = self._item(
            "http://www.gov.kr/portal/service-1?"
            f"serviceKey={credential}&tracking=discard#fragment"
        )
        warnings: list[str] = []
        document = to_document.convert_one(
            item,
            "2026-08-27T00:00:00+09:00",
            warnings,
            set(),
            set(),
            {},
        )
        self.assertIsNotNone(document)
        self.assertEqual(document["source_url"], "https://www.gov.kr/portal/service-1")
        self.assertEqual(
            document["metadata"]["public_detail_url"],
            "https://www.gov.kr/portal/service-1",
        )
        rendered = json.dumps({"document": document, "warnings": warnings})
        self.assertNotIn(credential, rendered)
        self.assertNotIn("tracking", rendered)

    def test_secret_or_unofficial_document_url_is_rejected_without_leak(self) -> None:
        secret = "actual/key=="
        encoded_secret = "actual%2Fkey%3D%3D"
        for source_url in (
            f"https://www.gov.kr/portal/service-1?unexpected={encoded_secret}",
            f"https://attacker.example/path?serviceKey={secret}",
        ):
            with self.subTest(source_url=source_url):
                warnings: list[str] = []
                with patch.object(to_document, "GOV24_SERVICE_KEY", secret):
                    document = to_document.convert_one(
                        self._item(source_url),
                        "2026-08-27T00:00:00+09:00",
                        warnings,
                        set(),
                        set(),
                        {},
                    )
                self.assertIsNone(document)
                rendered = json.dumps(warnings, ensure_ascii=False)
                self.assertNotIn(secret, rendered)
                self.assertNotIn(encoded_secret, rendered)

    def test_api_request_errors_are_redacted_in_log_return_and_raise(self) -> None:
        secret = "api/key=="
        encoded_secret = "api%2Fkey%3D%3D"
        error = requests.exceptions.HTTPError(
            f"401: ?serviceKey={encoded_secret}&token={secret}"
        )
        with (
            patch.object(gov24, "GOV24_SERVICE_KEY", secret),
            patch.object(gov24, "MAX_RETRIES", 1),
            patch.object(gov24.requests, "get", side_effect=error),
            patch.object(gov24, "log") as log_mock,
        ):
            _, _, returned_error = gov24._fetch_one(
                "detail", gov24.DETAIL_URL, "service-1"
            )
            with self.assertRaises(gov24.Gov24RequestError) as raised:
                gov24.fetch_list_page(1)

        rendered = " ".join(
            (str(log_mock.call_args_list), returned_error or "", str(raised.exception))
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn(encoded_secret, rendered)
        self.assertIn("[REDACTED]", rendered)


class SupportConditionsRequestTests(unittest.TestCase):
    @staticmethod
    def _payload(service_id: str = "service-1") -> dict:
        return {
            "matchCount": 1,
            "currentCount": 1,
            "data": [{"서비스ID": service_id}],
        }

    def test_support_conditions_uses_official_exact_condition_parameter(self) -> None:
        response = Mock()
        response.json.return_value = self._payload()

        with (
            patch.object(gov24, "GOV24_SERVICE_KEY", "test-key"),
            patch.object(gov24.requests, "get", return_value=response) as get_mock,
        ):
            service_id, payload, error = gov24._fetch_one(
                "conditions", gov24.CONDITIONS_URL, "service-1"
            )

        self.assertEqual(service_id, "service-1")
        self.assertEqual(payload, response.json.return_value)
        self.assertIsNone(error)
        params = get_mock.call_args.kwargs["params"]
        self.assertEqual(params["cond[서비스ID::EQ]"], "service-1")
        self.assertNotIn("servId", params)

    def test_service_detail_keeps_serv_id_parameter(self) -> None:
        response = Mock()
        response.json.return_value = {"data": [{"서비스ID": "service-1"}]}

        with (
            patch.object(gov24, "GOV24_SERVICE_KEY", "test-key"),
            patch.object(gov24.requests, "get", return_value=response) as get_mock,
        ):
            gov24._fetch_one("detail", gov24.DETAIL_URL, "service-1")

        params = get_mock.call_args.kwargs["params"]
        self.assertEqual(params["servId"], "service-1")
        self.assertNotIn("cond[서비스ID::EQ]", params)

    def test_invalid_conditions_payload_is_never_successful(self) -> None:
        valid_row = {"서비스ID": "service-1"}
        invalid_payloads = (
            [],
            {"data": [valid_row]},
            {"matchCount": True, "currentCount": 1, "data": [valid_row]},
            {"matchCount": 0, "currentCount": 1, "data": [valid_row]},
            {"matchCount": 1, "currentCount": 0, "data": [valid_row]},
            {"matchCount": 1, "currentCount": 1, "data": []},
            {
                "matchCount": 2,
                "currentCount": 2,
                "data": [valid_row, valid_row],
            },
            {"matchCount": 1, "currentCount": 1, "data": [{}]},
            self._payload("wrong-service"),
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = Mock()
                response.json.return_value = payload
                with (
                    patch.object(gov24, "GOV24_SERVICE_KEY", "test-key"),
                    patch.object(gov24, "MAX_RETRIES", 1),
                    patch.object(gov24.requests, "get", return_value=response),
                    patch.object(gov24, "log"),
                ):
                    service_id, data, error = gov24._fetch_one(
                        "conditions", gov24.CONDITIONS_URL, "service-1"
                    )

                self.assertEqual(service_id, "service-1")
                self.assertIsNone(data)
                self.assertIn("exact one-row response contract mismatch", error)

    def test_invalid_conditions_response_is_checkpointed_only_as_failed(self) -> None:
        response = Mock()
        response.json.return_value = self._payload("wrong-service")

        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = Path(temp_dir) / "conditions.json"
            with (
                patch.object(gov24, "GOV24_SERVICE_KEY", "test-key"),
                patch.object(gov24, "MAX_RETRIES", 1),
                patch.object(gov24, "MAX_WORKERS", 1),
                patch.object(gov24.requests, "get", return_value=response),
                patch.object(gov24, "log"),
            ):
                result = gov24.fetch_many(
                    "conditions",
                    gov24.CONDITIONS_URL,
                    ["service-1"],
                    str(out_path),
                    resume=False,
                )

            failed_path = Path(temp_dir) / "conditions_failed_ids.json"
            self.assertEqual(result, [])
            self.assertEqual(json.loads(out_path.read_text(encoding="utf-8")), [])
            self.assertEqual(
                json.loads(failed_path.read_text(encoding="utf-8")), ["service-1"]
            )

    def test_resume_refetches_duplicate_existing_conditions(self) -> None:
        old_a = self._payload()
        old_a["data"][0]["marker"] = "old-a"
        old_b = self._payload()
        old_b["data"][0]["marker"] = "old-b"
        fresh = self._payload()
        fresh["data"][0]["marker"] = "fresh"

        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = Path(temp_dir) / "conditions.json"
            gov24.save([old_a, old_b], str(out_path))
            with (
                patch.object(gov24, "GOV24_SERVICE_KEY", "test-key"),
                patch.object(gov24, "MAX_WORKERS", 1),
                patch.object(
                    gov24,
                    "_fetch_one",
                    return_value=("service-1", fresh, None),
                ) as fetch_mock,
                patch.object(gov24, "log"),
            ):
                result = gov24.fetch_many(
                    "conditions",
                    gov24.CONDITIONS_URL,
                    ["service-1"],
                    str(out_path),
                )

            fetch_mock.assert_called_once_with(
                "conditions", gov24.CONDITIONS_URL, "service-1"
            )
            self.assertEqual(result, [fresh])
            self.assertEqual(
                json.loads(out_path.read_text(encoding="utf-8")), [fresh]
            )


class AtomicCheckpointTests(unittest.TestCase):
    def test_failed_replace_preserves_old_checkpoint_and_removes_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = Path(temp_dir) / "checkpoint.json"
            out_path.write_text('[{"old": true}]', encoding="utf-8")

            with patch.object(gov24.os, "replace", side_effect=OSError("stop")):
                with self.assertRaises(OSError):
                    gov24.save([{"new": True}], str(out_path))

            self.assertEqual(
                json.loads(out_path.read_text(encoding="utf-8")), [{"old": True}]
            )
            self.assertEqual(list(Path(temp_dir).glob(".checkpoint.json.*.tmp")), [])


class AgeMetadataExtractionTests(unittest.TestCase):
    def test_api_age_takes_precedence_and_accepts_numeric_strings(self) -> None:
        item = {
            "JA0110": "65",
            "JA0111": "120",
            "지원대상": "만 18세 이상",
        }
        self.assertEqual(
            to_document.extract_age_metadata(item),
            (65, 120, "support_conditions_api"),
        )

    def test_explicit_text_minimum_maximum_and_range_are_extracted(self) -> None:
        cases = (
            ({"지원대상": "만 60세 이상인 주민"}, (60, None)),
            ({"지원대상": "만 18세 미만 아동"}, (None, 17)),
            ({"선정기준": "만 19세 이상 34세 이하 청년"}, (19, 34)),
            ({"지원대상": "3~5세 유아"}, (3, 5)),
        )
        for item, expected in cases:
            with self.subTest(item=item):
                self.assertEqual(
                    to_document.extract_age_metadata(item),
                    (*expected, "support_target_text"),
                )

    def test_ambiguous_multiple_ranges_and_labels_are_not_inferred(self) -> None:
        for item in (
            {"지원대상": "청년 또는 노인"},
            {"지원대상": "만 18세 미만 아동\n만 65세 이상 노인"},
            {"지원대상": "2000년 이후 출생자"},
            {"지원대상": "만 18세 미만 자녀가 있는 부모"},
            {"지원대상": "6-11세 자녀-아버지"},
        ):
            with self.subTest(item=item):
                self.assertEqual(
                    to_document.extract_age_metadata(item),
                    (None, None, None),
                )

    def test_non_age_ja_flag_marks_support_conditions_present(self) -> None:
        statuses = to_document.build_field_statuses(
            {"JA0201": "Y"}, "service-1", set(), set()
        )

        self.assertEqual(statuses["support_conditions"], "present")

    def test_zero_age_is_a_present_support_condition(self) -> None:
        statuses = to_document.build_field_statuses(
            {"JA0110": 0}, "service-1", set(), set()
        )

        self.assertEqual(statuses["support_conditions"], "present")

    def test_invalid_ja_tokens_are_not_treated_as_present(self) -> None:
        statuses = to_document.build_field_statuses(
            {"JA0110": "Y", "JA0201": "N"}, "service-1", set(), set()
        )

        self.assertEqual(statuses["support_conditions"], "missing_source")


class PackageEntrypointTests(unittest.TestCase):
    def test_package_entrypoint_help_runs_from_repository_root(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC_ROOT)
        result = subprocess.run(
            [sys.executable, "-m", "rag_chatbot.collectors.gov_24", "--help"],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--limit", result.stdout)


if __name__ == "__main__":
    unittest.main()
