from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from rag_chatbot.graph import llm_gateway, retrieval_gateway  # noqa: E402
from rag_chatbot.graph.nodes.general_law_reference_search import (  # noqa: E402
    search_general_law_references,
)
from rag_chatbot.graph.nodes.request_missing_region import (  # noqa: E402
    request_missing_region_input,
)
from rag_chatbot.graph.nodes.slot_completeness_gate import (  # noqa: E402
    check_slot_completeness,
    route_after_slot_completeness,
)
from rag_chatbot.collectors.gov_24.region_utils import extract_region  # noqa: E402
from rag_chatbot.graph.nodes.slot_parser import parse_slots  # noqa: E402
from rag_design.contracts import validate_region_metadata  # noqa: E402


class LlmGatewayExtractSlotsTests(unittest.TestCase):
    def test_extracts_age_region_interests_household_children(self) -> None:
        text = "저는 서울특별시 강남구에 사는 35세이고 4인 가구에 자녀 2명, 육아 지원을 찾아요"
        result = llm_gateway.extract_slots(text, {})
        self.assertEqual(result["age"], 35)
        self.assertEqual(result["region_raw"], "서울특별시 강남구")
        self.assertIn("육아", result["interests"])
        self.assertEqual(result["household_size"], 4)
        self.assertEqual(result["children_count"], 2)

    def test_bare_sido_name_is_captured_without_suffix_words(self) -> None:
        result = llm_gateway.extract_slots("부산 살아요", {})
        self.assertEqual(result["region_raw"], "부산")

    def test_common_words_ending_in_region_suffixes_are_not_false_positives(self) -> None:
        # "제도", "실시" 같은 흔한 단어가 지역명 접미사(도/시)로 끝난다는
        # 이유만으로 지역으로 오인되면 안 된다(회귀 테스트).
        result = llm_gateway.extract_slots("복지제도가 궁금한데 저는 서울특별시 강남구에 살아요", {})
        self.assertEqual(result["region_raw"], "서울특별시 강남구")

        result = llm_gateway.extract_slots("이 제도는 언제부터 실시되나요", {})
        self.assertIsNone(result["region_raw"])

    def test_missing_fields_are_none_or_empty(self) -> None:
        result = llm_gateway.extract_slots("그냥 궁금해서요", {})
        self.assertIsNone(result["age"])
        self.assertIsNone(result["region_raw"])
        self.assertEqual(result["interests"], [])
        self.assertIsNone(result["household_size"])
        self.assertIsNone(result["children_count"])

    def test_email_phone_and_resident_id_are_never_extracted(self) -> None:
        text = "제 번호는 010-1234-5678이고 이메일은 a@b.com, 주민번호는 990101-1234567인데 30세입니다"
        result = llm_gateway.extract_slots(text, {})
        # PII 자체를 슬롯 값으로 반환하지 않는다는 계약만 확인한다(추출 텍스트에
        # 이메일/전화/주민번호 원문이 그대로 등장하지 않음).
        self.assertNotIn("a@b.com", str(result))
        self.assertNotIn("010-1234-5678", str(result))
        self.assertNotIn("990101-1234567", str(result))
        self.assertEqual(result["age"], 30)


class LlmGatewayFollowupQuestionTests(unittest.TestCase):
    def test_mentions_reference_links_only_when_present(self) -> None:
        without_refs = llm_gateway.generate_followup_question(0)
        with_refs = llm_gateway.generate_followup_question(2)
        self.assertNotIn("법령 참고 링크", without_refs)
        self.assertIn("법령 참고 링크", with_refs)


class RetrievalGatewayTests(unittest.TestCase):
    def test_returns_empty_list_before_vector_db_is_wired(self) -> None:
        self.assertEqual(retrieval_gateway.search_general_law_citations("육아"), [])


class ParseSlotsNodeTests(unittest.TestCase):
    def test_first_turn_normalizes_official_region_name(self) -> None:
        state = {"user_input": "서울특별시에 사는 40세 청년입니다", "slots": {}}
        result = parse_slots(state)
        slots = result["slots"]
        self.assertEqual(slots["region_scope"], "regional")
        self.assertEqual(slots["region_names"], ["서울특별시"])
        self.assertEqual(slots["age"], 40)

    def test_sido_alias_is_normalized_to_official_name(self) -> None:
        state = {"user_input": "부산에서 지원금 찾고 있어요", "slots": {}}
        result = parse_slots(state)
        self.assertEqual(result["slots"]["region_scope"], "regional")
        self.assertEqual(result["slots"]["region_names"], ["부산광역시"])

    def test_ambiguous_bare_sigungu_name_stays_unknown(self) -> None:
        state = {"user_input": "중구에 살아요", "slots": {}}
        result = parse_slots(state)
        self.assertEqual(result["slots"]["region_scope"], "unknown")
        self.assertEqual(result["slots"]["region_names"], [])

    def test_national_phrase_maps_to_national_scope(self) -> None:
        state = {"user_input": "전국 어디서나 되는 제도 알려주세요", "slots": {}}
        result = parse_slots(state)
        self.assertEqual(result["slots"]["region_scope"], "national")
        self.assertEqual(result["slots"]["region_names"], ["전국"])

    def test_reentry_keeps_existing_region_when_new_turn_omits_it(self) -> None:
        state = {
            "user_input": "육아 지원도 궁금해요",
            "slots": {
                "region_scope": "regional",
                "region_names": ["서울특별시"],
                "interests": [],
            },
        }
        result = parse_slots(state)
        self.assertEqual(result["slots"]["region_scope"], "regional")
        self.assertEqual(result["slots"]["region_names"], ["서울특별시"])
        self.assertIn("육아", result["slots"]["interests"])

    def test_reentry_overwrites_region_when_user_explicitly_changes_it(self) -> None:
        state = {
            "user_input": "부산으로 이사했어요",
            "slots": {"region_scope": "regional", "region_names": ["서울특별시"]},
        }
        result = parse_slots(state)
        self.assertEqual(result["slots"]["region_names"], ["부산광역시"])

    def test_reentry_resets_to_unknown_when_new_region_text_fails_to_normalize(
        self,
    ) -> None:
        # 이번 턴에 지역처럼 보이는 텍스트가 감지됐지만 정규화에 실패하는
        # 경우, 예전 지역(서울)을 그대로 유지하면 실제로는 바뀐 지역인데 예전
        # 기준으로 검색이 진행된다 - 회귀 테스트. 추출 단계(정규식)의 세부
        # 동작과 무관하게 병합 정책만 검증하려고 extract_slots를 대체한다.
        fake_extracted = {
            "age": None,
            "region_raw": "이상한동네",
            "interests": [],
            "household_size": None,
            "children_count": None,
        }
        state = {
            "user_input": "이상한동네로 이사했어요",
            "slots": {"region_scope": "regional", "region_names": ["서울특별시"]},
        }
        with patch(
            "rag_chatbot.graph.nodes.slot_parser.extract_slots",
            return_value=fake_extracted,
        ):
            result = parse_slots(state)
        self.assertEqual(result["slots"]["region_scope"], "unknown")
        self.assertEqual(result["slots"]["region_names"], [])

    def test_sigungu_region_names_include_the_sido_prefix_first(self) -> None:
        # region_names는 단일 이름이 아니라 상위 시도부터 누적한 계층
        # 리스트여야 한다. 수집기(region_utils.extract_region)와 형태가
        # 어긋나면 N4 지역 필터가 시도 단위 매칭을 놓친다 - 회귀 테스트.
        result = parse_slots({"user_input": "서울 강남구에 삽니다", "slots": {}})
        self.assertEqual(
            result["slots"]["region_names"], ["서울특별시", "서울특별시 강남구"]
        )

    def test_normalized_region_satisfies_the_shared_contract(self) -> None:
        # N1이 만든 슬롯은 공통 validator를 그대로 통과해야 한다 - 회귀 테스트.
        utterances = (
            "서울 강남구에 삽니다",
            "경기도 성남시 분당구 거주",
            "강원도 원주시 삽니다",
            "부산에서 지원금 찾고 있어요",
            "전국 어디서나 되는 제도",
            "중구에 살아요",
        )
        for utterance in utterances:
            with self.subTest(utterance=utterance):
                slots = parse_slots({"user_input": utterance, "slots": {}})["slots"]
                validate_region_metadata(slots["region_scope"], slots["region_names"])

    def test_matches_the_collector_region_names_for_the_same_place(self) -> None:
        # 문서 쪽(수집기)과 슬롯 쪽이 같은 지역을 같은 문자열로 표현해야
        # 지역 필터가 성립한다 - 회귀 테스트.
        pairs = (
            ("서울특별시 강남구청", "서울 강남구에 삽니다"),
            ("경기도 성남시 분당구청", "경기도 성남시 분당구 거주"),
            ("강원특별자치도 원주시청", "강원도 원주시 삽니다"),
        )
        for org_name, utterance in pairs:
            with self.subTest(org_name=org_name):
                slots = parse_slots({"user_input": utterance, "slots": {}})["slots"]
                self.assertEqual(
                    slots["region_names"], extract_region(org_name)["region_names"]
                )

    def test_split_sido_suffix_is_not_read_as_a_sigungu(self) -> None:
        # "부산 광역시"의 "광역시"를 시군구로 오인하면 존재하지 않는
        # "부산광역시 광역시"가 만들어진다 - 회귀 테스트.
        result = parse_slots({"user_input": "부산 광역시 살아요", "slots": {}})
        self.assertEqual(result["slots"]["region_names"], ["부산광역시"])

    def test_legacy_sido_names_are_normalized(self) -> None:
        # 사용자는 개편 전 명칭도 그대로 쓴다. 축약형만 알아들으면 지역이
        # 있는데도 unknown으로 떨어져 N3 재질문이 반복된다 - 회귀 테스트.
        for utterance, expected in (
            ("강원도에 삽니다", "강원특별자치도"),
            ("전라남도 사람입니다", "전남광주통합특별시"),
            ("제주도에서 지원 찾아요", "제주특별자치도"),
        ):
            with self.subTest(utterance=utterance):
                slots = parse_slots({"user_input": utterance, "slots": {}})["slots"]
                self.assertEqual(slots["region_names"], [expected])

    def test_irregular_spacing_does_not_drop_the_region(self) -> None:
        # 공백 오타 하나로 지역이 통째로 사라지면 안 된다 - 회귀 테스트.
        result = parse_slots({"user_input": "서울  강남구에 삽니다", "slots": {}})
        self.assertEqual(
            result["slots"]["region_names"], ["서울특별시", "서울특별시 강남구"]
        )

    def test_does_not_return_list_objects_shared_with_the_input_state(self) -> None:
        # 반환한 리스트를 하위 노드가 in-place로 바꾸면 checkpointer가 든
        # 과거 스냅샷까지 오염된다 - 회귀 테스트.
        existing = {
            "interests": ["의료"],
            "region_scope": "regional",
            "region_names": ["서울특별시"],
        }
        merged = parse_slots({"user_input": "안녕하세요", "slots": existing})["slots"]
        self.assertIsNot(merged["interests"], existing["interests"])
        self.assertIsNot(merged["region_names"], existing["region_names"])

    def test_reentry_merges_interests_instead_of_replacing_them(self) -> None:
        # 이전 턴에서 언급한 관심사가 이후 턴에서 다른 관심사를 말했다고 해서
        # 사라지면 안 된다 - 회귀 테스트.
        first = parse_slots({"user_input": "의료 지원 궁금해요", "slots": {}})
        second = parse_slots({"user_input": "육아 지원도 궁금해요", "slots": first["slots"]})
        self.assertEqual(second["slots"]["interests"], ["의료", "육아"])


class SlotCompletenessGateTests(unittest.TestCase):
    def test_missing_region_scope_is_insufficient(self) -> None:
        state = {"slots": {}}
        result = check_slot_completeness(state)
        self.assertEqual(result["missing_slots"], ["region"])
        self.assertEqual(route_after_slot_completeness({**state, **result}), "insufficient")

    def test_unknown_region_scope_is_insufficient(self) -> None:
        state = {"slots": {"region_scope": "unknown", "region_names": []}}
        result = check_slot_completeness(state)
        self.assertEqual(result["missing_slots"], ["region"])

    def test_national_or_regional_scope_is_sufficient(self) -> None:
        for scope, names in (("national", ["전국"]), ("regional", ["서울특별시"])):
            state = {"slots": {"region_scope": scope, "region_names": names}}
            result = check_slot_completeness(state)
            self.assertEqual(result["missing_slots"], [])
            self.assertEqual(
                route_after_slot_completeness({**state, **result}), "sufficient"
            )

    def test_unrecognized_region_scope_is_insufficient(self) -> None:
        # 계약에 없는 값을 "충분"으로 통과시키면 지역이 확정되지 않은 채
        # N4 검색으로 넘어간다 - 회귀 테스트.
        for scope in ("", "seoul", "REGIONAL", 0):
            with self.subTest(scope=scope):
                state = {"slots": {"region_scope": scope, "region_names": []}}
                self.assertEqual(
                    check_slot_completeness(state)["missing_slots"], ["region"]
                )

    def test_regional_scope_without_region_names_is_insufficient(self) -> None:
        # 계약상 regional은 region_names가 비어 있을 수 없다 - 회귀 테스트.
        state = {"slots": {"region_scope": "regional", "region_names": []}}
        self.assertEqual(check_slot_completeness(state)["missing_slots"], ["region"])

    def test_soft_slots_do_not_affect_the_hard_gate(self) -> None:
        state = {
            "slots": {
                "region_scope": "regional",
                "region_names": ["서울특별시"],
                "age": None,
                "interests": [],
                "household_size": None,
                "children_count": None,
            }
        }
        self.assertEqual(check_slot_completeness(state)["missing_slots"], [])


class GeneralLawReferenceSearchNodeTests(unittest.TestCase):
    def test_returns_empty_when_no_query_material(self) -> None:
        state = {"slots": {"interests": []}, "user_input": ""}
        result = search_general_law_references(state)
        self.assertEqual(result["general_law_references"], [])

    def test_builds_a_query_from_interests_and_user_input(self) -> None:
        state = {"slots": {"interests": ["육아"]}, "user_input": "지원 궁금해요"}
        result = search_general_law_references(state)
        # 실제 색인이 없는 현재 단계에서는 항상 빈 리스트를 반환해야 한다
        # (근거 없는 법령 인용을 지어내지 않음).
        self.assertEqual(result["general_law_references"], [])

    def test_query_never_carries_pii_to_the_retrieval_gateway(self) -> None:
        # 쿼리는 embedding provider와 검색 로그로 나가므로 원문이 아니라
        # 마스킹된 텍스트여야 한다 - 회귀 테스트.
        state = {
            "slots": {"interests": ["육아"]},
            "user_input": "010-1234-5678, a@b.com, 990101-1234567 인데 육아 지원",
        }
        with patch(
            "rag_chatbot.graph.nodes.general_law_reference_search"
            ".search_general_law_citations",
            return_value=[],
        ) as spy:
            search_general_law_references(state)
        query = spy.call_args.args[0]
        self.assertNotIn("a@b.com", query)
        self.assertNotIn("010-1234-5678", query)
        self.assertNotIn("990101-1234567", query)
        self.assertIn("육아", query)


class RequestMissingRegionNodeTests(unittest.TestCase):
    def test_raises_when_region_is_not_actually_missing(self) -> None:
        with self.assertRaises(ValueError):
            request_missing_region_input({"missing_slots": []})

    def test_asks_for_region_and_flags_needs_input(self) -> None:
        state = {"missing_slots": ["region"], "general_law_references": []}
        result = request_missing_region_input(state)
        self.assertTrue(result["needs_input"])
        self.assertIn("지역", result["followup_question"])

    def test_mentions_reference_links_when_general_law_references_present(self) -> None:
        state = {"missing_slots": ["region"], "general_law_references": [object()]}
        result = request_missing_region_input(state)
        self.assertIn("법령 참고 링크", result["followup_question"])


if __name__ == "__main__":
    unittest.main()
