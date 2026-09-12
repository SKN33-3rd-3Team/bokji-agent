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


class LoanLimitAndThresholdTests(unittest.TestCase):
    """대출 한도와 자격 문턱값을 지원금으로 읽지 않는지 (2026-09-11 추가).

    전체 코퍼스(10,968건)를 돌려보니 규칙 추출 2,239건 중, 융자·보증 프로그램의
    "한도"와 "OO원 이상" 문턱값이 지원금으로 둔갑하는 사례가 있었다. 금액이
    클수록 피해가 커서(15억, 200억) 우선 막는다. 아래 문장은 전부 실제
    data/processed/subsidy_documents.jsonl에서 가져왔다.
    """

    def test_loan_and_guarantee_limits_are_not_benefits(self) -> None:
        # 빌릴 수 있는 상한이지 받는 돈이 아니다.
        for text in (
            "○ 유아숲체험원 운영비 융자 지원\n○ 융자 한도액 : 사업장당 15억 원 한도",
            "○ 보증한도 : 운전자금 2억원 이내\n○ 보증비율 : 100%",
            "- 대출한도 : 1개 기업당 4,000만원 까지",
            "○ 보증한도 : 기업당 최대 200억원(기보증금액 포함)",
        ):
            with self.subTest(text=text):
                amount, note = _extract_amount_by_rules(text)
                self.assertIsNone(amount)
                self.assertIn("한도", note)

    def test_eligibility_thresholds_are_not_benefits(self) -> None:
        # "이상"이 붙으면 받는 금액이 아니라 자격을 가르는 기준선이다.
        for text in (
            "소송비용 및 변호사 비용 지원(단, 승소가액 3억원 이상 및 근로관계 대응사건)",
            "정부지원 대상 농기계 및 기타 일반 농기계 중 30만원 이상 농기계 지원",
        ):
            with self.subTest(text=text):
                self.assertIsNone(_extract_amount_by_rules(text)[0])

    def test_keeps_upper_bound_expressions(self) -> None:
        # "이하"/"이내"는 "농가당 300만원 이하 지원"처럼 지원 상한으로 흔히 쓰인다.
        # 문턱값과 같이 취급하면 정상 추출을 대량으로 잃는다(실측 확인).
        for text, expected in (
            ("○ 원예작물 출하용 규격박스 구입비 50% 이내 지원(농가당 300만원 이하 지원)", 3_000_000.0),
            ("○ 1인당 60만원 한도 지원", 600_000.0),
        ):
            with self.subTest(text=text):
                self.assertEqual(_extract_amount_by_rules(text)[0], expected)

    def test_filtered_amount_never_promotes_a_leftover_number(self) -> None:
        """한도를 지운 자리에 남은 숫자가 단일 금액으로 승격되면 안 된다.

        이 방어가 없으면 융자 안내문에서 한도 줄만 빠지고 무관한 숫자 하나가
        확정 금액으로 올라온다(코퍼스 실측에서 9건 발생, 최대 120억원).
        """
        text = "○ 시설자금 : 기계장비 구입에 120억원 규모 운용\n○ 대출한도 : 기업당 60억원"
        self.assertIsNone(_extract_amount_by_rules(text)[0])

    def test_tiered_amounts_with_threshold_stay_unresolved(self) -> None:
        # "30만원∼50만원 이상 차등"에서 뒷 금액만 지워지면 30만원이 틀린 확정값이 된다.
        text = "○ 양육보조금 월 30만원∼50만원 이상 차등 지원\n- 만7세 미만 : 월 34만원 이상"
        self.assertIsNone(_extract_amount_by_rules(text)[0])


class LlmAmountPromptGuardTests(unittest.TestCase):
    """LLM 금액 추출 프롬프트에 한도·문턱값 배제 지시가 있는지 (2026-09-11 추가).

    규칙 경로(_extract_amount_by_rules)의 한도·문턱값 필터는 LLM이 연결돼
    있으면 아예 실행되지 않는다(_resolve_amount_without_metadata가 LLM을
    먼저 쓰고, 응답을 받으면 그대로 반환한다). 실제 운영 환경(.env에 HF_TOKEN
    설정됨)에서는 이 프롬프트가 유일한 방어선이므로, 지시문이 빠지지 않았는지
    회귀로 고정해 둔다.
    """

    def test_prompt_instructs_llm_to_null_loan_limits_and_thresholds(self) -> None:
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(json.dumps({"amount": None, "reason": "테스트"}))
        _extract_amount_via_llm("○ 융자 한도액 : 사업장당 15억 원 한도", client)

        self.assertEqual(len(client.calls), 1)
        prompt = client.calls[0]["prompt"]
        self.assertIn("대출", prompt)
        self.assertIn("한도", prompt)
        self.assertIn("이상", prompt)

    def test_llm_nulling_a_loan_limit_is_trusted_as_is(self) -> None:
        # LLM이 지시대로 한도를 null 처리하면, 규칙 경로로 넘기지 않고
        # 그 판단을 그대로 받아들여야 한다(_resolve_amount_without_metadata의
        # "LLM이 판단해서 없다고 한 경우는 규칙으로 뒤집지 않는다" 원칙).
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(
            json.dumps({"amount": None, "reason": "융자 한도이지 지원금이 아님"})
        )
        amount, note, consulted, amount_range = _extract_amount_via_llm(
            "○ 융자 한도액 : 사업장당 15억 원 한도", client
        )
        self.assertIsNone(amount)
        self.assertTrue(consulted)
        self.assertIn("한도", note)
        self.assertIsNone(amount_range)

    def test_prompt_instructs_unit_conversion_and_range_rejection(self) -> None:
        """T1 실사용 회귀(2026-09-11): '90-110만원/월'을 '최대 90원'으로
        표시하던 버그의 원인을 프롬프트 지시로 막는다.

        원인은 두 가지였다: (1) 프롬프트가 '만원'/'억원' 표기를 '원'
        단위 정수로 변환하라고 지시하지 않아 LLM이 '90만원'을 90으로
        반환했고, (2) '90-110만원'처럼 범위로만 적힌 경우를 null 처리
        대상으로 명시하지 않았다. 두 지시 모두 프롬프트에 들어갔는지
        회귀로 고정한다.
        """
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(json.dumps({"amount": None, "reason": "테스트"}))
        _extract_amount_via_llm("90-110만원/월 지원", client)

        prompt = client.calls[0]["prompt"]
        self.assertIn("200000", prompt)  # 단위 변환 예시
        self.assertIn("min_amount", prompt)  # 범위를 amount 대신 범위 필드로
        self.assertIn("한국어로만", prompt)

    def test_broken_korean_reason_is_not_shown_to_user(self) -> None:
        """LLM이 reason에 한자를 섞어 써도(예: Qwen 실사용 중 확인된
        '資格조건', '不存在임') 화면에는 깨끗한 한국어 기본 문구만
        보인다 - amount 자체(구조화 필드)는 영향받지 않는다.
        """
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(
            json.dumps({"amount": None, "reason": "資格조건이며 不存在임"})
        )
        amount, note, consulted, amount_range = _extract_amount_via_llm("텍스트", client)

        self.assertIsNone(amount)
        self.assertTrue(consulted)
        self.assertIsNone(amount_range)
        self.assertNotIn("資格", note)
        self.assertNotIn("不存在", note)
        self.assertEqual(
            note, "LLM이 원문에서 확정 금액을 추출하지 못함(조건부이거나 명시 안 됨)"
        )

    def test_json_parse_failure_and_non_numeric_amount_report_fixed_note(self) -> None:
        """원본 응답을 그대로 노출하지 않고 고정된 '계산 실패'만 남긴다
        (2026-09-11, 사용자 요청)."""
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        amount, note, consulted, amount_range = _extract_amount_via_llm(
            "텍스트", FakeLLMClient("이건 JSON이 아님")
        )
        self.assertIsNone(amount)
        self.assertFalse(consulted)
        self.assertEqual(note, "계산 실패")
        self.assertIsNone(amount_range)

        amount, note, consulted, amount_range = _extract_amount_via_llm(
            "텍스트", FakeLLMClient(json.dumps({"amount": "삼십만원", "reason": ""}))
        )
        self.assertIsNone(amount)
        self.assertFalse(consulted)
        self.assertEqual(note, "계산 실패")
        self.assertIsNone(amount_range)


class AmountRangeTests(unittest.TestCase):
    """T1 실사용 피드백(2026-09-11): '90-110만원'처럼 조건 구분 없는
    범위는 amount=None으로 뭉개지 않고 하한~상한 그대로 보여준다.
    """

    def test_llm_range_response_is_parsed_into_amount_range(self) -> None:
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(
            json.dumps(
                {"amount": None, "min_amount": 900000, "max_amount": 1100000, "reason": ""}
            )
        )
        amount, note, consulted, amount_range = _extract_amount_via_llm(
            "90-110만원/월 지원", client
        )
        self.assertIsNone(amount)
        self.assertTrue(consulted)
        self.assertEqual(amount_range, (900000.0, 1100000.0))
        self.assertIn("범위", note)

    def test_range_with_min_greater_than_or_equal_max_is_rejected(self) -> None:
        # 모델이 하한/상한을 뒤집어 반환하거나 같은 값을 넣으면 범위로
        # 신뢰하지 않는다 - 잘못된 범위를 그대로 보여주는 것보다 안전하게
        # "확인 필요"로 남기는 편이 낫다.
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(
            json.dumps(
                {"amount": None, "min_amount": 1100000, "max_amount": 900000, "reason": ""}
            )
        )
        amount, note, consulted, amount_range = _extract_amount_via_llm("텍스트", client)
        self.assertIsNone(amount)
        self.assertIsNone(amount_range)

    def test_range_with_only_one_bound_is_ignored(self) -> None:
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(
            json.dumps({"amount": None, "min_amount": 900000, "max_amount": None, "reason": ""})
        )
        amount, note, consulted, amount_range = _extract_amount_via_llm("텍스트", client)
        self.assertIsNone(amount)
        self.assertIsNone(amount_range)

    def test_build_benefit_amount_fills_min_max_and_period_but_not_is_maximum(self) -> None:
        from src.rag_chatbot.graph.nodes.benefit_calculator import _build_benefit_amount

        entry = _build_benefit_amount(
            policy_id="policy-range",
            amount=None,
            chunk_id="chunk-1",
            chunk_text="최대 3년 간 영농정착지원금 월 90-110만원 지원",
            note="원문에 범위로만 금액이 명시됨",
            slots={},
            amount_range=(900000.0, 1100000.0),
        )
        self.assertIsNone(entry["amount"])
        self.assertEqual(entry["amount_min"], 900000.0)
        self.assertEqual(entry["amount_max"], 1100000.0)
        self.assertEqual(entry["period"], "month")
        # is_maximum/total_amount는 대표 금액이 없으면 계산하지 않는다 -
        # 이 원문의 "최대"는 기간(3년)을 수식하는 것이지 금액이 아니므로,
        # 범위에 "최대"를 잘못 붙이는 것도 함께 막는다.
        self.assertFalse(entry["is_maximum"])
        self.assertIsNone(entry["total_amount"])

    def test_format_amount_label_renders_range(self) -> None:
        from src.rag_chatbot.service import _format_amount_label

        label = _format_amount_label(
            None,
            "원문에 범위로만 금액이 명시됨",
            {"amount_min": 900000.0, "amount_max": 1100000.0, "period": "month"},
        )
        self.assertEqual(label, "월 900,000원~1,100,000원")

    def test_format_amount_label_falls_back_when_only_one_bound_present(self) -> None:
        from src.rag_chatbot.service import _format_amount_label

        label = _format_amount_label(
            None, "확인 필요 사유", {"amount_min": 900000.0, "amount_max": None}
        )
        self.assertEqual(label, "확인 필요 사유")

    def test_rule_path_no_longer_promotes_upper_bound_of_a_range(self) -> None:
        """T1 실사용 중 확인된 버그(2026-09-11): 오프라인(규칙) 경로가
        '90-110만원'에서 '90'을 놓치고 '110만원'만 단일 확정 금액인 것처럼
        반환하던 문제 - 지금은 범위 표현의 일부를 단일 후보로 세지 않는다.
        """
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_by_rules

        amount, note = _extract_amount_by_rules("90-110만원/월 지원")
        self.assertIsNone(amount)
        self.assertIn("범위", note)

    def test_extract_range_by_rules_reads_shared_unit_range(self) -> None:
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_range_by_rules

        self.assertEqual(
            _extract_range_by_rules("90-110만원/월 지원"), (900000.0, 1100000.0)
        )
        self.assertEqual(
            _extract_range_by_rules("월 90~110만원 지원"), (900000.0, 1100000.0)
        )

    def test_extract_range_by_rules_rejects_conditional_tiers(self) -> None:
        # "10-30만원"이 대시로 적혀 있어도 근처에 "소득 구간별 차등"이
        # 있으면 진짜 범위가 아니라 조건별로 다른 두 금액일 가능성이 커서
        # 범위로 확정하지 않는다.
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_range_by_rules

        self.assertIsNone(_extract_range_by_rules("소득 구간별로 10-30만원 차등 지급"))
        self.assertIsNone(_extract_range_by_rules("10-30만원 소득 구간별 차등 지급"))

    def test_extract_range_by_rules_rejects_multiple_or_reversed_ranges(self) -> None:
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_range_by_rules

        # 범위가 두 개면(어느 쪽인지 알 수 없음) 확정하지 않는다.
        self.assertIsNone(
            _extract_range_by_rules("1차 90-110만원 지원, 2차 50-70만원 지원")
        )
        # 하한/상한이 뒤집혀 있으면 신뢰하지 않는다.
        self.assertIsNone(_extract_range_by_rules("110-90만원 지원"))

    def test_offline_path_shows_range_when_llm_unavailable(self) -> None:
        """사이드 이펙트 수정(2026-09-11): LLM 미연결/실패 시에도 범위를
        보여준다 - 이전에는 이 경우 '확인 필요'로만 표시됐다.
        """
        amount, note, amount_range = _resolve_amount_without_metadata(
            "90-110만원/월 지원", None
        )
        self.assertIsNone(amount)
        self.assertEqual(amount_range, (900000.0, 1100000.0))

        amount, note, amount_range = _resolve_amount_without_metadata(
            "90-110만원/월 지원", FailingLLMClient("토큰 만료")
        )
        self.assertIsNone(amount)
        self.assertEqual(amount_range, (900000.0, 1100000.0))

    def test_llm_claimed_range_is_discarded_without_corroborating_text(self) -> None:
        """사이드 이펙트 수정(2026-09-11): LLM이 min_amount/max_amount를
        줘도, 원문에 실제로 범위 표현이 없으면(규칙으로 교차 검증 실패)
        그 범위를 믿지 않는다 - 조건부 차등을 범위로 착각한 경우를
        대비한 안전장치다.
        """
        from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm

        client = FakeLLMClient(
            json.dumps(
                {
                    "amount": None,
                    "min_amount": 100000,
                    "max_amount": 300000,
                    "reason": "소득 구간별로 다르게 지급됨",
                }
            )
        )
        # 이 원문에는 규칙으로 확인 가능한 범위 표현("숫자-숫자만원")이 없다
        # - "10만원"과 "30만원"이 조건에 따라 각각 적힌 것뿐이다.
        amount, note, consulted, amount_range = _extract_amount_via_llm(
            "소득 구간에 따라 10만원 또는 30만원을 차등 지급한다", client
        )
        self.assertIsNone(amount)
        self.assertIsNone(amount_range)
        self.assertEqual(note, "소득 구간별로 다르게 지급됨")

    def test_range_total_amount_computed_when_duration_known(self) -> None:
        """사이드 이펙트 수정(2026-09-11): 범위에도 원문에 개월수 근거가
        있으면 총액(하한~상한)을 계산한다.
        """
        from src.rag_chatbot.graph.nodes.benefit_calculator import _build_benefit_amount

        entry = _build_benefit_amount(
            policy_id="policy-range-total",
            amount=None,
            chunk_id="chunk-1",
            chunk_text="영농정착지원금 월 90-110만원, 12개월 지원",
            note="원문에 범위로만 금액이 명시됨",
            slots={},
            amount_range=(900000.0, 1100000.0),
        )
        self.assertEqual(entry["total_amount_min"], 900000.0 * 12)
        self.assertEqual(entry["total_amount_max"], 1100000.0 * 12)

    def test_format_amount_label_renders_range_with_total(self) -> None:
        from src.rag_chatbot.service import _format_amount_label

        label = _format_amount_label(
            None,
            "원문에 범위로만 금액이 명시됨",
            {
                "amount_min": 900000.0,
                "amount_max": 1100000.0,
                "period": "month",
                "total_amount_min": 10800000.0,
                "total_amount_max": 13200000.0,
            },
        )
        self.assertEqual(
            label, "월 900,000원~1,100,000원 (총 10,800,000원~13,200,000원)"
        )


class ResolveAmountFallbackTests(unittest.TestCase):
    """LLM에게 못 물어봤을 때만 규칙으로 넘어가는지 (2026-08-31 추가)."""

    def test_llm_absent_falls_back_to_rules(self) -> None:
        amount, note, amount_range = _resolve_amount_without_metadata("월 20만원 지원", None)
        self.assertEqual(amount, 200_000.0)
        self.assertIn("LLM 미연결", note)
        self.assertIsNone(amount_range)

    def test_llm_failure_falls_back_to_rules_and_reports_the_failure(self) -> None:
        """LLM 실패해도 규칙 경로 금액은 그대로 쓰고, 실패 사실도 남는다.

        2026-09-11: 실패 사실 자체(계산 실패)는 여전히 note에 남지만,
        예외 메시지 원문("토큰 만료")은 더 이상 노출하지 않는다 - 실제
        운영 중 LLMCallError 메시지에 운영자용 디버깅 안내까지 섞여
        나와 사용자 화면에 그대로 노출된 문제가 있었다(사용자 요청으로
        변경, benefit_calculator.py의 같은 수정 참고).
        """
        amount, note, amount_range = _resolve_amount_without_metadata(
            "월 20만원 지원", FailingLLMClient("토큰 만료")
        )
        self.assertEqual(amount, 200_000.0)
        self.assertIn("계산 실패", note)
        self.assertNotIn("토큰 만료", note)
        self.assertIsNone(amount_range)

    def test_llm_judgement_of_no_amount_is_not_overridden_by_rules(self) -> None:
        # LLM이 "조건부라 확정 금액 없음"이라고 판단했으면 규칙으로 뒤집지
        # 않는다 - 뒤집으면 조건부 금액을 확정 금액처럼 만들게 된다.
        client = FakeLLMClient('{"amount": null, "reason": "소득 구간별 차등"}')
        amount, note, amount_range = _resolve_amount_without_metadata("월 20만원 지원", client)
        self.assertIsNone(amount)
        self.assertIn("소득 구간별 차등", note)

    def test_llm_response_wrapped_in_code_fence_is_still_read(self) -> None:
        # 코드펜스 때문에 파싱이 터져 금액이 버려지던 문제 - 회귀 테스트.
        client = FakeLLMClient('```json\n{"amount": 150000, "reason": "원문 명시"}\n```')
        amount, note, amount_range = _resolve_amount_without_metadata("지원내용", client)
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
