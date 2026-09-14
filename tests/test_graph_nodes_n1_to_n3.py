from __future__ import annotations

import json
import sys
import unittest
from datetime import date
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
from rag_chatbot.graph.nodes.request_missing_slots import (  # noqa: E402
    request_missing_slot_input,
)
from rag_chatbot.graph.nodes.slot_completeness_gate import (  # noqa: E402
    check_slot_completeness,
    needs_general_law_reference,
    route_after_slot_completeness,
)
from rag_chatbot.llm import FailingLLMClient, FakeLLMClient  # noqa: E402
from rag_chatbot.graph.slot_schema import (  # noqa: E402
    EmploymentStatus,
    HARD_GATE_SLOTS,
    SKIP_NOT_CONFIRMED,
    SKIP_SUBJECT_NOT_SELF,
    HARD_FILTER_SLOTS,
    MAX_SLOT_ASKS,
    SOFT_FILTER_SLOTS,
    calculate_ages,
    parse_birth_date,
    resolve_filter_slots,
)
from rag_chatbot.collectors.gov_24.region_utils import extract_region  # noqa: E402
from rag_chatbot.graph.nodes.slot_parser import parse_slots  # noqa: E402
from rag_design.contracts import validate_region_metadata  # noqa: E402


class LlmGatewayExtractSlotsTests(unittest.TestCase):
    def test_extracts_age_region_interests_household_children(self) -> None:
        text = "저는 서울특별시 강남구에 사는 35세이고 4인 가구에 자녀 2명, 육아 지원을 찾아요"
        result = llm_gateway.extract_slots(text, {})
        self.assertEqual(result["age_self_reported"], 35)
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
        self.assertIsNone(result["age_self_reported"])
        self.assertIsNone(result["birth_date"])
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
        self.assertEqual(result["age_self_reported"], 30)
        # 주민등록번호 앞 6자리에서 생년월일을 역산하지 않는다.
        self.assertIsNone(result["birth_date"])


class LlmGatewayAnswerContextTests(unittest.TestCase):
    """N3가 되물은 직후의 단답을 알아듣는지 (2026-08-31 추가).

    실제 사용자 테스트에서 "서울, 2000-03-26, 여성, 모름, 비장애인, 무직"
    이라고 답했는데 gender/income_bracket/employment_status가 하나도 안
    채워졌다. 자기서술 마커("입니다"/"이에요")를 요구하는 규칙 때문인데,
    되묻기에 대한 답은 원래 단답이라 마커가 붙을 자리가 없다.
    """

    ASKED_ALL = [
        "region",
        "birth_date",
        "gender",
        "income_bracket",
        "disability_status",
        "employment_status",
    ]

    def test_comma_separated_answer_fills_every_asked_slot(self) -> None:
        result = llm_gateway.extract_slots(
            "서울, 2000-03-26, 여성, 모름, 비장애인, 무직, ",
            {},
            asked_slots=self.ASKED_ALL,
        )
        self.assertEqual(result["region_raw"], "서울")
        self.assertEqual(result["birth_date"], "2000-03-26")
        self.assertEqual(result["gender"], "female")
        self.assertEqual(result["disability_status"], "not_registered")
        self.assertEqual(result["employment_status"], "not_working")
        # "모름"이라고 답한 항목은 센티넬로 확정해서 되묻기를 끝낸다.
        self.assertEqual(result["income_bracket"], "unknown")

    def test_short_answers_with_typos_and_particles(self) -> None:
        # "여자", "무직임"처럼 조사/오타가 섞인 단답도 알아들어야 한다.
        result = llm_gateway.extract_slots(
            "여자, 소둑수준 모름, 무직임",
            {},
            asked_slots=["gender", "income_bracket", "employment_status"],
        )
        self.assertEqual(result["gender"], "female")
        self.assertEqual(result["employment_status"], "not_working")
        self.assertEqual(result["income_bracket"], "unknown")

    def test_non_disabled_answer_is_not_read_as_registered(self) -> None:
        # "비장애인"이 장애인으로 읽히면 정반대 판정이 된다 - 회귀 테스트.
        for text in ("비장애인", "장애 없어요", "장애는 없습니다"):
            with self.subTest(text=text):
                result = llm_gateway.extract_slots(
                    text, {}, asked_slots=["disability_status"]
                )
                self.assertEqual(result["disability_status"], "not_registered")

    def test_negated_keywords_never_become_search_interests(self) -> None:
        # 실제 버그: "비장애인"이라고 답했더니 관심사에 "장애인"이 잡히고,
        # N4 검색 질의가 관심사만으로 만들어지는 탓에 질의가 그대로
        # "장애인"이 되어 장애인 정책만 5건 추천됐다. 사용자가 아니라고 말한
        # 조건으로 검색이 돌면 안 된다 - 회귀 테스트.
        for text in ("비장애인", "장애인 아니에요", "장애인이 아녜요", "노인 아닙니다"):
            with self.subTest(text=text):
                self.assertEqual(llm_gateway.extract_slots(text, {})["interests"], [])

    def test_positive_keyword_mentions_are_still_interests(self) -> None:
        # 부정 처리가 과해서 정상적인 관심사까지 지워버리면 검색이 넓어진다.
        self.assertEqual(
            llm_gateway.extract_slots("장애인 지원 알려줘", {})["interests"], ["장애인"]
        )
        self.assertEqual(
            llm_gateway.extract_slots("등록장애인입니다", {})["interests"], ["장애인"]
        )
        self.assertIn("육아", llm_gateway.extract_slots("육아랑 주거 지원", {})["interests"])

    def test_marker_requirement_still_protects_free_form_questions(self) -> None:
        # 되묻기 맥락이 아닐 때(asked_slots 없음)는 예전 보호가 그대로다.
        # "주거급여 얼마 받나요"는 질문이지 수급 사실이 아니다.
        self.assertIsNone(llm_gateway.extract_slots("주거급여 얼마 받나요?", {})["income_bracket"])
        self.assertIsNone(
            llm_gateway.extract_slots("여성 대상 지원 제도 뭐 있나요?", {})["gender"]
        )

    def test_dont_know_is_ignored_without_an_asked_slot_context(self) -> None:
        # 되묻지도 않았는데 "모르겠는데"가 나왔다고 슬롯을 센티넬로 확정하면
        # 사용자가 답할 기회를 잃는다.
        result = llm_gateway.extract_slots("잘 모르겠는데 지원 뭐 있어요?", {})
        for field in ("gender", "income_bracket", "disability_status", "employment_status"):
            self.assertIsNone(result[field], field)

    def test_dont_know_only_fills_slots_that_were_not_answered(self) -> None:
        # "모름"이 어느 항목인지 규칙으로는 모르지만, 다른 항목이 정상적으로
        # 뽑혔다면 그 값은 덮어쓰지 않는다.
        result = llm_gateway.extract_slots(
            "여자, 나머지는 모름", {}, asked_slots=["gender", "employment_status"]
        )
        self.assertEqual(result["gender"], "female")
        self.assertEqual(result["employment_status"], "unknown")


class LlmGatewayLlmSlotExtractionTests(unittest.TestCase):
    """N1이 LLM으로 슬롯을 채우는 경로 (2026-08-31 추가)."""

    def test_llm_fills_what_the_rule_based_extractor_cannot_understand(self) -> None:
        # "1955년 3월생"은 일자가 없어서 규칙 정규식이 못 잡는다. 실제로
        # 사용자가 이렇게 답했는데 슬롯이 안 채워져서, N2가 되묻기 상한에
        # 닿아 birth_date를 unknown으로 확정해버리는 문제가 있었다.
        text = "1955년 3월생이에요"
        self.assertIsNone(llm_gateway.extract_slots(text, {})["birth_date"])

        client = FakeLLMClient(json.dumps({"birth_date": "1955-03-01"}))
        result = llm_gateway.extract_slots(text, {}, llm_client=client)
        self.assertEqual(result["birth_date"], "1955-03-01")

    def test_llm_failure_falls_back_to_the_rule_based_result(self) -> None:
        # LLM이 죽어도 그래프는 끝까지 돌아야 한다.
        result = llm_gateway.extract_slots(
            "1990년 3월 15일생이고 서울 살아요", {}, llm_client=FailingLLMClient()
        )
        self.assertEqual(result["birth_date"], "1990-03-15")
        self.assertEqual(result["region_raw"], "서울")

    def test_values_outside_the_contract_are_dropped_fail_closed(self) -> None:
        # 계약에 없는 값을 그대로 저장하면 N2 게이트가 "채워졌음"으로 읽고
        # 검증되지 않은 값으로 판정이 진행된다.
        client = FakeLLMClient(
            json.dumps(
                {
                    "gender": "female_maybe",  # 계약에 없는 값
                    "birth_date": "3000-01-01",  # 미래 날짜
                    "employment_status": "student",  # 유효
                }
            )
        )
        result = llm_gateway.extract_slots("학생이에요", {}, llm_client=client)
        self.assertIsNone(result["gender"])
        self.assertIsNone(result["birth_date"])
        self.assertEqual(result["employment_status"], "student")

    def test_json_wrapped_in_prose_or_code_fence_is_still_parsed(self) -> None:
        # 모델이 코드펜스나 앞뒤 설명을 붙여 답하는 건 흔한 일이라, 그것만으로
        # 슬롯을 통째로 잃으면 안 된다.
        client = FakeLLMClient('설명입니다.\n```json\n{"region_raw": "부산"}\n```\n끝')
        result = llm_gateway.extract_slots("어디 사는지 물으셨죠", {}, llm_client=client)
        self.assertEqual(result["region_raw"], "부산")

    def test_llm_is_not_called_when_rules_already_answered_everything(self) -> None:
        """규칙이 물어본 항목을 다 채웠으면 LLM을 부르지 않는다.

        되묻기 답변은 규칙만으로 다 잡히는 경우가 많은데, 그런 턴에도 LLM을
        부르면 아무것도 더 얻지 못하면서 호출 시간만 그대로 든다.
        실측에서 N1 한 번이 50초였다(추론형 모델 호출 1회).
        """

        client = FakeLLMClient(json.dumps({"gender": "male"}))
        result = llm_gateway.extract_slots(
            "그건 잘 모르겠어요", {}, llm_client=client, asked_slots=["income_bracket"]
        )
        # 규칙이 "모름"을 센티넬로 확정했으므로 LLM은 부르지 않는다.
        self.assertEqual(result["income_bracket"], "unknown")
        self.assertEqual(client.calls, [])

    def test_llm_is_still_called_when_rules_fell_short(self) -> None:
        # 규칙이 못 채운 항목이 남아 있으면 LLM에게 맡긴다. 이때 직전에
        # 물어본 항목이 프롬프트에 들어가야 "모름"이 어느 슬롯에 대한
        # 답인지 LLM이 판단할 수 있다.
        client = FakeLLMClient(json.dumps({"income_bracket": "unknown"}))
        # "글쎄"는 규칙의 '모름' 표현 목록에 있어서 규칙이 처리해버린다.
        # 규칙이 아무것도 못 잡는 발화를 써야 LLM 경로를 검증할 수 있다.
        result = llm_gateway.extract_slots(
            "네 그렇습니다", {}, llm_client=client, asked_slots=["income_bracket"]
        )
        self.assertEqual(len(client.calls), 1)
        self.assertIn("income_bracket", client.calls[0]["prompt"])
        self.assertEqual(result["income_bracket"], "unknown")

    def test_first_free_form_turn_always_asks_the_llm(self) -> None:
        # 되묻기 맥락이 없는 첫 발화는 규칙이 뭘 잡았든 LLM에게 물어본다.
        # 규칙이 못 읽는 문장을 이해하는 것이 LLM을 붙인 이유다.
        client = FakeLLMClient("{}")
        llm_gateway.extract_slots("혼자 사는데 월세가 부담돼요", {}, llm_client=client)
        self.assertEqual(len(client.calls), 1)

    def test_prompt_never_carries_email_phone_or_resident_id(self) -> None:
        # 외부 LLM provider로 나가는 텍스트에 PII가 실리면 안 된다.
        client = FakeLLMClient("{}")
        llm_gateway.extract_slots(
            "제 번호는 010-1234-5678, 이메일 a@b.com, 주민번호 990101-1234567입니다",
            {},
            llm_client=client,
        )
        prompt = client.calls[0]["prompt"]
        self.assertNotIn("010-1234-5678", prompt)
        self.assertNotIn("a@b.com", prompt)
        self.assertNotIn("990101-1234567", prompt)

    def test_prompt_enum_choices_are_generated_from_the_contract(self) -> None:
        # 프롬프트에 값 목록을 손으로 적어두면 계약이 바뀔 때 조용히 어긋나고,
        # LLM이 계약에 없는 값만 내놓아 전부 버려진다 - 회귀 테스트.
        prompt = llm_gateway.build_slot_extraction_prompt("테스트 발화")
        for member in EmploymentStatus:
            self.assertIn(f'"{member.value}"', prompt)

    def test_age_subject_signals_are_merged_not_replaced(self) -> None:
        # 규칙이 잡은 신호를 LLM 응답이 덮어써서 없애면, 41세 학부모의
        # "우리 아이 지원" 질문에 본인 나이로 필터가 걸려 아동 제도가 전부
        # 탈락한다(참고자료 §8). 어느 한쪽만 감지해도 살린다.
        client = FakeLLMClient(
            json.dumps({"age_subject_signals": {"self": False, "child": False, "other": True}})
        )
        result = llm_gateway.extract_slots(
            "우리 아이 지원 뭐 있나요", {}, llm_client=client
        )
        signals = result["age_subject_signals"]
        self.assertTrue(signals["child"])  # 규칙이 잡은 신호가 살아있다
        self.assertTrue(signals["other"])  # LLM이 준 신호도 함께 반영된다

    def test_multi_value_fields_are_merged_as_a_union(self) -> None:
        client = FakeLLMClient(json.dumps({"interests": ["주거"]}))
        result = llm_gateway.extract_slots("육아 지원 알려줘", {}, llm_client=client)
        self.assertIn("육아", result["interests"])  # 규칙이 뽑은 값
        self.assertIn("주거", result["interests"])  # LLM이 뽑은 값


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
        # 자기신고 숫자는 age가 아니라 age_self_reported에만 남는다.
        self.assertEqual(slots["age_self_reported"], 40)
        self.assertIsNone(slots["age"])

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

    def test_llm_client_is_threaded_through_and_derives_age(self) -> None:
        # N1이 llm_client를 실제로 gateway까지 넘기는지, 그리고 LLM이 채운
        # 생년월일에서 만 나이 파생까지 정상적으로 이어지는지 확인한다.
        client = FakeLLMClient(json.dumps({"birth_date": "1955-03-01"}))
        state = {
            "user_input": "1955년 3월생이에요",
            "slots": {},
            "missing_slots": ["birth_date"],
        }

        slots = parse_slots(state, llm_client=client)["slots"]

        self.assertEqual(slots["birth_date"], "1955-03-01")
        # 나이는 사용자가 말한 숫자가 아니라 생년월일에서 파생된 값이다.
        expected_age, _ = calculate_ages(date(1955, 3, 1), date.today())
        self.assertEqual(slots["age"], expected_age)
        # 직전에 물어본 슬롯이 프롬프트 맥락으로 전달됐는지도 확인한다.
        self.assertIn("birth_date", client.calls[0]["prompt"])

    def test_age_uses_the_graph_as_of_across_the_year_boundary(self) -> None:
        state = {
            "user_input": "",
            "slots": {"birth_date": "2000-01-01"},
        }

        before_midnight = parse_slots({**state, "as_of": date(2025, 12, 31)})[
            "slots"
        ]
        after_midnight = parse_slots({**state, "as_of": date(2026, 1, 1)})[
            "slots"
        ]

        self.assertEqual(before_midnight["age"], 25)
        self.assertEqual(before_midnight["age_ref_date"], "2025-12-31")
        self.assertEqual(after_midnight["age"], 26)
        self.assertEqual(after_midnight["age_ref_date"], "2026-01-01")

    def test_graph_as_of_is_shared_by_parser_gate_and_filter(self) -> None:
        reference_date = date(2026, 1, 1)
        profile_without_age = {
            key: value
            for key, value in _FILTER_READY_SLOTS.items()
            if key not in {"birth_date", "age", "age_year_based"}
        }
        slots = parse_slots(
            {
                "user_input": "1905년 1월 2일생입니다",
                "slots": profile_without_age,
                "as_of": reference_date,
            }
        )["slots"]
        state = {"slots": slots, "as_of": reference_date}

        self.assertNotIn("birth_date", check_slot_completeness(state)["missing_slots"])
        self.assertIn(
            "birth_date",
            resolve_filter_slots(slots, reference_date=reference_date)["hard"],
        )

        future_slots = parse_slots(
            {
                "user_input": "2026년 1월 2일생입니다",
                "slots": profile_without_age,
                "as_of": reference_date,
            }
        )["slots"]
        future_state = {"slots": future_slots, "as_of": reference_date}
        self.assertIn(
            "birth_date", check_slot_completeness(future_state)["missing_slots"]
        )
        self.assertNotIn(
            "birth_date",
            resolve_filter_slots(future_slots, reference_date=reference_date)["hard"],
        )

    def test_llm_birth_date_validation_uses_the_graph_as_of(self) -> None:
        reference_date = date(2026, 1, 1)
        client = FakeLLMClient(json.dumps({"birth_date": "1905-01-02"}))

        slots = parse_slots(
            {
                "user_input": "생년월일을 답했습니다",
                "slots": {},
                "missing_slots": ["birth_date"],
                "as_of": reference_date,
            },
            llm_client=client,
        )["slots"]

        self.assertEqual(slots["birth_date"], "1905-01-02")
        self.assertEqual(slots["age"], 120)
        self.assertEqual(slots["age_ref_date"], "2026-01-01")

    def test_invalid_graph_as_of_does_not_silently_fall_back_to_today(self) -> None:
        with self.assertRaisesRegex(ValueError, "state\\['as_of'\\] must be a date"):
            parse_slots({"user_input": "", "slots": {}, "as_of": "2026-01-01"})

    def test_first_question_is_preserved_for_the_search_query(self) -> None:
        # user_input은 되묻기에 답할 때마다 덮어써진다. N4 검색에 쓸 원래
        # 질문을 첫 턴에 한 번만 보존하고, 이후 턴에는 건드리지 않는다.
        first = parse_slots({"user_input": "아이 키우는데 지원 뭐 있나요", "slots": {}})
        self.assertEqual(first["initial_user_input"], "아이 키우는데 지원 뭐 있나요")

        later = parse_slots(
            {
                "user_input": "서울, 2000-03-26, 여성",
                "slots": {},
                "initial_user_input": "아이 키우는데 지원 뭐 있나요",
            }
        )
        self.assertNotIn("initial_user_input", later)

    def test_without_llm_client_behaves_exactly_as_before(self) -> None:
        # LLM을 안 붙여도 규칙 기반으로 그대로 동작해야 한다(기존 배포 경로).
        state = {"user_input": "1990년 3월 15일생 서울 살아요", "slots": {}}
        slots = parse_slots(state)["slots"]
        self.assertEqual(slots["birth_date"], "1990-03-15")
        self.assertEqual(slots["region_names"], ["서울특별시"])

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
            "birth_date": None,
            "age_self_reported": None,
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


_FULL_HARD_GATE_SLOTS = {
    "region_scope": "regional",
    "region_names": ["서울특별시"],
    "birth_date": "1990-03-15",
    "gender": "female",
    "income_bracket": "pct_50_75",
    "disability_status": "not_registered",
    "employment_status": "employed",
}


_FILTER_READY_SLOTS = {
    **_FULL_HARD_GATE_SLOTS,
    "age_subject": "self",
    "region_names": ["서울특별시", "서울특별시 강남구"],
    "age": 36,
    "age_year_based": 36,
}


class SlotCompletenessGateTests(unittest.TestCase):
    def test_missing_region_scope_is_insufficient(self) -> None:
        state = {"slots": {}}
        result = check_slot_completeness(state)
        self.assertIn("region", result["missing_slots"])
        self.assertEqual(route_after_slot_completeness({**state, **result}), "insufficient")

    def test_unknown_region_scope_is_insufficient(self) -> None:
        state = {"slots": {"region_scope": "unknown", "region_names": []}}
        result = check_slot_completeness(state)
        self.assertIn("region", result["missing_slots"])

    def test_national_or_regional_scope_satisfies_the_region_slot(self) -> None:
        for scope, names in (("national", ["전국"]), ("regional", ["서울특별시"])):
            slots = {**_FULL_HARD_GATE_SLOTS, "region_scope": scope, "region_names": names}
            state = {"slots": slots}
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
                slots = {**_FULL_HARD_GATE_SLOTS, "region_scope": scope, "region_names": []}
                self.assertIn(
                    "region", check_slot_completeness({"slots": slots})["missing_slots"]
                )

    def test_regional_scope_without_region_names_is_insufficient(self) -> None:
        # 계약상 regional은 region_names가 비어 있을 수 없다 - 회귀 테스트.
        slots = {**_FULL_HARD_GATE_SLOTS, "region_scope": "regional", "region_names": []}
        self.assertIn("region", check_slot_completeness({"slots": slots})["missing_slots"])

    def test_soft_slots_do_not_affect_the_hard_gate(self) -> None:
        state = {
            "slots": {
                **_FULL_HARD_GATE_SLOTS,
                "marital_status": None,
                "household_types": [],
                "pregnancy_status": None,
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


class RequestMissingSlotNodeTests(unittest.TestCase):
    def test_raises_when_nothing_is_actually_missing(self) -> None:
        with self.assertRaises(ValueError):
            request_missing_slot_input({"missing_slots": []})

    def test_asks_for_region_and_flags_needs_input(self) -> None:
        state = {"missing_slots": ["region"], "general_law_references": []}
        result = request_missing_slot_input(state)
        self.assertTrue(result["needs_input"])
        self.assertIn("지역", result["followup_question"])

    def test_mentions_reference_links_when_general_law_references_present(self) -> None:
        state = {"missing_slots": ["region"], "general_law_references": [object()]}
        result = request_missing_slot_input(state)
        self.assertIn("법령 참고 링크", result["followup_question"])

    def test_asks_every_missing_slot_at_once_including_region(self) -> None:
        # 2026-08-31 변경: 예전에는 지역이 섞여 있으면 지역만 먼저 물었다.
        # 되묻기 왕복이 두 배로 늘어 대화가 길어지고, 슬롯별 상한
        # (MAX_SLOT_ASKS)에 먼저 닿아 값이 unknown으로 확정돼버리는 문제가
        # 더 커서 한 번에 모아 묻도록 바꿨다 - 회귀 테스트.
        state = {"missing_slots": ["region", "gender", "birth_date"]}
        result = request_missing_slot_input(state)
        question = result["followup_question"]
        self.assertIn("지역", question)
        self.assertIn("성별", question)
        self.assertIn("생년월일", question)
        self.assertEqual(
            result["slot_ask_counts"], {"region": 1, "gender": 1, "birth_date": 1}
        )

    def test_question_orders_slots_by_contract_not_by_caller_order(self) -> None:
        # 호출자가 어떤 순서로 넘기든 지역 -> 프로필 순서로 묻는다.
        state = {"missing_slots": ["employment_status", "birth_date", "region"]}
        question = request_missing_slot_input(state)["followup_question"]
        self.assertLess(question.index("거주 지역"), question.index("생년월일"))
        self.assertLess(question.index("생년월일"), question.index("취업 상태"))

    def test_asks_profile_slots_together_once_region_is_settled(self) -> None:
        state = {"missing_slots": ["birth_date", "gender", "income_bracket"]}
        result = request_missing_slot_input(state)
        question = result["followup_question"]
        self.assertIn("생년월일", question)
        self.assertIn("성별", question)
        self.assertIn("소득", question)
        self.assertEqual(
            result["slot_ask_counts"],
            {"birth_date": 1, "gender": 1, "income_bracket": 1},
        )

    def test_ask_counts_accumulate_without_mutating_the_input_state(self) -> None:
        # 반환한 dict을 in-place로 바꾸면 checkpointer가 든 과거 스냅샷까지
        # 오염된다 - 회귀 테스트.
        existing = {"gender": 1}
        state = {"missing_slots": ["gender"], "slot_ask_counts": existing}
        result = request_missing_slot_input(state)
        self.assertEqual(result["slot_ask_counts"], {"gender": 2})
        self.assertEqual(existing, {"gender": 1})


class BirthDateAndAgeTests(unittest.TestCase):
    def test_birth_date_is_extracted_from_common_korean_formats(self) -> None:
        for text, expected in (
            ("1990-03-15 생입니다", "1990-03-15"),
            ("1990년 3월 15일에 태어났어요", "1990-03-15"),
            ("생년월일은 1990.3.15 입니다", "1990-03-15"),
            ("2001/12/05 생", "2001-12-05"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    llm_gateway.extract_slots(text, {})["birth_date"], expected
                )

    def test_six_digit_shorthand_is_not_read_as_a_birth_date(self) -> None:
        # "900101"은 세기가 모호하고 주민등록번호 앞자리와 구분되지 않는다.
        # 잘못 읽은 생년월일은 그대로 만 나이가 되어 자격 판정을 뒤집는다.
        self.assertIsNone(llm_gateway.extract_slots("900101 생이에요", {})["birth_date"])

    def test_impossible_and_future_dates_are_rejected(self) -> None:
        for text in ("1990-02-31 생", "2099-01-01 생", "1800-01-01 생"):
            with self.subTest(text=text):
                self.assertIsNone(llm_gateway.extract_slots(text, {})["birth_date"])

    def test_korean_age_is_computed_from_the_birth_date_not_the_spoken_number(
        self,
    ) -> None:
        # 사용자가 말한 "35세"는 세는 나이일 수 있다. 만 나이는 항상
        # 생년월일에서 파생해야 경계에서 오판정이 나지 않는다 - 회귀 테스트.
        slots = parse_slots(
            {"user_input": "1990-03-15 생이고 35세입니다", "slots": {}}
        )["slots"]
        expected_age, expected_year_age = calculate_ages(date(1990, 3, 15), date.today())
        self.assertEqual(slots["age"], expected_age)
        self.assertEqual(slots["age_year_based"], expected_year_age)
        self.assertEqual(slots["age_self_reported"], 35)
        self.assertEqual(slots["age_ref_date"], date.today().isoformat())

    def test_man_age_and_year_age_differ_before_the_birthday(self) -> None:
        # 만 나이 하나만 들고 있으면 출생연도 기준 청년 정책에서 경계에 있는
        # 사람이 조용히 탈락한다(참고자료 §8).
        self.assertEqual(calculate_ages(date(2000, 12, 31), date(2026, 1, 1)), (25, 26))
        self.assertEqual(calculate_ages(date(2000, 1, 1), date(2026, 1, 1)), (26, 26))

    def test_leap_day_birth_ages_on_march_first_in_non_leap_years(self) -> None:
        self.assertEqual(calculate_ages(date(2000, 2, 29), date(2025, 2, 28))[0], 24)
        self.assertEqual(calculate_ages(date(2000, 2, 29), date(2025, 3, 1))[0], 25)

    def test_derived_age_is_cleared_when_no_birth_date_is_known(self) -> None:
        # 근거 없는 나이로 판정이 진행되면 안 된다 - 회귀 테스트.
        slots = parse_slots({"user_input": "35세입니다", "slots": {}})["slots"]
        self.assertIsNone(slots["age"])
        self.assertIsNone(slots["age_ref_date"])

    def test_birth_date_never_reaches_the_retrieval_gateway(self) -> None:
        # 생년월일은 슬롯으로는 필요하지만 embedding provider와 검색 로그로
        # 나가면 안 되는 PII다 - 회귀 테스트.
        redacted = llm_gateway.redact_sensitive_text("1990-03-15 생이고 육아 지원 궁금해요")
        self.assertNotIn("1990", redacted)
        self.assertIn("육아", redacted)


class ProfileSlotExtractionTests(unittest.TestCase):
    def test_extracts_gender_disability_employment_and_income(self) -> None:
        text = "저는 여성이고 등록장애인이며 구직 중입니다. 기초생활수급자예요."
        result = llm_gateway.extract_slots(text, {})
        self.assertEqual(result["gender"], "female")
        self.assertEqual(result["disability_status"], "registered")
        self.assertEqual(result["employment_status"], "job_seeking")
        self.assertEqual(result["income_bracket"], "under_30")

    def test_extracts_soft_slots(self) -> None:
        text = "기혼이고 임신 중이에요. 다문화 가구이고 다자녀입니다."
        result = llm_gateway.extract_slots(text, {})
        self.assertEqual(result["marital_status"], "married")
        self.assertEqual(result["pregnancy_status"], "pregnant")
        self.assertEqual(
            sorted(result["household_types"]), ["multi_child", "multicultural"]
        )

    def test_median_income_percentage_maps_to_a_bracket(self) -> None:
        for text, expected in (
            ("중위소득 30% 이하입니다", "under_30"),
            ("기준 중위소득 60% 정도예요", "pct_50_75"),
            ("중위소득 대비 120%입니다", "pct_100_150"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    llm_gateway.extract_slots(text, {})["income_bracket"], expected
                )

    def test_raw_income_amount_is_never_converted_into_a_bracket(self) -> None:
        # 금액에서 소득인정액을 추정해 구간을 붙이면 자격이 되는 제도가 조용히
        # 탈락한다. 가구원 수·공제·재산환산 없이는 계산이 불가능하다
        # (참고자료 C군) - 회귀 테스트.
        result = llm_gateway.extract_slots("월 소득이 250만원입니다", {})
        self.assertIsNone(result["income_bracket"])

    def test_interest_keywords_are_not_mistaken_for_a_personal_state(self) -> None:
        # "출산 지원금이 궁금해요"는 관심사이지 출산 사실이 아니다. 상태로
        # 오인하면 해당 없는 조건이 붙어 정답이 사라진다 - 회귀 테스트.
        result = llm_gateway.extract_slots("출산 지원금이랑 육아 지원 궁금해요", {})
        self.assertIsNone(result["pregnancy_status"])
        self.assertIn("출산", result["interests"])

    def test_extracts_requested_benefit_names_as_soft_interests(self) -> None:
        cases = (
            ("지원금제도 받고 싶어", "지원금제도"),
            ("실업급여 관련 제도 알아봐줘", "실업급여"),
            ("청년수당 받을 수 있나요?", "청년수당"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                result = llm_gateway.extract_slots(text, {})
                self.assertIn(expected, result["interests"])

    def test_llm_prompt_explains_open_ended_interest_intent(self) -> None:
        prompt = llm_gateway.build_slot_extraction_prompt("문화예술비 받고 싶어")
        self.assertIn("OOO 받고 싶어", prompt)
        self.assertIn("OOO 관련 제도 알아봐줘", prompt)
        self.assertIn("실업급여", prompt)

    def test_out_of_contract_enum_values_are_not_stored(self) -> None:
        # 계약에 없는 값이 슬롯에 들어가면 하드 게이트가 "채워졌음"으로 읽고
        # 검증되지 않은 값으로 판정이 진행된다 - 회귀 테스트.
        fake = {"gender": "제3의성별", "region_raw": None, "interests": []}
        with patch(
            "rag_chatbot.graph.nodes.slot_parser.extract_slots", return_value=fake
        ):
            slots = parse_slots({"user_input": "…", "slots": {}})["slots"]
        self.assertIsNone(slots.get("gender"))

    def test_reentry_merges_household_types_instead_of_replacing_them(self) -> None:
        first = parse_slots({"user_input": "한부모 가구입니다", "slots": {}})
        second = parse_slots(
            {"user_input": "다문화 가정이기도 해요", "slots": first["slots"]}
        )
        self.assertEqual(
            second["slots"]["household_types"], ["single_parent", "multicultural"]
        )


class ProfileHardGateTests(unittest.TestCase):
    def test_missing_profile_slots_are_reported_even_when_region_is_settled(
        self,
    ) -> None:
        state = {"slots": {"region_scope": "regional", "region_names": ["서울특별시"]}}
        missing = check_slot_completeness(state)["missing_slots"]
        self.assertNotIn("region", missing)
        self.assertEqual(
            missing,
            ["birth_date", "gender", "income_bracket", "disability_status",
             "employment_status"],
        )

    def test_soft_slots_are_never_reported_as_missing(self) -> None:
        missing = check_slot_completeness({"slots": {}})["missing_slots"]
        for soft in ("marital_status", "household_types", "pregnancy_status"):
            self.assertNotIn(soft, missing)

    def test_unknown_sentinel_satisfies_the_gate(self) -> None:
        slots = {**_FULL_HARD_GATE_SLOTS, "gender": "unknown"}
        self.assertEqual(check_slot_completeness({"slots": slots})["missing_slots"], [])

    def test_gate_gives_up_and_writes_a_sentinel_after_the_ask_limit(self) -> None:
        # 규칙 기반 추출기는 "말하기 싫어요"에서 값을 못 채운다. 상한이 없으면
        # N2 <-> N3가 무한히 돈다 - 회귀 테스트.
        slots = {key: value for key, value in _FULL_HARD_GATE_SLOTS.items()}
        del slots["gender"]
        ask_counts = {"gender": MAX_SLOT_ASKS}
        result = check_slot_completeness({"slots": slots, "slot_ask_counts": ask_counts})
        self.assertEqual(result["missing_slots"], [])
        self.assertEqual(result["slots"]["gender"], "unknown")
        # 입력 slots를 in-place로 바꾸지 않는다.
        self.assertNotIn("gender", slots)

    def test_n2a_runs_only_when_the_region_is_the_missing_slot(self) -> None:
        # N2a는 "지역을 몰라 지역별 제도를 못 찾는 동안" 실행하는 노드다.
        # 성별이 비었다고 법령 참고 검색을 돌릴 이유는 없다.
        self.assertTrue(needs_general_law_reference({"missing_slots": ["region"]}))
        self.assertFalse(needs_general_law_reference({"missing_slots": ["gender"]}))
        self.assertFalse(needs_general_law_reference({"missing_slots": []}))


class FilterPlanTests(unittest.TestCase):
    def test_income_and_employment_are_hard_filters(self) -> None:
        self.assertIn("income_bracket", HARD_FILTER_SLOTS)
        self.assertIn("employment_status", HARD_FILTER_SLOTS)
        self.assertEqual(SOFT_FILTER_SLOTS, {"gender", "disability_status"})

    def test_confirmed_slots_become_hard_and_soft_conditions(self) -> None:
        plan = resolve_filter_slots(_FILTER_READY_SLOTS)
        self.assertEqual(
            sorted(plan["hard"]),
            ["birth_date", "employment_status", "income_bracket", "region"],
        )
        self.assertEqual(sorted(plan["soft"]), ["disability_status", "gender"])
        self.assertEqual(plan["skipped"], [])

    def test_unknown_sentinel_is_never_turned_into_a_filter(self) -> None:
        # "모른다"는 "해당하지 않는다"가 아니다. 미확인 값을 필터로 걸면
        # 자격이 되는 제도가 조용히 탈락한다 - 회귀 테스트.
        slots = {**_FILTER_READY_SLOTS, "income_bracket": "unknown",
                 "employment_status": "unknown"}
        plan = resolve_filter_slots(slots)
        self.assertNotIn("income_bracket", plan["hard"])
        self.assertNotIn("employment_status", plan["hard"])
        self.assertEqual(
            sorted(plan["skipped"]), ["employment_status", "income_bracket"]
        )

    def test_income_preserves_ordered_interval_rank(self) -> None:
        # 문자열 equals 대신 순서값을 넘겨 N4가 raw JA 구간 경계 overlap을
        # 적용할 수 있게 한다.
        plan = resolve_filter_slots({**_FILTER_READY_SLOTS, "income_bracket": "pct_50_75"})
        condition = plan["hard"]["income_bracket"]
        self.assertNotIn("equals", condition)
        self.assertEqual(condition["max_bracket_rank"], 2)

        higher = resolve_filter_slots(
            {**_FILTER_READY_SLOTS, "income_bracket": "pct_100_150"}
        )
        self.assertGreater(
            higher["hard"]["income_bracket"]["max_bracket_rank"],
            condition["max_bracket_rank"],
        )

    def test_documents_without_the_criterion_are_not_excluded(self) -> None:
        # 소득 기준이 적혀 있지 않은 제도는 "소득 무관"이지 "불일치"가 아니다.
        # 지역만 예외 - 지역 없는 제도는 애초에 전국(national)으로 표기된다.
        plan = resolve_filter_slots(_FILTER_READY_SLOTS)
        for field in ("income_bracket", "employment_status", "birth_date"):
            with self.subTest(field=field):
                self.assertTrue(plan["hard"][field]["allow_missing"])
        self.assertFalse(plan["hard"]["region"]["allow_missing"])

    def test_region_hierarchy_is_passed_as_an_or_condition(self) -> None:
        # 광역·기초 제도가 중첩 유효하므로 계층 전체를 넘겨야 한다.
        plan = resolve_filter_slots(_FILTER_READY_SLOTS)
        self.assertEqual(
            plan["hard"]["region"]["any_of"], ["서울특별시", "서울특별시 강남구"]
        )

    def test_age_filter_carries_both_bases_from_the_birth_date(self) -> None:
        plan = resolve_filter_slots(_FILTER_READY_SLOTS)
        self.assertEqual(plan["hard"]["birth_date"]["age"], 36)
        self.assertEqual(plan["hard"]["birth_date"]["age_year_based"], 36)

    def test_age_filter_is_skipped_when_no_birth_date_was_given(self) -> None:
        # 자기신고 숫자만으로는 연령 필터를 만들지 않는다 - 회귀 테스트.
        slots = {key: value for key, value in _FILTER_READY_SLOTS.items()}
        for field in ("birth_date", "age", "age_year_based"):
            slots.pop(field, None)
        slots["age_self_reported"] = 36
        plan = resolve_filter_slots(slots)
        self.assertNotIn("birth_date", plan["hard"])
        self.assertIn("birth_date", plan["skipped"])

    def test_end_to_end_from_utterance_to_filter_plan(self) -> None:
        slots = parse_slots(
            {
                "user_input": (
                    "서울 강남구 사는 1990-03-15 생 여성입니다. "
                    "차상위계층이고 구직 중이에요. 장애는 없습니다."
                ),
                "slots": {},
            }
        )["slots"]
        self.assertEqual(check_slot_completeness({"slots": slots})["missing_slots"], [])
        plan = resolve_filter_slots(slots)
        self.assertEqual(plan["hard"]["income_bracket"]["max_bracket_rank"], 1)
        self.assertEqual(plan["hard"]["employment_status"]["equals"], "job_seeking")
        self.assertEqual(plan["soft"]["gender"]["equals"], "female")


class ProgramNameIsNotUserStateTests(unittest.TestCase):
    """제도 이름을 사용자 상태로 읽지 않는지 본다.

    소득과 취업상태는 하드 필터라, 질문을 상태로 오인하면 자격이 되는 제도가
    통째로 탈락한다. 사라진 정답은 복구할 방법이 없다.
    """

    def test_asking_about_a_benefit_does_not_set_income_bracket(self) -> None:
        for text in (
            "주거급여 얼마나 받을 수 있나요?",
            "생계급여 신청하고 싶어요",
            "의료급여 대상 조건이 뭔가요",
            "차상위계층 지원 정책 뭐 있어요?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    llm_gateway.extract_slots(text, {})["income_bracket"]
                )

    def test_stating_the_status_does_set_income_bracket(self) -> None:
        # 반대 방향도 고정한다. 너무 좁혀서 실제 상태를 놓치면 안 된다.
        for text, expected in (
            ("차상위계층입니다", "pct_30_50"),
            ("주거급여 수급자입니다", "pct_30_50"),
            ("기초생활수급자예요", "under_30"),
            ("생계급여 받고 있어요", "under_30"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    llm_gateway.extract_slots(text, {})["income_bracket"], expected
                )

    def test_asking_about_a_benefit_does_not_set_employment_status(self) -> None:
        for text in ("구직급여 얼마 나오나요", "재직증명서 어디서 떼나요"):
            with self.subTest(text=text):
                self.assertIsNone(
                    llm_gateway.extract_slots(text, {})["employment_status"]
                )

    def test_stating_the_status_does_set_employment_status(self) -> None:
        for text, expected in (
            ("구직 중입니다", "job_seeking"),
            ("재직 중이에요", "employed"),
            ("자영업 하고 있습니다", "self_employed"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    llm_gateway.extract_slots(text, {})["employment_status"], expected
                )

    def test_asking_about_a_target_group_does_not_set_gender_or_household(self) -> None:
        result = llm_gateway.extract_slots("여성 대상 정책이랑 다자녀 혜택 알려주세요", {})
        self.assertIsNone(result["gender"])
        self.assertEqual(result["household_types"], [])

    def test_asking_about_postpartum_care_does_not_set_pregnancy_status(self) -> None:
        self.assertIsNone(
            llm_gateway.extract_slots("산후조리원 지원금 있나요", {})["pregnancy_status"]
        )


class OppositeMeaningTests(unittest.TestCase):
    def test_absent_spouse_is_not_read_as_married(self) -> None:
        # "배우자 없이"가 기혼으로 읽히면 정반대 판정이 된다 - 회귀 테스트.
        for text in ("배우자 없이 혼자 삽니다", "배우자가 없습니다"):
            with self.subTest(text=text):
                self.assertEqual(
                    llm_gateway.extract_slots(text, {})["marital_status"], "single"
                )

    def test_present_spouse_is_still_read_as_married(self) -> None:
        self.assertEqual(
            llm_gateway.extract_slots("배우자와 함께 삽니다", {})["marital_status"],
            "married",
        )

    def test_income_above_a_threshold_is_not_read_as_below_it(self) -> None:
        # "100% 초과"를 "100% 이하" 구간으로 잡으면 상한 비교 방향이 뒤집혀
        # 대상이 아닌 제도가 통과한다 - 회귀 테스트.
        self.assertEqual(
            llm_gateway.extract_slots("중위소득 100% 초과입니다", {})["income_bracket"],
            "pct_100_150",
        )
        self.assertEqual(
            llm_gateway.extract_slots("중위소득 100% 이하입니다", {})["income_bracket"],
            "pct_75_100",
        )

    def test_particle_after_median_income_does_not_drop_the_bracket(self) -> None:
        # 조사 하나로 소득 구간이 빠지면 하드 필터가 아예 안 걸린다.
        self.assertEqual(
            llm_gateway.extract_slots("기준중위소득의 50% 이하입니다", {})["income_bracket"],
            "pct_30_50",
        )


class BirthDateValidationParityTests(unittest.TestCase):
    def test_parse_birth_date_rejects_future_and_implausible_dates(self) -> None:
        # docstring이 "미래·비현실적 날짜는 거부한다"고 적혀 있었는데 실제로는
        # 검증이 없었다 - 회귀 테스트.
        self.assertIsNone(parse_birth_date("2099-01-01"))
        self.assertIsNone(parse_birth_date("1500-01-01"))
        self.assertIsNone(parse_birth_date("not-a-date"))
        self.assertIsNone(parse_birth_date(""))
        self.assertIsNotNone(parse_birth_date("1990-03-15"))

    def test_birth_date_upper_bound_matches_vector_filter_contract(self) -> None:
        reference_date = date(2026, 1, 1)

        self.assertEqual(
            parse_birth_date("1905-01-02", reference_date), date(1905, 1, 2)
        )
        self.assertIsNone(parse_birth_date("1905-01-01", reference_date))

    def test_gate_and_filter_agree_on_what_counts_as_a_birth_date(self) -> None:
        # 게이트는 "값이 있다"로 통과시키고 필터는 "날짜가 아니다"로 건너뛰면,
        # 연령 조건 없이 검색이 진행되는데 아무도 모른다 - 회귀 테스트.
        for bad in ("2099-01-01", "그냥 문자열", ""):
            with self.subTest(bad=bad):
                slots = {**_FILTER_READY_SLOTS, "birth_date": bad}
                gate_missing = check_slot_completeness({"slots": slots})["missing_slots"]
                filter_skipped = resolve_filter_slots(slots)["skipped"]
                self.assertIn("birth_date", gate_missing)
                self.assertIn("birth_date", filter_skipped)


class RegionAskLimitTests(unittest.TestCase):
    def test_region_falls_back_to_national_instead_of_looping_forever(self) -> None:
        # 지역은 센티넬로 둘 수 없어서 상한이 없으면 N2 <-> N3가 영원히 돈다
        # - 회귀 테스트.
        slots = {key: value for key, value in _FULL_HARD_GATE_SLOTS.items()}
        slots["region_scope"] = "unknown"
        slots["region_names"] = []
        result = check_slot_completeness(
            {"slots": slots, "slot_ask_counts": {"region": MAX_SLOT_ASKS}}
        )
        self.assertEqual(result["missing_slots"], [])
        self.assertEqual(result["slots"]["region_scope"], "national")
        self.assertEqual(result["slots"]["region_names"], ["전국"])
        self.assertTrue(result["region_fallback_applied"])

    def test_no_fallback_flag_before_the_ask_limit(self) -> None:
        slots = {**_FULL_HARD_GATE_SLOTS, "region_scope": "unknown", "region_names": []}
        result = check_slot_completeness(
            {"slots": slots, "slot_ask_counts": {"region": MAX_SLOT_ASKS - 1}}
        )
        self.assertEqual(result["missing_slots"], ["region"])
        self.assertNotIn("region_fallback_applied", result)


class GateLoopTerminationTests(unittest.TestCase):
    def test_the_n1_n2_n3_loop_always_terminates(self) -> None:
        """사용자가 끝까지 답하지 않아도 루프가 끝나는지 실제로 돌려본다.

        슬롯이 여섯 개로 늘면서 되묻기 경로가 길어졌다. 상한이 어느 한
        슬롯에서 빠지면 대화가 영원히 끝나지 않는다.
        """

        state = {"user_input": "지원금 알려주세요", "slots": {}, "slot_ask_counts": {}}
        for _ in range(20):
            state.update(parse_slots(state))
            state.update(check_slot_completeness(state))
            if not state["missing_slots"]:
                break
            state.update(request_missing_slot_input(state))
            # 사용자가 아무 정보도 주지 않는 최악의 경우.
            state["user_input"] = "잘 모르겠어요"
        else:
            self.fail("N1 <-> N3 루프가 종료되지 않았다")

        self.assertEqual(state["missing_slots"], [])
        self.assertTrue(state["region_fallback_applied"])
        # 끝까지 못 받은 슬롯은 센티넬로 남아 필터에서 제외된다.
        plan = resolve_filter_slots(state["slots"])
        self.assertEqual(
            sorted(plan["skipped"]),
            ["birth_date", "disability_status", "employment_status", "gender",
             "income_bracket"],
        )
        self.assertIn("region", plan["hard"])


class AgeSubjectTests(unittest.TestCase):
    """참고자료 §8 세 번째 함정: 연령 조건이 누구를 가리키는가."""

    def test_parent_asking_about_a_child_does_not_get_an_age_filter(self) -> None:
        # 41세 학부모의 "우리 아이 지원 뭐 있나요"에서 본인 나이로 필터가
        # 걸리면 아동 제도가 전부 탈락한다 - 회귀 테스트.
        slots = parse_slots(
            {
                "user_input": (
                    "서울 강남구 사는 1985-05-20 생 여성입니다. "
                    "재직 중이고 중위소득 90%예요. 우리 아이 지원 뭐 있나요?"
                ),
                "slots": {},
            }
        )["slots"]
        self.assertEqual(slots["age_subject"], "child")
        # 본인 나이는 여전히 계산해 둔다 - 답변에서 쓸 수 있어야 한다.
        self.assertIsNotNone(slots["age"])
        plan = resolve_filter_slots(slots)
        self.assertNotIn("birth_date", plan["hard"])
        self.assertEqual(plan["skipped_reasons"]["birth_date"], SKIP_SUBJECT_NOT_SELF)
        # 나머지 필터는 그대로 살아 있어야 한다.
        self.assertIn("region", plan["hard"])
        self.assertIn("income_bracket", plan["hard"])

    def test_asking_about_oneself_still_gets_an_age_filter(self) -> None:
        slots = parse_slots(
            {
                "user_input": "서울 강남구 삽니다. 1990-03-15 생 여성이고 구직 중이에요.",
                "slots": {},
            }
        )["slots"]
        self.assertEqual(slots["age_subject"], "self")
        self.assertIn("birth_date", resolve_filter_slots(slots)["hard"])

    def test_mentioning_both_self_and_a_child_is_ambiguous(self) -> None:
        # 어느 쪽인지 모를 때는 연령 필터를 생략하는 것이 맞다.
        slots = parse_slots(
            {"user_input": "저는 1985-05-20 생인데 우리 아이 지원도 궁금해요", "slots": {}}
        )["slots"]
        self.assertEqual(slots["age_subject"], "unknown")
        self.assertNotIn("birth_date", resolve_filter_slots(slots)["hard"])

    def test_asking_on_behalf_of_a_parent_is_not_self(self) -> None:
        # 노인 돌봄도 같은 구조다 - 신청자와 연령 주체가 다르다.
        slots = parse_slots({"user_input": "어머니 돌봄 지원 뭐 있나요", "slots": {}})["slots"]
        self.assertNotEqual(slots["age_subject"], "self")

    def test_subject_does_not_silently_revert_to_self_on_a_later_turn(self) -> None:
        # 사용자가 정정하지 않았는데 주체가 본인으로 돌아오면 판정이 조용히
        # 뒤집힌다 - 회귀 테스트.
        first = parse_slots({"user_input": "우리 아이 지원 뭐 있나요", "slots": {}})
        second = parse_slots(
            {"user_input": "서울 강남구예요. 1985-05-20 생입니다.", "slots": first["slots"]}
        )
        self.assertEqual(second["slots"]["age_subject"], "child")
        self.assertNotIn("birth_date", resolve_filter_slots(second["slots"])["hard"])

    def test_skip_reasons_distinguish_missing_data_from_wrong_subject(self) -> None:
        # "미확인이라 못 걸었다"와 "주체가 본인이 아니라 걸면 안 된다"는
        # 다른 사건이고, 답변에서도 다르게 설명해야 한다.
        no_birth_date = {key: value for key, value in _FILTER_READY_SLOTS.items()}
        for field in ("birth_date", "age", "age_year_based"):
            no_birth_date.pop(field, None)
        self.assertEqual(
            resolve_filter_slots(no_birth_date)["skipped_reasons"]["birth_date"],
            SKIP_NOT_CONFIRMED,
        )
        wrong_subject = {**_FILTER_READY_SLOTS, "age_subject": "child"}
        self.assertEqual(
            resolve_filter_slots(wrong_subject)["skipped_reasons"]["birth_date"],
            SKIP_SUBJECT_NOT_SELF,
        )

    def test_age_subject_is_not_a_hard_gate_slot(self) -> None:
        # 사용자에게 되물을 값이 아니라 대화에서 파생하는 값이다.
        self.assertNotIn("age_subject", HARD_GATE_SLOTS)
        missing = check_slot_completeness({"slots": {}})["missing_slots"]
        self.assertNotIn("age_subject", missing)


if __name__ == "__main__":
    unittest.main()
