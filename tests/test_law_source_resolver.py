from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_chatbot.graph.nodes.law_source_resolver import (
    LawDocumentIndexResolver,
    parse_legal_basis_names,
    resolve_required_law_sources,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeLawResolver:
    def __init__(self, table: dict[str, dict]) -> None:
        self._table = table

    def resolve(self, law_name: str):
        return self._table.get(law_name)


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


class LawDocumentIndexResolverTests(unittest.TestCase):
    def test_loads_real_committed_sample_and_resolves_exact_match(self) -> None:
        sample_path = REPO_ROOT / "data" / "samples" / "law_documents_sample.jsonl"
        resolver = LawDocumentIndexResolver(sample_path)

        # 실제 커밋된 샘플에 있는 법령명으로 조회 (유나님이 만든 진짜 데이터)
        result = resolver.resolve(
            "10ㆍ29이태원참사 피해자 권리보장과 진상규명 및 재발방지를 위한 특별법"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["law_type"], "law")
        self.assertEqual(result["source_id"], "014656")

    def test_unmatched_name_returns_none(self) -> None:
        sample_path = REPO_ROOT / "data" / "samples" / "law_documents_sample.jsonl"
        resolver = LawDocumentIndexResolver(sample_path)

        self.assertIsNone(resolver.resolve("이런 법은 없습니다"))


if __name__ == "__main__":
    unittest.main()
