"""src/rag_chatbot/graph/nodes/claim_extractor.py 단위 테스트.

N5(plan_claims)가 요구하는 ClaimExtractor 구현체(RuleBasedClaimExtractor /
LLMClaimExtractor) 검증. Issue #25(graph builder 조립)에서 추가했다.
"""

from __future__ import annotations

import json

from src.rag_chatbot.graph.nodes import LLMClaimExtractor, RuleBasedClaimExtractor
from src.rag_chatbot.llm import FailingLLMClient, FakeLLMClient


def test_rule_based_extractor_creates_one_claim_per_type_with_verbatim_reason() -> None:
    extractor = RuleBasedClaimExtractor()

    claims = extractor.extract(policy_id="policy-a", text="지원 대상 안내문")

    assert {c["claim_type"] for c in claims} == {"eligibility", "amount", "duplicate"}
    for claim in claims:
        assert claim["reasons"] == ["지원 대상 안내문"]
        assert claim["law_check_required"] is False


def test_rule_based_extractor_returns_empty_for_blank_text() -> None:
    assert RuleBasedClaimExtractor().extract(policy_id="policy-a", text="   ") == []


def test_llm_extractor_uses_validated_llm_output() -> None:
    text = "나이 65세 이상 대상자에게 지급한다."
    response = json.dumps(
        {
            "claims": [
                {
                    "claim_type": "eligibility",
                    "law_check_required": False,
                    "reasons": ["나이 65세 이상 대상자에게 지급한다."],
                }
            ]
        }
    )
    extractor = LLMClaimExtractor(FakeLLMClient(response=response))

    claims = extractor.extract(policy_id="policy-a", text=text)

    assert claims == [
        {
            "claim_type": "eligibility",
            "law_check_required": False,
            "reasons": ["나이 65세 이상 대상자에게 지급한다."],
            "required_aspects": [],
        }
    ]


def test_llm_extractor_falls_back_when_reason_not_in_source_text() -> None:
    text = "실제 원문 내용"
    response = json.dumps({"claims": [{"claim_type": "eligibility", "reasons": ["원문에 없는 문장"]}]})
    extractor = LLMClaimExtractor(FakeLLMClient(response=response))

    claims = extractor.extract(policy_id="policy-a", text=text)

    # 지어낸 reason만 있던 claim은 버려지고, 규칙 기반 폴백으로 대체된다.
    assert claims == RuleBasedClaimExtractor().extract(policy_id="policy-a", text=text)


def test_llm_extractor_falls_back_when_llm_call_fails() -> None:
    text = "원문"
    extractor = LLMClaimExtractor(FailingLLMClient())

    claims = extractor.extract(policy_id="policy-a", text=text)

    assert claims == RuleBasedClaimExtractor().extract(policy_id="policy-a", text=text)


def test_llm_extractor_falls_back_on_invalid_json() -> None:
    text = "원문"
    extractor = LLMClaimExtractor(FakeLLMClient(response="이건 JSON이 아님"))

    claims = extractor.extract(policy_id="policy-a", text=text)

    assert claims == RuleBasedClaimExtractor().extract(policy_id="policy-a", text=text)


def test_llm_extractor_drops_claim_with_unknown_claim_type() -> None:
    text = "원문 내용"
    response = json.dumps(
        {
            "claims": [
                {"claim_type": "not_a_real_type", "reasons": ["원문 내용"]},
                {"claim_type": "amount", "reasons": ["원문 내용"]},
            ]
        }
    )
    extractor = LLMClaimExtractor(FakeLLMClient(response=response))

    claims = extractor.extract(policy_id="policy-a", text=text)

    assert len(claims) == 1
    assert claims[0]["claim_type"] == "amount"


def test_llm_extractor_returns_empty_for_blank_text() -> None:
    extractor = LLMClaimExtractor(FakeLLMClient(response="{}"))
    assert extractor.extract(policy_id="policy-a", text="   ") == []


def test_llm_extractor_reads_json_wrapped_in_a_code_fence() -> None:
    """모델이 코드펜스를 붙여 답해도 claim을 살려낸다 (2026-08-31 회귀 테스트).

    예전에는 json.loads(raw)를 그대로 써서, LLM이 제대로 뽑아준 claim이
    코드펜스 하나 때문에 통째로 버려지고 규칙 기반으로 폴백했다.
    프로브에서 N5만 한 번도 성공하지 못한 유력한 원인이다.
    """

    text = "만 65세 이상 어르신에게 월 30만원을 지원합니다."
    payload = {
        "claims": [
            {
                "claim_type": "eligibility",
                "law_check_required": False,
                "reasons": ["만 65세 이상 어르신에게 월 30만원을 지원합니다."],
                "required_aspects": [],
            }
        ]
    }
    client = FakeLLMClient("설명입니다.\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")

    claims = LLMClaimExtractor(client).extract(policy_id="policy-a", text=text)

    # 규칙 기반 폴백은 claim_type 3개를 모두 만든다. 1개면 LLM 결과를 쓴 것.
    assert len(claims) == 1
    assert claims[0]["claim_type"] == "eligibility"
