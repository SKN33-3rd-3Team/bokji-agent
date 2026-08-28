from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
