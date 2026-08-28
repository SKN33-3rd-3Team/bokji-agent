from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from rag_design.citation import citation_url_for_document  # noqa: E402
from rag_design.contracts import Document, render_legal_metadata_summary  # noqa: E402
from rag_design.validation import validate_collection_handoff  # noqa: E402
from rag_chatbot.collectors.law.filtered_to_document import (  # noqa: E402
    build_document,
    write_outputs,
)


class LawCollectorContractTests(unittest.TestCase):
    secret = "actual-law-oc"

    def setUp(self) -> None:
        records = (
            {
                "_target": "law",
                "_matched_keywords": ["보장"],
                "법령명한글": "국민기초생활 보장법",
                "법령ID": "006478",
                "법령일련번호": "276653",
                "법령구분명": "법률",
                "소관부처명": "보건복지부",
                "제개정구분명": "일부개정",
                "공포일자": "20250318",
                "시행일자": "20251001",
                "법령상세링크": (
                    f"/DRF/lawService.do?OC={self.secret}&target=law&MST=276653"
                ),
            },
            {
                "_target": "admrul",
                "_matched_keywords": ["급여"],
                "행정규칙명": "2026년 교육급여의 선정기준 및 최저보장수준",
                "행정규칙ID": "48377",
                "행정규칙일련번호": "2100000269822",
                "행정규칙종류": "고시",
                "소관부처명": "교육부",
                "제개정구분명": "전부개정",
                "발령일자": "20251217",
                "시행일자": "20260101",
            },
            {
                "_target": "ordin",
                "_matched_keywords": ["수당"],
                "자치법규명": "가평군 각종 위원회 수당 및 여비 지급 조례",
                "자치법규ID": "2019873",
                "자치법규일련번호": "2124625",
                "자치법규종류": "조례",
                "지자체기관명": "경기도 가평군",
                "제개정구분명": "전부개정",
                "공포일자": "20260420",
                "시행일자": "20260420",
            },
        )
        self.documents = tuple(build_document(record) for record in records)

    def test_all_subtypes_use_the_canonical_metadata_contract(self) -> None:
        expected = {
            "law": (
                "law:law:006478:276653:2025-10-01",
                "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=276653&efYd=20251001",
                "2025-03-18",
            ),
            "admrul": (
                "law:admrul:48377:2100000269822:2026-01-01",
                "https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=2100000269822",
                "2025-12-17",
            ),
            "ordin": (
                "law:ordin:2019873:2124625:2026-04-20",
                "https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=2124625",
                "2026-04-20",
            ),
        }
        for document in self.documents:
            self.assertIsNotNone(document)
            law_type = document.metadata["law_type"]
            with self.subTest(law_type=law_type):
                doc_id, source_url, issued_date = expected[law_type]
                self.assertEqual(document.doc_id, doc_id)
                self.assertEqual(document.source_url, source_url)
                self.assertEqual(document.metadata["issued_date"], issued_date)
                self.assertEqual(document.metadata["content_level"], "metadata_only")
                self.assertEqual(
                    document.content,
                    render_legal_metadata_summary(document.metadata),
                )
                self.assertEqual(document.sections[0].metadata["section_type"], "basic_info")
                self.assertEqual(document.parse_warnings, ())

    def test_generated_handoff_is_accepted_without_oc_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            jsonl_path = output_dir / "documents.jsonl"
            manifest_path = output_dir / "manifest.json"
            write_outputs(
                list(self.documents),
                [],
                jsonl_path,
                manifest_path,
            )
            documents = [
                Document.from_dict(json.loads(line))
                for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            ]
            handoff = json.loads(manifest_path.read_text(encoding="utf-8"))

        report = validate_collection_handoff(
            documents,
            handoff["manifest"],
            handoff["document_card"],
            secret_values=(self.secret,),
        )
        self.assertTrue(report.accepted, report.issues)
        rendered = json.dumps([document.to_dict() for document in documents])
        self.assertNotIn(self.secret, rendered)
        self.assertNotIn("OC=", rendered.upper())

    def test_tracked_sample_is_canonical_and_accepted(self) -> None:
        documents = [
            Document.from_dict(json.loads(line))
            for line in (
                REPO_ROOT / "data/samples/law_documents_sample.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        handoff = json.loads(
            (REPO_ROOT / "data/processed/law_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        handoff["manifest"]["document_count"] = len(documents)
        handoff["document_card"]["document_count"] = len(documents)
        report = validate_collection_handoff(
            documents,
            handoff["manifest"],
            handoff["document_card"],
        )
        self.assertEqual(len(documents), 5)
        self.assertEqual(
            {document.metadata["law_type"] for document in documents},
            {"law", "admrul", "ordin"},
        )
        self.assertTrue(report.accepted, report.issues)


class LawPackageEntrypointTests(unittest.TestCase):
    def test_package_entrypoint_help_runs_from_repository_root(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC_ROOT)
        result = subprocess.run(
            [sys.executable, "-m", "rag_chatbot.collectors.law", "--help"],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--skip-index", result.stdout)


if __name__ == "__main__":
    unittest.main()
