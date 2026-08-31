"""Tests for the ``index-documents`` vector CLI subcommand.

Covers the new Document -> Chunk -> vector-store ingest path (Issue #18):
loading raw Document JSONL, chunking it per source, rejecting a mismatched
source_type, and syncing the resulting chunks through the same CLI wiring
``index`` already uses.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from rag_design.chunking import ChunkingConfig, chunk_document
from rag_design.contracts import Chunk, Document, SourceType
from rag_design.vector_cli import (
    _chunk_documents,
    _load_documents,
    build_parser,
    main,
)

try:
    import chromadb as _chromadb  # noqa: F401
except Exception:
    CHROMA_AVAILABLE = False
else:
    CHROMA_AVAILABLE = True


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture_documents() -> tuple[Document, ...]:
    documents = tuple(
        Document.from_dict(json.loads(line))
        for line in (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if len(documents) != 4:
        raise AssertionError("fixture must contain one subsidy and three legal documents")
    return documents


class _Utf8StdoutBuffer(io.StringIO):
    """Stand-in for a real stdout stream that main() can reconfigure.

    main() always calls ``sys.stdout.reconfigure(encoding="utf-8")`` before
    printing (to avoid Windows console-codepage corruption of Korean text);
    a bare ``io.StringIO`` has no such method, so tests need this shim to
    capture output.
    """

    def reconfigure(self, *args, **kwargs) -> None:  # noqa: D401 - no-op shim
        return None


def run_cli(argv: list[str]) -> tuple[int, dict | list]:
    buffer = _Utf8StdoutBuffer()
    with contextlib.redirect_stdout(buffer):
        exit_code = main(argv)
    return exit_code, json.loads(buffer.getvalue())


class BuildParserTests(unittest.TestCase):
    def test_index_documents_parses_defaults_and_overrides(self) -> None:
        args = build_parser().parse_args(
            [
                "index-documents",
                "--source",
                "law",
                "--snapshot-id",
                "law-001",
                "--documents",
                "law_documents.jsonl",
            ]
        )
        self.assertEqual(args.command, "index-documents")
        self.assertEqual(args.source, "law")
        self.assertEqual(args.documents, Path("law_documents.jsonl"))
        self.assertEqual(args.max_chars, 800)
        self.assertEqual(args.overlap_chars, 100)
        self.assertIsNone(args.chunks_out)

        overridden = build_parser().parse_args(
            [
                "index-documents",
                "--source",
                "subsidy",
                "--snapshot-id",
                "subsidy-001",
                "--documents",
                "subsidy_documents.jsonl",
                "--max-chars",
                "500",
                "--overlap-chars",
                "50",
                "--chunks-out",
                "subsidy_chunks.jsonl",
            ]
        )
        self.assertEqual(overridden.max_chars, 500)
        self.assertEqual(overridden.overlap_chars, 50)
        self.assertEqual(overridden.chunks_out, Path("subsidy_chunks.jsonl"))


class LoadDocumentsTests(unittest.TestCase):
    def test_load_documents_parses_fixture(self) -> None:
        documents = _load_documents(FIXTURES / "documents.jsonl")
        self.assertEqual(len(documents), 4)
        self.assertTrue(all(isinstance(item, Document) for item in documents))
        self.assertEqual(documents[0].source_type, SourceType.SUBSIDY)
        self.assertTrue(
            all(item.source_type is SourceType.LAW for item in documents[1:])
        )

    def test_load_documents_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "documents.jsonl"
            fixture_text = (FIXTURES / "documents.jsonl").read_text(encoding="utf-8")
            path.write_text(f"\n{fixture_text}\n\n", encoding="utf-8")
            documents = _load_documents(path)
        self.assertEqual(len(documents), 4)

    def test_load_documents_reports_offending_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "documents.jsonl"
            fixture_lines = (
                (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines()
            )
            path.write_text(
                "\n".join([fixture_lines[0], "{not valid json"]), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "line 2"):
                _load_documents(path)


class ChunkDocumentsTests(unittest.TestCase):
    def setUp(self) -> None:
        documents = load_fixture_documents()
        self.subsidy = documents[0]
        self.legal_documents = documents[1:]

    def test_chunk_documents_matches_direct_chunk_document(self) -> None:
        config = ChunkingConfig()
        chunks = _chunk_documents([self.subsidy], SourceType.SUBSIDY, config)
        expected = chunk_document(self.subsidy, config)
        self.assertEqual(tuple(chunks), expected)
        self.assertTrue(all(isinstance(item, Chunk) for item in chunks))

    def test_chunk_documents_concatenates_across_multiple_documents(self) -> None:
        config = ChunkingConfig()
        chunks = _chunk_documents(list(self.legal_documents), SourceType.LAW, config)
        expected_count = sum(
            len(chunk_document(document, config)) for document in self.legal_documents
        )
        self.assertEqual(len(chunks), expected_count)

    def test_chunk_documents_rejects_source_type_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, self.subsidy.doc_id):
            _chunk_documents([self.subsidy], SourceType.LAW, ChunkingConfig())


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class IndexDocumentsCLIIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.persist_directory = Path(self.temporary_directory.name) / "index"
        self.documents_dir = Path(self.temporary_directory.name) / "documents"
        self.documents_dir.mkdir()

        documents = load_fixture_documents()
        self.subsidy = documents[0]
        self.legal_documents = documents[1:]

        self.subsidy_path = self.documents_dir / "subsidy_documents.jsonl"
        self.subsidy_path.write_text(
            json.dumps(self.subsidy.to_dict(), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.law_path = self.documents_dir / "law_documents.jsonl"
        self.law_path.write_text(
            "\n".join(
                json.dumps(document.to_dict(), ensure_ascii=False)
                for document in self.legal_documents
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _global_args(self) -> list[str]:
        return [
            "--persist-directory",
            str(self.persist_directory),
            "--collection-prefix",
            "test_ingest",
            "--embedding",
            "hash",
        ]

    def test_index_documents_syncs_chunks_and_reports_counts(self) -> None:
        exit_code, payload = run_cli(
            self._global_args()
            + [
                "index-documents",
                "--source",
                "subsidy",
                "--snapshot-id",
                "subsidy-001",
                "--documents",
                str(self.subsidy_path),
            ]
        )
        self.assertEqual(exit_code, 0)
        expected_chunks = chunk_document(self.subsidy)
        self.assertEqual(payload["document_count"], 1)
        self.assertEqual(payload["chunk_count"], len(expected_chunks))
        self.assertEqual(payload["upserted_count"], len(expected_chunks))
        self.assertEqual(payload["total_count"], len(expected_chunks))

        # The chunks actually landed in the store, and are independently
        # retrievable through the existing `search` subcommand.
        search_code, results = run_cli(
            self._global_args()
            + [
                "search",
                "--source",
                "subsidy",
                "--query-id",
                "q-ingest",
                "--query",
                expected_chunks[0].text,
                "--top-k",
                "1",
            ]
        )
        self.assertEqual(search_code, 0)
        self.assertEqual(results[0]["chunk"]["chunk_id"], expected_chunks[0].chunk_id)

    def test_index_documents_keeps_law_and_subsidy_collections_separate(self) -> None:
        run_cli(
            self._global_args()
            + [
                "index-documents",
                "--source",
                "subsidy",
                "--snapshot-id",
                "subsidy-001",
                "--documents",
                str(self.subsidy_path),
            ]
        )
        exit_code, payload = run_cli(
            self._global_args()
            + [
                "index-documents",
                "--source",
                "law",
                "--snapshot-id",
                "law-001",
                "--documents",
                str(self.law_path),
            ]
        )
        self.assertEqual(exit_code, 0)
        expected_chunk_count = sum(
            len(chunk_document(document)) for document in self.legal_documents
        )
        self.assertEqual(payload["chunk_count"], expected_chunk_count)

        # Both snapshots are independently searchable through their own
        # source collection; a subsidy-text query must not surface law chunks.
        search_code, subsidy_results = run_cli(
            self._global_args()
            + [
                "search",
                "--source",
                "subsidy",
                "--query-id",
                "q-subsidy-only",
                "--query",
                chunk_document(self.subsidy)[0].text,
                "--top-k",
                "5",
            ]
        )
        self.assertEqual(search_code, 0)
        self.assertTrue(subsidy_results)
        self.assertTrue(
            all(item["index_name"] == "subsidy" for item in subsidy_results)
        )

    def test_index_documents_rejects_mismatched_source(self) -> None:
        with self.assertRaises(SystemExit):
            run_cli(
                self._global_args()
                + [
                    "index-documents",
                    "--source",
                    "law",
                    "--snapshot-id",
                    "law-mismatch",
                    "--documents",
                    str(self.subsidy_path),
                ]
            )

    def test_chunks_out_writes_reloadable_chunk_jsonl(self) -> None:
        chunks_out = self.documents_dir / "subsidy_chunks.jsonl"
        exit_code, _ = run_cli(
            self._global_args()
            + [
                "index-documents",
                "--source",
                "subsidy",
                "--snapshot-id",
                "subsidy-002",
                "--documents",
                str(self.subsidy_path),
                "--chunks-out",
                str(chunks_out),
            ]
        )
        self.assertEqual(exit_code, 0)
        reloaded = [
            Chunk.from_dict(json.loads(line))
            for line in chunks_out.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(tuple(reloaded), chunk_document(self.subsidy))


if __name__ == "__main__":
    unittest.main()
