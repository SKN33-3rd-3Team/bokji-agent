from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, SourceType
from rag_chatbot.graph.nodes.law_source_resolver import (
    VectorStoreLawSourceResolver,
    parse_legal_basis_names,
    resolve_required_law_sources,
)

FIXTURES = Path(__file__).parent / "fixtures"


class FakeLawResolver:
    def __init__(self, table: dict[str, dict]) -> None:
        self._table = table

    def resolve(self, law_name: str):
        return self._table.get(law_name)


class FakeExactLawStore:
    def __init__(self, chunks=()) -> None:
        self.chunks = tuple(chunks)
        self.calls: list[tuple[SourceType, dict]] = []

    def get_chunks_by_metadata(self, source_type, *, metadata_equals, **kwargs):
        self.calls.append((source_type, dict(metadata_equals)))
        return tuple(
            chunk
            for chunk in self.chunks
            if chunk.source_type is source_type
            and all(
                chunk.metadata.get(key) == value
                for key, value in metadata_equals.items()
            )
        )


class ParseLegalBasisNamesTests(unittest.TestCase):
    def test_parses_pipe_separated_names_and_drops_article_numbers(self) -> None:
        content = "유아교육법(제24조)||영유아보육법(제34조)||유아교육법 시행령(제29조)"

        names = parse_legal_basis_names(content)

        self.assertEqual(names, ["유아교육법", "영유아보육법", "유아교육법 시행령"])

    def test_handles_single_name_without_article(self) -> None:
        self.assertEqual(parse_legal_basis_names("국민기초생활 보장법"), ["국민기초생활 보장법"])

    def test_empty_content_yields_empty_list(self) -> None:
        self.assertEqual(parse_legal_basis_names(""), [])


class ResolveRequiredLawSourcesTests(unittest.TestCase):
    def test_matched_names_are_resolved(self) -> None:
        resolver = FakeLawResolver(
            {
                "유아교육법": {"law_type": "law", "source_id": "001"},
                "영유아보육법": {"law_type": "law", "source_id": "002"},
            }
        )
        content = "유아교육법(제24조)||영유아보육법(제34조)||존재안함법(제1조)"

        result = resolve_required_law_sources(content, resolver)

        self.assertEqual(
            result,
            [
                {"law_type": "law", "source_id": "001"},
                {"law_type": "law", "source_id": "002"},
            ],
        )

    def test_none_content_yields_empty_list(self) -> None:
        resolver = FakeLawResolver({})
        self.assertEqual(resolve_required_law_sources(None, resolver), [])

    def test_no_matches_yields_empty_list_not_error(self) -> None:
        resolver = FakeLawResolver({})
        result = resolve_required_law_sources("존재안함법(제1조)", resolver)
        self.assertEqual(result, [])

    def test_duplicate_resolved_pairs_are_collapsed_in_input_order(self) -> None:
        source = {"law_type": "law", "source_id": "001"}
        resolver = FakeLawResolver({"A법": source, "A법 시행령": dict(source)})

        result = resolve_required_law_sources("A법||A법 시행령", resolver)

        self.assertEqual(result, [source])


class VectorStoreLawSourceResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        documents = [
            Document.from_dict(json.loads(line))
            for line in (FIXTURES / "documents.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.chunks = tuple(
            chunk_document(document)[0]
            for document in documents
            if document.source_type is SourceType.LAW
        )

    def test_resolves_only_an_exact_canonical_law_name(self) -> None:
        store = FakeExactLawStore(self.chunks)
        resolver = VectorStoreLawSourceResolver(store)
        chunk = self.chunks[0]
        law_name = chunk.metadata["law_name"]

        result = resolver.resolve(law_name)

        self.assertEqual(
            result,
            {
                "law_type": chunk.metadata["law_type"],
                "source_id": chunk.metadata["source_id"],
            },
        )
        self.assertEqual(
            store.calls,
            [(SourceType.LAW, {"law_name": law_name})],
        )
        self.assertIsNone(resolver.resolve(f"{law_name} 시행령"))

    def test_duplicate_chunks_for_one_pair_collapse(self) -> None:
        chunk = self.chunks[0]
        resolver = VectorStoreLawSourceResolver(FakeExactLawStore((chunk, chunk)))

        self.assertEqual(
            resolver.resolve(chunk.metadata["law_name"]),
            {
                "law_type": chunk.metadata["law_type"],
                "source_id": chunk.metadata["source_id"],
            },
        )

    def test_one_exact_name_with_distinct_pairs_fails_closed(self) -> None:
        first, second = self.chunks[:2]
        law_name = first.metadata["law_name"]
        _, _, body = second.text.partition("\n")
        conflicting = replace(
            second,
            text=f"{law_name}\n{body}",
            metadata={**second.metadata, "law_name": law_name},
        )
        resolver = VectorStoreLawSourceResolver(
            FakeExactLawStore((first, conflicting))
        )

        with self.assertRaisesRegex(ValueError, "multiple source identities"):
            resolver.resolve(law_name)

    def test_mismatched_title_and_invalid_identity_fail_closed(self) -> None:
        chunk = self.chunks[0]
        law_name = chunk.metadata["law_name"]
        wrong_title = replace(chunk, text=f"다른 이름\n{chunk.text}")
        invalid_id = replace(
            chunk,
            metadata={**chunk.metadata, "source_id": "not-numeric"},
        )

        with self.assertRaisesRegex(ValueError, "canonical name"):
            VectorStoreLawSourceResolver(FakeExactLawStore((wrong_title,))).resolve(
                law_name
            )
        with self.assertRaisesRegex(ValueError, "source_id"):
            VectorStoreLawSourceResolver(FakeExactLawStore((invalid_id,))).resolve(
                law_name
            )


if __name__ == "__main__":
    unittest.main()
