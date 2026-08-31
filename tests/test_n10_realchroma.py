"""N10 노드를 실제 ChromaVectorStore로 검증 (FakeStore 대신)."""

from __future__ import annotations

from dataclasses import replace
import gc
import json
from pathlib import Path
import tempfile
import unittest

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, EvidenceStatus, SourceType
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from src.rag_chatbot.graph.nodes.benefit_calculator import (
    _extract_amount_by_rules,
    analyze_amount_context,
    compute_total,
    _resolve_amount_without_metadata,
    calculate_benefit_amount,
)
from src.rag_chatbot.llm import FailingLLMClient, FakeLLMClient

try:
    import chromadb as _chromadb  # noqa: F401
except Exception:
    CHROMA_AVAILABLE = False
else:
    CHROMA_AVAILABLE = True

FIXTURES = Path(__file__).parent / "fixtures"


def _load_subsidy_document() -> Document:
    line = (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines()[0]
    return Document.from_dict(json.loads(line))


def _amount_claim(policy_id: str, status: EvidenceStatus) -> dict:
    return {
        "claim_id": f"{policy_id}-amount",
        "policy_id": policy_id,
        "claim_type": "amount",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": ["chunk-1"],
        "status": status,
        "reasons": ["근거 문장"],
    }


def _verdict(policy_id: str, verdict: str) -> dict:
    return {"policy_id": policy_id, "verdict": verdict, "reasons": []}


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class CalculateBenefitAmountRealChromaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.persist_directory = Path(self.temporary_directory.name) / "index"
        self.config = VectorStoreConfig(
            persist_directory=self.persist_directory,
            collection_prefix="test_n10",
            batch_size=2,
        )
        self.store = ChromaVectorStore(HashEmbeddingProvider(64), self.config)
        subsidy_document = _load_subsidy_document()
        self.policy_id = subsidy_document.source_id
        self.subsidy_chunks = chunk_document(subsidy_document)

    def tearDown(self) -> None:
        del self.store
        gc.collect()
        self.temporary_directory.cleanup()

    def _sync_with_amount(self, amount) -> None:
        chunks = tuple(
            replace(chunk, metadata={**chunk.metadata, "amount": amount})
            for chunk in self.subsidy_chunks
        )
        self.store.sync_snapshot(SourceType.SUBSIDY, chunks, snapshot_id="snap-001")

    def test_real_search_uses_structured_amount_field(self) -> None:
        self._sync_with_amount(300000)
        state = {
            "query_id": "q1",
            "eligibility_verdicts": [_verdict(self.policy_id, "충족")],
            "claim_plan": [_amount_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, self.store)

        entry = result["benefit_amounts"][0]
        self.assertEqual(entry["policy_id"], self.policy_id)
        self.assertEqual(entry["amount"], 300000.0)
        self.assertTrue(entry["rule_chunk_id"])

    def test_real_search_without_amount_metadata_yields_none_not_guessed(self) -> None:
        self.store.sync_snapshot(
            SourceType.SUBSIDY, self.subsidy_chunks, snapshot_id="snap-001"
        )
        state = {
            "query_id": "q2",
            "eligibility_verdicts": [_verdict(self.policy_id, "충족")],
            "claim_plan": [_amount_claim(self.policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, self.store)

        entry = result["benefit_amounts"][0]
        # 이 fixture 원문에는 확정 금액이 없으므로 규칙 경로도 값을 만들지
        # 않는다. 지어내지 않고 사유를 남기는지가 핵심(2026-08-31: LLM이
        # 없을 때도 규칙으로 금액을 뽑도록 바뀌면서 문구가 달라졌다).
        self.assertIsNone(entry["amount"])
        self.assertIn("규칙 추출", entry["calculation_note"])
        self.assertIn("LLM 미연결", entry["calculation_note"])

    def test_real_search_against_never_synced_source_type_yields_none(self) -> None:
        state = {
            "query_id": "q3",
            "eligibility_verdicts": [_verdict("policy-never-synced", "충족")],
            "claim_plan": [_amount_claim("policy-never-synced", EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, self.store)

        entry = result["benefit_amounts"][0]
        self.assertIsNone(entry["amount"])


if __name__ == "__main__":
    unittest.main()


class ExtractAmountByRulesTests(unittest.TestCase):
    """LLM 없이도 원문에 명시된 금액을 뽑는 규칙 경로 (2026-08-31 추가).

    예전에는 llm_client가 없으면 무조건 amount=None이라, 원문에 "월 20만원"
    이라고 적혀 있어도 화면에는 늘 "지원금액 확인 필요"만 떴다.
    """

    def test_extracts_a_single_explicit_amount(self) -> None:
        for text, expected in (
            ("월 최대 20만원을 지원합니다.", 200_000.0),
            ("가구당 월 280,000원 지급", 280_000.0),
            ("지원금 1억원", 100_000_000.0),
        ):
            with self.subTest(text=text):
                amount, note = _extract_amount_by_rules(text)
                self.assertEqual(amount, expected)
                self.assertIn("규칙", note)

    def test_refuses_to_pick_one_when_several_amounts_appear(self) -> None:
        # 조건별 차등 금액에서 하나를 골라버리면 틀린 금액을 확정값처럼 준다.
        amount, note = _extract_amount_by_rules("소득 구간별로 10만원 또는 30만원 차등 지급")
        self.assertIsNone(amount)
        self.assertIn("여러 개", note)

    def test_ignores_numbers_that_are_not_benefit_amounts(self) -> None:
        for text in (
            "비급여 진료비 총액의 50% 지원",  # 비율
            "연 1,080시간 지원",  # 시간
            "○ 서비스 제공",  # 금액 언급 없음
        ):
            with self.subTest(text=text):
                self.assertIsNone(_extract_amount_by_rules(text)[0])

    def test_excludes_self_payment_amounts(self) -> None:
        # 본인부담금을 지원금으로 읽으면 정반대 정보가 된다.
        amount, _ = _extract_amount_by_rules("월 20만원 지원, 본인부담금 5만원")
        self.assertEqual(amount, 200_000.0)


class ResolveAmountFallbackTests(unittest.TestCase):
    """LLM에게 못 물어봤을 때만 규칙으로 넘어가는지 (2026-08-31 추가)."""

    def test_llm_absent_falls_back_to_rules(self) -> None:
        amount, note = _resolve_amount_without_metadata("월 20만원 지원", None)
        self.assertEqual(amount, 200_000.0)
        self.assertIn("LLM 미연결", note)

    def test_llm_failure_falls_back_to_rules_and_reports_the_failure(self) -> None:
        amount, note = _resolve_amount_without_metadata("월 20만원 지원", FailingLLMClient("토큰 만료"))
        self.assertEqual(amount, 200_000.0)
        self.assertIn("토큰 만료", note)  # 실패 사실을 숨기지 않는다

    def test_llm_judgement_of_no_amount_is_not_overridden_by_rules(self) -> None:
        # LLM이 "조건부라 확정 금액 없음"이라고 판단했으면 규칙으로 뒤집지
        # 않는다 - 뒤집으면 조건부 금액을 확정 금액처럼 만들게 된다.
        client = FakeLLMClient('{"amount": null, "reason": "소득 구간별 차등"}')
        amount, note = _resolve_amount_without_metadata("월 20만원 지원", client)
        self.assertIsNone(amount)
        self.assertIn("소득 구간별 차등", note)

    def test_llm_response_wrapped_in_code_fence_is_still_read(self) -> None:
        # 코드펜스 때문에 파싱이 터져 금액이 버려지던 문제 - 회귀 테스트.
        client = FakeLLMClient('```json\n{"amount": 150000, "reason": "원문 명시"}\n```')
        amount, note = _resolve_amount_without_metadata("지원내용", client)
        self.assertEqual(amount, 150_000.0)
        self.assertIn("LLM이 원문에서 추출한 금액", note)


class AmountContextTests(unittest.TestCase):
    """금액의 성격(주기/한도/지급 단위) 읽기 (2026-08-31 추가).

    금액 숫자만 보여주면 "200,000원"이 월인지 연인지 1회인지, 확정인지
    상한인지 알 수 없다. 원천 데이터 실측상 42.5%가 "최대/한도" 표현이라
    이걸 놓치면 절반 가까이가 과대 표기가 된다.
    """

    def test_reads_period_maximum_and_unit(self) -> None:
        context = analyze_amount_context("월 최대 20만원을 12개월간 지원합니다.")
        self.assertEqual(context["period"], "month")
        self.assertTrue(context["is_maximum"])
        self.assertEqual(context["duration_months"], 12)

    def test_reads_per_person_and_per_household(self) -> None:
        self.assertEqual(analyze_amount_context("1인당 30만원 지급")["per_unit"], "person")
        self.assertEqual(analyze_amount_context("가구당 28만원 지급")["per_unit"], "household")

    def test_one_time_payment_is_marked(self) -> None:
        self.assertEqual(analyze_amount_context("1회에 한하여 100만원 지급")["period"], "once")

    def test_nothing_is_invented_when_the_text_is_silent(self) -> None:
        context = analyze_amount_context("소득에 따라 차등 지원")
        self.assertIsNone(context["period"])
        self.assertIsNone(context["per_unit"])
        self.assertIsNone(context["duration_months"])
        self.assertFalse(context["is_maximum"])

    def test_absurd_duration_is_ignored(self) -> None:
        # 지원 기간이 아닌 숫자를 개월수로 읽어 터무니없는 총액을 만들지 않는다.
        self.assertIsNone(analyze_amount_context("가입 후 600개월 경과")["duration_months"])


class ComputeTotalTests(unittest.TestCase):
    """총액 산술 - 원문에 근거가 있을 때만 (2026-08-31 추가)."""

    def test_monthly_amount_times_stated_duration(self) -> None:
        context = analyze_amount_context("월 최대 20만원을 12개월간 지원")
        total, note = compute_total(200_000.0, context, {})
        self.assertEqual(total, 2_400_000.0)
        self.assertIn("12개월", note)
        self.assertIn("최대", note)  # 한도 표기를 총액에도 유지

    def test_per_person_amount_times_household_size(self) -> None:
        context = analyze_amount_context("1인당 30만원 지급")
        total, note = compute_total(300_000.0, context, {"household_size": 4})
        self.assertEqual(total, 1_200_000.0)
        self.assertIn("가구원 4명", note)

    def test_monthly_without_duration_is_not_annualised(self) -> None:
        # 지원 기간을 모르는데 12를 곱하면, 실제와 다른 금액을 확정값처럼
        # 보여주게 된다 - 회귀 테스트.
        context = analyze_amount_context("월 28만원 지급")
        self.assertEqual(compute_total(280_000.0, context, {}), (None, ""))

    def test_per_person_without_household_size_is_not_multiplied(self) -> None:
        context = analyze_amount_context("1인당 30만원 지급")
        self.assertEqual(compute_total(300_000.0, context, {}), (None, ""))

    def test_absurd_household_size_is_not_multiplied(self) -> None:
        context = analyze_amount_context("1인당 30만원 지급")
        self.assertEqual(compute_total(300_000.0, context, {"household_size": 99}), (None, ""))
