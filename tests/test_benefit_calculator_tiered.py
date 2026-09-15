"""N10 조건부/구간별 금액 규칙 + N10a(request_calc_info) 테스트 (2026-09-08 추가).

기존 tests/test_n10_realchroma.py는 실제 ChromaVectorStore로 단일 금액
경로를 검증한다. 이 파일은 새로 추가한 조건부 규칙 경로(_extract_tiered_
rule_via_llm/_select_tier_amount/_resolve_amount)와 N10a 노드를
FakeStore/FakeLLMClient로 빠르게 검증한다 - chromadb 없이도 규칙 자체가
맞는지 볼 수 있게 tests/test_graph_nodes.py와 같은 패턴을 쓴다.
"""

from __future__ import annotations

import json
import unittest

from rag_design.contracts import (
    Chunk,
    EvidenceStatus,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.builder import route_after_benefit_calculator
from src.rag_chatbot.graph.nodes.benefit_calculator import (
    _BENEFIT_CALC_MAX_NEW_TOKENS,
    _extract_amount_via_llm,
    _extract_tiered_rule_via_llm,
    _resolve_amount,
    _select_tier_amount,
    calculate_benefit_amount,
)
from src.rag_chatbot.graph.nodes.request_calc_info import (
    apply_calc_skip,
    generate_calc_followup_question,
    is_calc_skip_response,
    merge_calc_choice_answer,
    merge_calc_slot_answer,
    request_calc_info_input,
)
from src.rag_chatbot.graph.slot_schema import MAX_SLOT_ASKS, UNKNOWN
from src.rag_chatbot.llm import FailingLLMClient, FakeLLMClient


def _tiered_response(variable: str, tiers: list[dict]) -> str:
    return json.dumps({"variable": variable, "tiers": tiers, "reason": "테스트"})


class ExtractTieredRuleViaLLMTests(unittest.TestCase):
    def test_no_llm_client_skips_recognition(self) -> None:
        rule, note = _extract_tiered_rule_via_llm("월 20만원 지원", None)
        self.assertIsNone(rule)
        self.assertIn("LLM 미연결", note)

    def test_valid_tiered_rule_is_extracted(self) -> None:
        response = _tiered_response(
            "income_bracket",
            [
                {"match_values": ["under_30", "pct_30_50"], "amount": 300000, "label": "중위소득 50% 이하"},
                {"match_values": ["pct_50_75"], "amount": 200000, "label": "중위소득 50~75%"},
            ],
        )
        rule, note = _extract_tiered_rule_via_llm("소득 구간별로 차등 지급", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(rule["variable"], "income_bracket")
        self.assertEqual(len(rule["tiers"]), 2)
        self.assertEqual(note, "")

    def test_no_conditional_rule_returns_none_with_reason(self) -> None:
        response = json.dumps({"variable": None, "reason": "단일 확정 금액"})
        rule, note = _extract_tiered_rule_via_llm("월 20만원 지원", FakeLLMClient(response))
        self.assertIsNone(rule)
        self.assertIn("단일 확정 금액", note)

    def test_uses_protected_max_tokens_budget_not_global_default(self) -> None:
        """2026-09-11: 이 호출은 규칙 기반 폴백이 없는 유일한 경로라,
        전역 LLM_MAX_NEW_TOKENS(속도 때문에 1024로 낮춰둠)를 그대로
        쓰면 추론형 모델의 내부 '생각' 토큰 때문에 실패할 수 있다.
        그래서 별도의 보호된 예산(_BENEFIT_CALC_MAX_NEW_TOKENS)을
        써야 한다 - 인스턴스 기본값이 아니라 이 값이 실제로
        전달되는지 FakeLLMClient.calls로 확인한다."""
        response = _tiered_response("income_bracket", [])
        client = FakeLLMClient(response)
        _extract_tiered_rule_via_llm("소득 구간별로 차등 지급", client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["max_tokens"], _BENEFIT_CALC_MAX_NEW_TOKENS)

    def test_broken_korean_reason_falls_back_to_default(self) -> None:
        """reason에 한자가 섞이면(Qwen 실사용 중 확인) 기본 문구로 대체한다
        (2026-09-11)."""
        response = json.dumps({"variable": None, "reason": "不存在임"})
        rule, note = _extract_tiered_rule_via_llm("월 20만원 지원", FakeLLMClient(response))
        self.assertIsNone(rule)
        self.assertNotIn("不存在", note)
        self.assertEqual(note, "원문에 조건부 규칙(변수/선택 옵션) 없음")

    def test_unknown_variable_is_not_trusted(self) -> None:
        response = json.dumps({"variable": "근로일수", "tiers": []})
        rule, note = _extract_tiered_rule_via_llm("근로일수에 따라 차등", FakeLLMClient(response))
        self.assertIsNone(rule)
        # 2026-09-11: 예전엔 f"...(신뢰하지 않음): {variable!r}"처럼 LLM이
        # 반환한 원본 값을 그대로 노출했다 - 계약 위반 응답이라는 성격은
        # 같으므로 다른 실패 경로와 동일하게 고정 문구로 통일한다.
        self.assertEqual(note, "계산 실패")

    def test_invalid_match_values_are_dropped_and_empty_tier_yields_no_rule(self) -> None:
        # match_values에 계약 밖 값("moderate")만 있으면 그 tier는 통째로 버려진다.
        response = _tiered_response(
            "income_bracket", [{"match_values": ["moderate"], "amount": 100000, "label": "x"}]
        )
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNone(rule)
        self.assertIn("유효한 구간을 하나도", note)

    def test_overlapping_match_values_across_tiers_are_dropped(self) -> None:
        # 같은 값이 두 tier에 걸치면 어느 쪽도 확정할 수 없으니 둘 다에서 뺀다.
        response = _tiered_response(
            "employment_status",
            [
                {"match_values": ["employed", "self_employed"], "amount": 100000, "label": "a"},
                {"match_values": ["self_employed"], "amount": 200000, "label": "b"},
            ],
        )
        rule, _ = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        matched = {v for tier in rule["tiers"] for v in tier["match_values"]}
        self.assertNotIn("self_employed", matched)
        self.assertIn("employed", matched)

    def test_malformed_json_does_not_crash(self) -> None:
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient("이건 JSON이 아님"))
        self.assertIsNone(rule)
        # 2026-09-11: 원본 응답 일부를 그대로 노출하지 않기로 했다(사용자
        # 요청) - 고정된 "계산 실패" 문구인지만 확인한다.
        self.assertEqual(note, "계산 실패")

    def test_llm_call_failure_is_reported(self) -> None:
        """LLM 호출 실패는 예외 메시지 원문 대신 고정 문구로 보고된다.

        2026-09-11 이전에는 예외 메시지(예: "토큰 만료")를 그대로 note에
        담아 "실패 사실을 숨기지 않는다"는 원칙을 지켰다. 그런데 실제
        운영 중 LLMCallError 메시지 안에 "timeout_seconds를 늘리거나 더
        작은 모델을 쓰세요" 같은 운영자용 디버깅 안내까지 들어 있어서,
        이 원문이 그대로 사용자 화면(needs_confirmation)에 노출되는 문제가
        생겼다. 사용자 요청으로 실패 시 항상 고정된 "계산 실패" 문구만
        보여주도록 바꿨다 - amount가 None이라는 사실 자체는 그대로
        드러나므로 실패를 숨기는 것은 아니다.
        """
        rule, note = _extract_tiered_rule_via_llm("텍스트", FailingLLMClient("토큰 만료"))
        self.assertIsNone(rule)
        self.assertEqual(note, "계산 실패")
        self.assertNotIn("토큰 만료", note)


class ExtractAmountViaLLMTests(unittest.TestCase):
    def test_uses_protected_max_tokens_budget_not_global_default(self) -> None:
        """_extract_tiered_rule_via_llm과 마찬가지로 규칙 기반 폴백이
        없는 유일한 경로이므로, 전역 LLM_MAX_NEW_TOKENS(1024)가 아니라
        별도로 보호된 예산(_BENEFIT_CALC_MAX_NEW_TOKENS)을 써야 한다."""
        response = json.dumps(
            {"amount": 200000, "min_amount": None, "max_amount": None, "reason": "테스트"}
        )
        client = FakeLLMClient(response)
        _extract_amount_via_llm("월 20만원 지원", client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["max_tokens"], _BENEFIT_CALC_MAX_NEW_TOKENS)


def _numeric_tiered_response(variable: str, tiers: list[dict]) -> str:
    return json.dumps({"variable": variable, "tiers": tiers, "reason": "테스트"})


class ExtractNumericTieredRuleViaLLMTests(unittest.TestCase):
    """2026-09-11 확장: children_count/household_size는 열거형이 아니라
    구간(match_min/match_max)으로 tier를 표현한다 - 실제 정책인
    "첫만남이용권"(첫째 200만원/둘째 이상 300만원), "임산부 교통비
    바우처"(첫째 70만원/둘째 80만원/셋째 이상 100만원)가 이 경로로
    되묻기까지 가는지 확인한다."""

    def test_valid_numeric_tiers_are_extracted(self) -> None:
        response = _numeric_tiered_response(
            "children_count",
            [
                {"match_min": 1, "match_max": 1, "amount": 2000000, "label": "첫째아"},
                {"match_min": 2, "match_max": None, "amount": 3000000, "label": "둘째아 이상"},
            ],
        )
        rule, note = _extract_tiered_rule_via_llm("첫째아 200만원, 둘째아 이상 300만원", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(rule["variable"], "children_count")
        self.assertEqual(len(rule["tiers"]), 2)
        self.assertEqual(note, "")

    def test_three_tier_numeric_rule_is_extracted(self) -> None:
        """임산부 교통비 바우처: 첫째 70만원/둘째 80만원/셋째 이상 100만원."""
        response = _numeric_tiered_response(
            "children_count",
            [
                {"match_min": 1, "match_max": 1, "amount": 700000, "label": "첫째"},
                {"match_min": 2, "match_max": 2, "amount": 800000, "label": "둘째"},
                {"match_min": 3, "match_max": None, "amount": 1000000, "label": "셋째 이상"},
            ],
        )
        rule, note = _extract_tiered_rule_via_llm("자녀수별 차등", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(len(rule["tiers"]), 3)

    def test_tier_missing_both_bounds_is_dropped(self) -> None:
        """match_min/match_max가 둘 다 null이면 무제한 tier라 의미가 없다."""
        response = _numeric_tiered_response(
            "children_count",
            [{"match_min": None, "match_max": None, "amount": 100000, "label": "전체"}],
        )
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNone(rule)
        self.assertIn("추출하지 못함", note)

    def test_min_greater_than_max_is_dropped(self) -> None:
        response = _numeric_tiered_response(
            "children_count",
            [
                {"match_min": 5, "match_max": 1, "amount": 100000, "label": "역전"},
                {"match_min": 1, "match_max": 1, "amount": 200000, "label": "정상"},
            ],
        )
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(len(rule["tiers"]), 1)
        self.assertEqual(rule["tiers"][0]["amount"], 200000.0)

    def test_overlapping_numeric_tiers_are_both_dropped(self) -> None:
        """1~3과 2~4가 2/3에서 겹친다 - 겹치면 두 tier 모두 버려서
        조용히 하나를 고르지 않는다(_parse_enum_tiers의 값 겹침 처리와
        같은 원칙)."""
        response = _numeric_tiered_response(
            "children_count",
            [
                {"match_min": 1, "match_max": 3, "amount": 100000, "label": "1~3"},
                {"match_min": 2, "match_max": 4, "amount": 200000, "label": "2~4"},
            ],
        )
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNone(rule)
        self.assertIn("추출하지 못함", note)

    def test_household_size_variable_is_also_supported(self) -> None:
        response = _numeric_tiered_response(
            "household_size",
            [{"match_min": 1, "match_max": None, "amount": 100000, "label": "1인당"}],
        )
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(rule["variable"], "household_size")



class SelectTierAmountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = {
            "variable": "income_bracket",
            "tiers": [
                {"match_values": ["under_30", "pct_30_50"], "amount": 300000.0, "label": "중위소득 50% 이하"},
                {"match_values": ["pct_50_75"], "amount": 200000.0, "label": "중위소득 50~75%"},
            ],
        }

    def test_matching_hard_gate_slot_selects_amount(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"income_bracket": "pct_50_75"}, {})
        self.assertEqual(amount, 200000.0)
        self.assertIsNone(missing)
        self.assertIn("income_bracket", note)

    def test_value_not_covered_by_any_tier_yields_none(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"income_bracket": "over_150"}, {})
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("일치하지 않음", note)

    def test_hard_gate_unknown_sentinel_is_not_asked_again(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"income_bracket": "unknown"}, {})
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("확인하지 못해", note)

    def test_soft_slot_missing_and_askable_requests_it(self) -> None:
        rule = {**self.rule, "variable": "marital_status", "tiers": [
            {"match_values": ["single"], "amount": 100000.0, "label": "미혼"},
        ]}
        amount, note, missing = _select_tier_amount(rule, {}, {})
        self.assertIsNone(amount)
        self.assertEqual(missing, "marital_status")

    def test_soft_slot_missing_but_ask_limit_reached_stops_asking(self) -> None:
        rule = {**self.rule, "variable": "marital_status", "tiers": [
            {"match_values": ["single"], "amount": 100000.0, "label": "미혼"},
        ]}
        amount, note, missing = _select_tier_amount(
            rule, {}, {"marital_status": MAX_SLOT_ASKS}
        )
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("재질문 상한", note)


class SelectTierAmountNumericTests(unittest.TestCase):
    """첫만남이용권(첫째 200만원/둘째 이상 300만원) 같은 children_count
    기반 tier가 실제로 되묻고, 값이 있으면 정확히 계산되는지 확인한다."""

    def setUp(self) -> None:
        self.rule = {
            "variable": "children_count",
            "tiers": [
                {"match_min": 1, "match_max": 1, "amount": 2000000.0, "label": "첫째아"},
                {"match_min": 2, "match_max": None, "amount": 3000000.0, "label": "둘째아 이상"},
            ],
        }

    def test_first_child_selects_first_tier(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"children_count": 1}, {})
        self.assertEqual(amount, 2000000.0)
        self.assertIsNone(missing)

    def test_second_child_selects_open_ended_tier(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"children_count": 2}, {})
        self.assertEqual(amount, 3000000.0)
        self.assertIsNone(missing)

    def test_third_child_still_matches_open_ended_tier(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"children_count": 3}, {})
        self.assertEqual(amount, 3000000.0)

    def test_missing_children_count_is_askable(self) -> None:
        """이게 바로 "지금 되묻기 안 뜬다"의 원인이었던 케이스 - children_count가
        _TIER_ASKABLE_SOFT_FIELDS에 없던 예전에는 missing이 항상 None이었다."""
        amount, note, missing = _select_tier_amount(self.rule, {}, {})
        self.assertIsNone(amount)
        self.assertEqual(missing, "children_count")

    def test_missing_children_count_ask_limit_reached_stops_asking(self) -> None:
        amount, note, missing = _select_tier_amount(
            self.rule, {}, {"children_count": MAX_SLOT_ASKS}
        )
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("재질문 상한", note)

    def test_household_size_missing_is_also_askable(self) -> None:
        rule = {**self.rule, "variable": "household_size", "tiers": [
            {"match_min": 1, "match_max": 2, "amount": 100000.0, "label": "1~2인"},
        ]}
        amount, note, missing = _select_tier_amount(rule, {}, {})
        self.assertIsNone(amount)
        self.assertEqual(missing, "household_size")

    def test_value_not_covered_by_any_numeric_tier_yields_none(self) -> None:
        rule = {"variable": "children_count", "tiers": [
            {"match_min": 1, "match_max": 1, "amount": 100000.0, "label": "첫째만"},
        ]}
        amount, note, missing = _select_tier_amount(rule, {"children_count": 2}, {})
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("일치하지 않음", note)



class ResolveAmountTests(unittest.TestCase):
    def test_tiered_rule_takes_priority_over_flat(self) -> None:
        response = _tiered_response(
            "income_bracket", [{"match_values": ["pct_50_75"], "amount": 200000, "label": "x"}]
        )
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            "소득 구간별 차등 지급", FakeLLMClient(response), {"income_bracket": "pct_50_75"}, {}
        )
        self.assertEqual(amount, 200000.0)
        self.assertFalse(needs_more_info)
        self.assertIsNone(missing)
        self.assertIsNone(amount_range)

    def test_soft_slot_missing_surfaces_needs_more_info(self) -> None:
        response = _tiered_response(
            "pregnancy_status", [{"match_values": ["pregnant"], "amount": 500000, "label": "임신"}]
        )
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            "임신 여부에 따라 차등 지급", FakeLLMClient(response), {}, {}
        )
        self.assertIsNone(amount)
        self.assertTrue(needs_more_info)
        self.assertEqual(missing, "pregnancy_status")
        self.assertIsNone(amount_range)

    def test_no_tiered_rule_falls_back_to_flat_amount_path(self) -> None:
        # variable이 없는(flat) 응답이면 기존 _resolve_amount_without_metadata
        # 경로로 넘어간다 - 회귀 방지(기존 단일 금액 정책이 계속 동작해야 함).
        response = json.dumps({"amount": 150000, "reason": "원문 명시"})
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            "지원내용", FakeLLMClient(response), {}, {}
        )
        self.assertEqual(amount, 150000.0)
        self.assertFalse(needs_more_info)
        self.assertIsNone(missing)
        self.assertIsNone(amount_range)

    def test_no_llm_client_behaves_like_before(self) -> None:
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            "월 20만원 지원", None, {}, {}
        )
        self.assertEqual(amount, 200000.0)
        self.assertIn("LLM 미연결", note)
        self.assertFalse(needs_more_info)
        self.assertIsNone(amount_range)


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


def _retrieved_chunk(policy_id: str, text: str) -> RetrievedChunk:
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-chunk-1",
        doc_id=f"subsidy:{policy_id}:v1",
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("지원내용",),
        ordinal=0,
        citation_locator="지원내용",
        content_hash=compute_content_hash(text),
        metadata={"source_id": policy_id, "section_type": "support_details"},
    )
    return RetrievedChunk(
        query_id=f"{policy_id}-recheck",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="subsidy",
    )


class ResolveAmountRealWorldChildrenCountTests(unittest.TestCase):
    """실데이터 "첫만남이용권"(source_id 135200005015) 원문으로 end-to-end
    확인한다 - 지금까지는 children_count가 _TIER_VARIABLE_ENUMS/
    _TIER_ASKABLE_SOFT_FIELDS 어디에도 없어서 이 정책이 절대 되묻지
    않았다(amount=None, needs_more_info=False로 조용히 끝남). 2026-09-11
    확장 이후에는 이 정책도 children_count를 모르면 되묻고, 알면
    정확한 금액을 계산해야 한다."""

    FIRST_MEETING_VOUCHER_TEXT = (
        "출생아로서 출생신고되어 정상적으로 주민등록번호를 부여받은 아동에게 "
        "200만원 이상의 첫만남이용권 지급\n"
        "'24년 1월 1일 이후 출생아는 첫째아 200만원, 둘째아 이상 300만원 지급"
    )

    class _TwoPromptLLM:
        """조건부 규칙 프롬프트와 금액 프롬프트에 각각 다른 canned 응답을
        준다 - 실제 Qwen처럼 두 호출이 서로 다른 질문에 답하는 걸 흉내낸다."""

        def complete(self, prompt, *, system=None, max_tokens=None):
            if "조건부 금액 규칙" in (system or ""):
                return json.dumps({
                    "variable": "children_count",
                    "tiers": [
                        {"match_min": 1, "match_max": 1, "amount": 2000000, "label": "첫째아"},
                        {"match_min": 2, "match_max": None, "amount": 3000000, "label": "둘째아 이상"},
                    ],
                    "reason": "출생순위에 따른 차등",
                })
            return json.dumps(
                {"amount": None, "min_amount": None, "max_amount": None, "reason": "조건부"}
            )

    def test_unknown_children_count_triggers_followup(self) -> None:
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            self.FIRST_MEETING_VOUCHER_TEXT, self._TwoPromptLLM(), {}, {}
        )
        self.assertIsNone(amount)
        self.assertTrue(needs_more_info)
        self.assertEqual(missing, "children_count")
        self.assertIsNone(amount_range)

    def test_known_first_child_resolves_to_two_million(self) -> None:
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            self.FIRST_MEETING_VOUCHER_TEXT, self._TwoPromptLLM(), {"children_count": 1}, {}
        )
        self.assertEqual(amount, 2000000.0)
        self.assertFalse(needs_more_info)
        self.assertIsNone(missing)

    def test_known_third_child_resolves_to_three_million(self) -> None:
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            self.FIRST_MEETING_VOUCHER_TEXT, self._TwoPromptLLM(), {"children_count": 3}, {}
        )
        self.assertEqual(amount, 3000000.0)
        self.assertFalse(needs_more_info)



class FakeStore:
    """rag_design.vector_store.ChromaVectorStore와 같은 search(...) 시그니처를
    갖는 테스트용 대체 구현 (tests/test_graph_nodes.py의 FakeStore와 동일 패턴).
    """

    def __init__(self, chunks_by_policy: dict[str, list[RetrievedChunk]]):
        self._chunks_by_policy = chunks_by_policy

    def search(self, source_type, query, *, query_id, top_k, search_filter, expected_collection_fingerprint=None):
        source_id = search_filter.metadata_equals.get("source_id")
        return tuple(self._chunks_by_policy.get(source_id, ()))


class CalculateBenefitAmountTieredIntegrationTests(unittest.TestCase):
    def test_end_to_end_tiered_amount_with_known_slot(self) -> None:
        policy_id = "policy-tiered-1"
        store = FakeStore({policy_id: [_retrieved_chunk(policy_id, "소득 구간별로 차등 지급")]})
        response = _tiered_response(
            "income_bracket",
            [
                {"match_values": ["under_30", "pct_30_50"], "amount": 300000, "label": "중위소득 50% 이하"},
                {"match_values": ["pct_50_75"], "amount": 200000, "label": "중위소득 50~75%"},
            ],
        )
        state = {
            "query_id": "q1",
            "slots": {"income_bracket": "pct_50_75"},
            "slot_ask_counts": {},
            "eligibility_verdicts": [_verdict(policy_id, "충족")],
            "claim_plan": [_amount_claim(policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, store, FakeLLMClient(response))

        entry = result["benefit_amounts"][0]
        self.assertEqual(entry["amount"], 200000.0)
        self.assertFalse(entry["needs_more_info"])
        self.assertEqual(entry["missing_calc_fields"], [])
        self.assertEqual(result["calc_missing_slots"], [])

    def test_end_to_end_tiered_amount_requests_missing_soft_slot(self) -> None:
        policy_id = "policy-tiered-2"
        store = FakeStore({policy_id: [_retrieved_chunk(policy_id, "혼인 상태에 따라 차등 지급")]})
        response = _tiered_response(
            "marital_status",
            [{"match_values": ["single"], "amount": 100000, "label": "미혼"}],
        )
        state = {
            "query_id": "q2",
            "slots": {},
            "slot_ask_counts": {},
            "eligibility_verdicts": [_verdict(policy_id, "충족")],
            "claim_plan": [_amount_claim(policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, store, FakeLLMClient(response))

        entry = result["benefit_amounts"][0]
        self.assertIsNone(entry["amount"])
        self.assertTrue(entry["needs_more_info"])
        self.assertEqual(entry["missing_calc_fields"], ["marital_status"])
        self.assertEqual(result["calc_missing_slots"], ["marital_status"])

    def test_end_to_end_without_llm_client_is_unaffected(self) -> None:
        # llm_client=None인 기존 호출 방식(tests/test_n10_realchroma.py)이
        # 그대로 동작하는지 - 이 파일이 추가한 로직이 기본 경로를 건드리지
        # 않았는지 재확인한다.
        policy_id = "policy-flat"
        store = FakeStore({policy_id: [_retrieved_chunk(policy_id, "월 20만원 지원")]})
        state = {
            "query_id": "q3",
            "slots": {},
            "slot_ask_counts": {},
            "eligibility_verdicts": [_verdict(policy_id, "충족")],
            "claim_plan": [_amount_claim(policy_id, EvidenceStatus.SUPPORTED)],
        }

        result = calculate_benefit_amount(state, store)

        entry = result["benefit_amounts"][0]
        self.assertEqual(entry["amount"], 200000.0)
        self.assertFalse(entry["needs_more_info"])
        self.assertEqual(result["calc_missing_slots"], [])


class RequestCalcInfoNodeTests(unittest.TestCase):
    def test_raises_on_empty_missing_slots(self) -> None:
        with self.assertRaises(ValueError):
            request_calc_info_input({"calc_missing_slots": []})

    def test_builds_question_and_increments_ask_counts(self) -> None:
        state = {
            "calc_missing_slots": ["marital_status", "household_size"],
            "slot_ask_counts": {"marital_status": 1},
        }
        update = request_calc_info_input(state)

        self.assertTrue(update["needs_input"])
        self.assertIn("혼인 상태", update["followup_question"])
        self.assertIn("가구원 수", update["followup_question"])
        self.assertEqual(update["slot_ask_counts"]["marital_status"], 2)
        self.assertEqual(update["slot_ask_counts"]["household_size"], 1)

    def test_does_not_mutate_original_ask_counts_dict(self) -> None:
        original = {"marital_status": 0}
        state = {"calc_missing_slots": ["marital_status"], "slot_ask_counts": original}
        request_calc_info_input(state)
        self.assertEqual(original["marital_status"], 0)

    def test_unknown_field_falls_back_to_generic_label(self) -> None:
        question = generate_calc_followup_question(["some_future_field"])
        self.assertIn("추가 정보", question)


class CalcSkipResponseTests(unittest.TestCase):
    def test_detects_skip_phrases(self) -> None:
        for phrase in ["그냥 계산해주세요", "모름", "스킵할게요", "말하기 싫어요"]:
            with self.subTest(phrase=phrase):
                self.assertTrue(is_calc_skip_response(phrase))

    def test_does_not_flag_ordinary_answers(self) -> None:
        for phrase in ["기혼입니다", "임신 중이에요", "3명입니다"]:
            with self.subTest(phrase=phrase):
                self.assertFalse(is_calc_skip_response(phrase))


class MergeCalcSlotAnswerTests(unittest.TestCase):
    def test_extracts_marital_status_from_free_text(self) -> None:
        result = merge_calc_slot_answer("기혼이에요", ["marital_status"], {})
        self.assertEqual(result["marital_status"], "married")

    def test_extracts_pregnancy_status_from_free_text(self) -> None:
        result = merge_calc_slot_answer(
            "임신 중이에요", ["pregnancy_status"], {"gender": "female"}
        )
        self.assertEqual(result["pregnancy_status"], "pregnant")
        self.assertEqual(result["gender"], "female")

    def test_ignores_fields_not_in_missing_fields(self) -> None:
        # 답변에 지역 등 다른 정보가 섞여도, missing_fields에 없는 필드는
        # 병합하지 않는다(모듈 docstring 참고 - N1 전체를 대신하지 않는다).
        result = merge_calc_slot_answer(
            "저는 서울 살고 기혼이에요", ["marital_status"], {}
        )
        self.assertEqual(result, {"marital_status": "married"})
        self.assertNotIn("region_scope", result)

    def test_unrecognized_text_leaves_slots_unchanged(self) -> None:
        result = merge_calc_slot_answer(
            "음... 그건 좀", ["marital_status"], {"gender": "female"}
        )
        self.assertEqual(result, {"gender": "female"})

    def test_does_not_mutate_existing_slots_dict(self) -> None:
        original = {"gender": "female"}
        merge_calc_slot_answer("기혼이에요", ["marital_status"], original)
        self.assertEqual(original, {"gender": "female"})


class ApplyCalcSkipTests(unittest.TestCase):
    def test_skip_response_sets_missing_slots_to_unknown(self) -> None:
        state = {"calc_missing_slots": ["marital_status", "pregnancy_status"], "slots": {"gender": "female"}}
        update = {"needs_input": False, "user_input": "모름"}
        result = apply_calc_skip(state, update, "모름")
        self.assertEqual(result["slots"]["marital_status"], UNKNOWN)
        self.assertEqual(result["slots"]["pregnancy_status"], UNKNOWN)
        self.assertEqual(result["slots"]["gender"], "female")
        self.assertEqual(state["slots"], {"gender": "female"})

    def test_non_skip_response_returns_update_unchanged(self) -> None:
        state = {"calc_missing_slots": ["marital_status"], "slots": {}}
        update = {"needs_input": False, "user_input": "기혼입니다"}
        result = apply_calc_skip(state, update, "기혼입니다")
        self.assertIs(result, update)

    def test_skip_response_with_no_missing_slots_returns_update_unchanged(self) -> None:
        state = {"calc_missing_slots": [], "slots": {}}
        update = {"needs_input": False, "user_input": "모름"}
        result = apply_calc_skip(state, update, "모름")
        self.assertIs(result, update)

    def test_already_merged_field_is_not_overwritten_by_skip(self) -> None:
        # merge_calc_slot_answer가 marital_status를 실제 값으로 이미 채운
        # 뒤에도(예: "혼인 상태는 기혼이고 나머지는 모르겠어요"), apply_calc_skip은
        # 그 값을 UNKNOWN으로 덮어쓰지 않고 여전히 비어 있는 슬롯만 채운다.
        state = {"calc_missing_slots": ["marital_status", "pregnancy_status"], "slots": {}}
        resumed = "혼인 상태는 기혼이고 나머지는 모르겠어요"
        merged_slots = merge_calc_slot_answer(resumed, state["calc_missing_slots"], state["slots"])
        update = {"needs_input": False, "user_input": resumed, "slots": merged_slots}

        result = apply_calc_skip(state, update, resumed)

        self.assertEqual(result["slots"]["marital_status"], "married")
        self.assertEqual(result["slots"]["pregnancy_status"], UNKNOWN)


class RouteAfterBenefitCalculatorTests(unittest.TestCase):
    def test_routes_to_request_calc_info_when_slots_missing(self) -> None:
        self.assertEqual(
            route_after_benefit_calculator({"calc_missing_slots": ["marital_status"]}),
            "request_calc_info",
        )

    def test_routes_to_result_assembly_when_nothing_missing(self) -> None:
        self.assertEqual(route_after_benefit_calculator({"calc_missing_slots": []}), "result_assembly")
        self.assertEqual(route_after_benefit_calculator({}), "result_assembly")

    def test_routes_to_request_calc_info_when_only_choices_missing(self) -> None:
        self.assertEqual(
            route_after_benefit_calculator(
                {"calc_missing_choices": [{"policy_id": "p1", "labels": ["A"]}]}
            ),
            "request_calc_info",
        )


def _choice_tiered_response(tiers: list[dict]) -> str:
    return json.dumps({"variable": "other_choice", "tiers": tiers, "reason": "테스트"})


class ExtractChoiceTieredRuleViaLLMTests(unittest.TestCase):
    """2026-09-11 확장: slot_schema 열거형에 없는 자유 선택형(예: 자연분만/
    제왕절개)도 variable="other_choice"로 인식한다."""

    def test_valid_choice_tiers_are_extracted(self) -> None:
        response = _choice_tiered_response(
            [
                {"label": "자연분만", "amount": 300000},
                {"label": "제왕절개", "amount": 500000},
            ]
        )
        rule, note = _extract_tiered_rule_via_llm(
            "분만 방법에 따라 차등 지급", FakeLLMClient(response)
        )
        self.assertIsNotNone(rule)
        self.assertEqual(rule["variable"], "other_choice")
        self.assertEqual(len(rule["tiers"]), 2)
        self.assertEqual(note, "")

    def test_empty_label_is_dropped(self) -> None:
        response = _choice_tiered_response(
            [{"label": "", "amount": 100000}, {"label": "제왕절개", "amount": 500000}]
        )
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(len(rule["tiers"]), 1)
        self.assertEqual(rule["tiers"][0]["label"], "제왕절개")

    def test_duplicate_labels_are_both_dropped(self) -> None:
        response = _choice_tiered_response(
            [
                {"label": "자연분만", "amount": 100000},
                {"label": "자연분만", "amount": 200000},
            ]
        )
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNone(rule)
        self.assertIn("유효한 구간을 하나도", note)

    def test_all_labels_empty_yields_no_rule(self) -> None:
        response = _choice_tiered_response([{"label": "", "amount": 100000}])
        rule, note = _extract_tiered_rule_via_llm("텍스트", FakeLLMClient(response))
        self.assertIsNone(rule)


class SelectTierAmountChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = {
            "variable": "other_choice",
            "tiers": [
                {"label": "자연분만", "amount": 300000.0},
                {"label": "제왕절개", "amount": 500000.0},
            ],
        }

    def test_missing_answer_is_askable_with_labels(self) -> None:
        amount, note, missing = _select_tier_amount(
            self.rule, {}, {}, policy_id="policy-1"
        )
        self.assertIsNone(amount)
        self.assertEqual(
            missing, {"policy_id": "policy-1", "labels": ["자연분만", "제왕절개"]}
        )

    def test_missing_answer_ask_limit_reached_stops_asking(self) -> None:
        amount, note, missing = _select_tier_amount(
            self.rule, {}, {"choice:policy-1": MAX_SLOT_ASKS}, policy_id="policy-1"
        )
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("재질문 상한", note)

    def test_matching_answer_selects_amount(self) -> None:
        amount, note, missing = _select_tier_amount(
            self.rule, {}, {}, policy_id="policy-1", choice_answers={"policy-1": "제왕절개"}
        )
        self.assertEqual(amount, 500000.0)
        self.assertIsNone(missing)

    def test_unknown_sentinel_answer_stops_asking(self) -> None:
        amount, note, missing = _select_tier_amount(
            self.rule, {}, {}, policy_id="policy-1", choice_answers={"policy-1": UNKNOWN}
        )
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("확인하지 못해(미확인)", note)

    def test_answer_not_in_labels_yields_none(self) -> None:
        amount, note, missing = _select_tier_amount(
            self.rule, {}, {}, policy_id="policy-1", choice_answers={"policy-1": "무통분만"}
        )
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("일치하지 않음", note)

    def test_different_policy_answer_does_not_leak_across_policies(self) -> None:
        # policy-2의 답변이 policy-1 계산에 쓰이면 안 된다 - choice_answers는
        # policy_id로 구분된다.
        amount, note, missing = _select_tier_amount(
            self.rule, {}, {}, policy_id="policy-1", choice_answers={"policy-2": "제왕절개"}
        )
        self.assertIsNone(amount)
        self.assertEqual(
            missing, {"policy_id": "policy-1", "labels": ["자연분만", "제왕절개"]}
        )

class ResolveAmountChoiceIntegrationTests(unittest.TestCase):
    def test_missing_choice_surfaces_needs_more_info(self) -> None:
        response = _choice_tiered_response(
            [{"label": "자연분만", "amount": 300000}, {"label": "제왕절개", "amount": 500000}]
        )
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            "분만 방법에 따라 차등 지급",
            FakeLLMClient(response),
            {},
            {},
            policy_id="policy-1",
        )
        self.assertIsNone(amount)
        self.assertTrue(needs_more_info)
        self.assertEqual(
            missing, {"policy_id": "policy-1", "labels": ["자연분만", "제왕절개"]}
        )
        self.assertIsNone(amount_range)

    def test_known_choice_answer_resolves_amount(self) -> None:
        response = _choice_tiered_response(
            [{"label": "자연분만", "amount": 300000}, {"label": "제왕절개", "amount": 500000}]
        )
        amount, note, needs_more_info, missing, amount_range = _resolve_amount(
            "분만 방법에 따라 차등 지급",
            FakeLLMClient(response),
            {},
            {},
            policy_id="policy-1",
            choice_answers={"policy-1": "자연분만"},
        )
        self.assertEqual(amount, 300000.0)
        self.assertFalse(needs_more_info)
        self.assertIsNone(missing)


class CalculateBenefitAmountChoiceIntegrationTests(unittest.TestCase):
    def test_end_to_end_requests_choice_then_resolves_with_answer(self) -> None:
        policy_id = "policy-choice-1"
        store = FakeStore({policy_id: [_retrieved_chunk(policy_id, "분만 방법에 따라 차등 지급")]})
        response = _choice_tiered_response(
            [{"label": "자연분만", "amount": 300000}, {"label": "제왕절개", "amount": 500000}]
        )
        state = {
            "query_id": "q-choice",
            "slots": {},
            "slot_ask_counts": {},
            "calc_choice_answers": {},
            "eligibility_verdicts": [_verdict(policy_id, "충족")],
            "claim_plan": [_amount_claim(policy_id, EvidenceStatus.SUPPORTED)],
        }

        first = calculate_benefit_amount(state, store, FakeLLMClient(response))
        entry = first["benefit_amounts"][0]
        self.assertIsNone(entry["amount"])
        self.assertTrue(entry["needs_more_info"])
        self.assertEqual(first["calc_missing_slots"], [])
        self.assertEqual(
            first["calc_missing_choices"],
            [{"policy_id": policy_id, "labels": ["자연분만", "제왕절개"]}],
        )

        # 사용자가 "제왕절개"라고 답했다고 가정하고 calc_choice_answers를 채운
        # 뒤 다시 계산하면(N9->N10 재실행과 같은 모양) 결정론적으로 금액이 나온다.
        state["calc_choice_answers"] = {policy_id: "제왕절개"}
        second = calculate_benefit_amount(state, store, FakeLLMClient(response))
        entry2 = second["benefit_amounts"][0]
        self.assertEqual(entry2["amount"], 500000.0)
        self.assertFalse(entry2["needs_more_info"])
        self.assertEqual(second["calc_missing_choices"], [])

class RequestCalcInfoChoiceQuestionTests(unittest.TestCase):
    def test_raises_when_both_missing_lists_empty(self) -> None:
        with self.assertRaises(ValueError):
            request_calc_info_input({"calc_missing_slots": [], "calc_missing_choices": []})

    def test_builds_choice_question_with_policy_title(self) -> None:
        state = {
            "calc_missing_slots": [],
            "calc_missing_choices": [{"policy_id": "p1", "labels": ["자연분만", "제왕절개"]}],
            "slot_ask_counts": {},
            "subsidy_chunks": [_retrieved_chunk("p1", "출산지원금\n분만 방법에 따라 차등 지급")],
        }
        update = request_calc_info_input(state)
        self.assertTrue(update["needs_input"])
        self.assertIn("출산지원금", update["followup_question"])
        self.assertIn("자연분만", update["followup_question"])
        self.assertIn("제왕절개", update["followup_question"])
        self.assertEqual(update["slot_ask_counts"]["choice:p1"], 1)

    def test_mixed_slot_and_choice_question(self) -> None:
        state = {
            "calc_missing_slots": ["marital_status"],
            "calc_missing_choices": [{"policy_id": "p1", "labels": ["A", "B"]}],
            "slot_ask_counts": {},
        }
        update = request_calc_info_input(state)
        self.assertIn("1. 혼인 상태", update["followup_question"])
        self.assertIn("2. [p1]", update["followup_question"])


class MergeCalcChoiceAnswerTests(unittest.TestCase):
    def test_substring_match_picks_single_label(self) -> None:
        result = merge_calc_choice_answer(
            "제왕절개로 했어요", [{"policy_id": "p1", "labels": ["자연분만", "제왕절개"]}], {}
        )
        self.assertEqual(result["p1"], "제왕절개")

    def test_ambiguous_substring_without_llm_is_not_matched(self) -> None:
        # 두 라벨이 답변에 동시에 들어가면 조용히 하나를 고르지 않는다 -
        # llm_client가 없으면 매칭 실패로 남는다.
        result = merge_calc_choice_answer(
            "자연분만이랑 제왕절개 둘 다 궁금해요",
            [{"policy_id": "p1", "labels": ["자연분만", "제왕절개"]}],
            {},
        )
        self.assertNotIn("p1", result)

    def test_no_match_and_no_llm_leaves_unanswered(self) -> None:
        result = merge_calc_choice_answer(
            "잘 모르겠어요", [{"policy_id": "p1", "labels": ["자연분만", "제왕절개"]}], {}
        )
        self.assertNotIn("p1", result)

    def test_llm_fallback_matches_paraphrased_answer(self) -> None:
        result = merge_calc_choice_answer(
            "수술로 낳았어요",
            [{"policy_id": "p1", "labels": ["자연분만", "제왕절개"]}],
            {},
            llm_client=FakeLLMClient("제왕절개"),
        )
        self.assertEqual(result["p1"], "제왕절개")

    def test_llm_none_response_leaves_unanswered(self) -> None:
        result = merge_calc_choice_answer(
            "글쎄요",
            [{"policy_id": "p1", "labels": ["자연분만", "제왕절개"]}],
            {},
            llm_client=FakeLLMClient("NONE"),
        )
        self.assertNotIn("p1", result)

    def test_preserves_existing_answers_for_other_policies(self) -> None:
        result = merge_calc_choice_answer(
            "제왕절개요",
            [{"policy_id": "p2", "labels": ["자연분만", "제왕절개"]}],
            {"p1": "자연분만"},
        )
        self.assertEqual(result["p1"], "자연분만")
        self.assertEqual(result["p2"], "제왕절개")

class ApplyCalcSkipChoiceTests(unittest.TestCase):
    def test_skip_response_sets_missing_choice_to_unknown(self) -> None:
        state = {
            "calc_missing_slots": [],
            "calc_missing_choices": [{"policy_id": "p1", "labels": ["A", "B"]}],
            "calc_choice_answers": {},
        }
        update = {"needs_input": False, "user_input": "모름"}
        result = apply_calc_skip(state, update, "모름")
        self.assertEqual(result["calc_choice_answers"]["p1"], UNKNOWN)

    def test_already_answered_choice_is_not_overwritten_by_skip(self) -> None:
        state = {
            "calc_missing_slots": [],
            "calc_missing_choices": [
                {"policy_id": "p1", "labels": ["A", "B"]},
                {"policy_id": "p2", "labels": ["A", "B"]},
            ],
            "calc_choice_answers": {},
        }
        resumed = "p1은 A고 나머지는 모르겠어요"
        update = {
            "needs_input": False,
            "user_input": resumed,
            "calc_choice_answers": {"p1": "A"},
        }
        result = apply_calc_skip(state, update, resumed)
        self.assertEqual(result["calc_choice_answers"]["p1"], "A")
        self.assertEqual(result["calc_choice_answers"]["p2"], UNKNOWN)

    def test_non_skip_response_returns_update_unchanged(self) -> None:
        state = {
            "calc_missing_slots": [],
            "calc_missing_choices": [{"policy_id": "p1", "labels": ["A", "B"]}],
        }
        update = {"needs_input": False, "user_input": "A입니다"}
        result = apply_calc_skip(state, update, "A입니다")
        self.assertIs(result, update)

class ExtractGenderAgeTieredRuleViaLLMTests(unittest.TestCase):
    """2026-09-12 추가: gender(열거형)/age(정수 구간)도 다른 하드 게이트
    변수와 같은 방식으로 인식되는지 확인한다 - 장애인가정 출산지원금
    (성별 조건), 한부모가족 양육비(연령대 조건) 같은 실제 정책이 이
    경로로 계산돼야 한다."""

    def test_gender_enum_rule_is_extracted(self) -> None:
        response = _tiered_response(
            "gender",
            [
                {"match_values": ["male"], "amount": 300000, "label": "부"},
                {"match_values": ["female"], "amount": 500000, "label": "모"},
            ],
        )
        rule, note = _extract_tiered_rule_via_llm("성별에 따라 차등 지급", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(rule["variable"], "gender")
        self.assertEqual(len(rule["tiers"]), 2)

    def test_age_numeric_rule_is_extracted(self) -> None:
        response = _numeric_tiered_response(
            "age",
            [
                {"match_min": 18, "match_max": 34, "amount": 300000, "label": "청년"},
                {"match_min": 65, "match_max": None, "amount": 500000, "label": "노인"},
            ],
        )
        rule, note = _extract_tiered_rule_via_llm("연령대에 따라 차등 지급", FakeLLMClient(response))
        self.assertIsNotNone(rule)
        self.assertEqual(rule["variable"], "age")
        self.assertEqual(len(rule["tiers"]), 2)


class SelectTierAmountGenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = {
            "variable": "gender",
            "tiers": [
                {"match_values": ["male"], "amount": 300000.0, "label": "부"},
                {"match_values": ["female"], "amount": 500000.0, "label": "모"},
            ],
        }

    def test_matching_gender_selects_amount(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"gender": "female"}, {})
        self.assertEqual(amount, 500000.0)
        self.assertIsNone(missing)

    def test_unknown_gender_sentinel_is_not_asked_again(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"gender": "unknown"}, {})
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("확인하지 못해", note)

    def test_missing_gender_is_not_askable_fails_closed(self) -> None:
        # gender는 하드 게이트라 여기 도달한 시점엔 None일 수 없어야 하지만,
        # 방어적으로도 절대 되묻지 않고 바로 확인 불가 처리해야 한다.
        amount, note, missing = _select_tier_amount(self.rule, {}, {})
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("확인할 수 없어", note)


class SelectTierAmountAgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = {
            "variable": "age",
            "tiers": [
                {"match_min": 18, "match_max": 34, "amount": 300000.0, "label": "청년"},
                {"match_min": 65, "match_max": None, "amount": 500000.0, "label": "노인"},
            ],
        }

    def test_age_within_range_selects_amount(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"age": 25}, {})
        self.assertEqual(amount, 300000.0)
        self.assertIsNone(missing)

    def test_age_in_open_ended_tier_selects_amount(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"age": 70}, {})
        self.assertEqual(amount, 500000.0)
        self.assertIsNone(missing)

    def test_age_outside_any_tier_yields_none(self) -> None:
        amount, note, missing = _select_tier_amount(self.rule, {"age": 50}, {})
        self.assertIsNone(amount)
        self.assertIsNone(missing)
        self.assertIn("일치하지 않음", note)

    def test_missing_age_is_not_askable_fails_closed(self) -> None:
        # age는 소프트 슬롯이 아니라 하드 게이트(birth_date)에서 파생된
        # 값이라 _TIER_ASKABLE_SOFT_FIELDS에 없다 - 없어도 새로 묻지 않는다.
        amount, note, missing = _select_tier_amount(self.rule, {}, {})
        self.assertIsNone(amount)
        self.assertIsNone(missing)

if __name__ == "__main__":
    unittest.main()
