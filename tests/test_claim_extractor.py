"""src/rag_chatbot/graph/nodes/claim_extractor.py 단위 테스트.

N5(plan_claims)가 요구하는 ClaimExtractor 구현체(RuleBasedClaimExtractor /
LLMClaimExtractor) 검증. Issue #25(graph builder 조립)에서 추가했다.
"""

from __future__ import annotations

import json
from threading import Barrier

from src.rag_chatbot.graph.nodes import LLMClaimExtractor, RuleBasedClaimExtractor
from src.rag_chatbot.llm import (
    FailingLLMClient,
    FakeLLMClient,
    RecordingLLMClient,
)


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


def test_same_chunk_is_not_extracted_twice() -> None:
    """claim 추출 결과는 정책 원문에만 의존하므로 두 번 뽑을 이유가 없다.

    캐시가 없을 때 실측(2026-08-31): N5가 청크 5개에 매 턴 LLM을 5번 불러
    292초가 걸렸다. 되묻기로 대화가 길어지면 같은 정책을 계속 다시 뽑는다.
    """

    text = "만 65세 이상 어르신에게 지원합니다."
    payload = {"claims": [{"claim_type": "eligibility", "reasons": [text]}]}
    client = FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    extractor = LLMClaimExtractor(client)

    first = extractor.extract(policy_id="policy-a", text=text)
    second = extractor.extract(policy_id="policy-a", text=text)

    assert first == second
    assert len(client.calls) == 1  # 두 번째는 캐시에서 나온다


def test_cache_is_keyed_by_text_not_only_policy_id() -> None:
    # 정책 하나가 여러 청크로 쪼개져 있다. 원문이 다르면 다시 뽑아야 한다.
    payload = {"claims": [{"claim_type": "eligibility", "reasons": ["가"]}]}
    client = FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    extractor = LLMClaimExtractor(client)

    extractor.extract(policy_id="policy-a", text="가 조건입니다")
    extractor.extract(policy_id="policy-a", text="나 조건입니다")

    assert len(client.calls) == 2


def test_cached_result_cannot_be_mutated_by_the_caller() -> None:
    # 캐시가 준 리스트를 호출자가 바꿔도 다음 호출이 오염되면 안 된다.
    text = "만 65세 이상 지원"
    payload = {"claims": [{"claim_type": "eligibility", "reasons": [text]}]}
    extractor = LLMClaimExtractor(FakeLLMClient(json.dumps(payload, ensure_ascii=False)))

    first = extractor.extract(policy_id="policy-a", text=text)
    first[0]["claim_type"] = "오염됨"

    assert extractor.extract(policy_id="policy-a", text=text)[0]["claim_type"] == "eligibility"


def test_prefetch_fills_the_cache_so_extract_makes_no_call() -> None:
    payload = {"claims": [{"claim_type": "eligibility", "reasons": ["근거"]}]}
    client = FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    extractor = LLMClaimExtractor(client)
    items = [("policy-a", "근거 하나"), ("policy-b", "근거 둘")]

    extractor.prefetch(items)
    calls_after_prefetch = len(client.calls)
    for policy_id, text in items:
        extractor.extract(policy_id=policy_id, text=text)

    assert calls_after_prefetch == 2
    assert len(client.calls) == 2  # extract가 추가 호출을 하지 않는다


def test_prefetch_failure_does_not_break_extraction() -> None:
    # prefetch는 최적화일 뿐이다. 실패해도 extract가 평소대로 동작해야 한다.
    extractor = LLMClaimExtractor(FailingLLMClient())
    extractor.prefetch([("policy-a", "가 조건"), ("policy-b", "나 조건")])

    claims = extractor.extract(policy_id="policy-a", text="가 조건")

    # 규칙 기반 폴백은 claim_type 3개를 만든다.
    assert len(claims) == 3


def test_prefetch_workers_keep_the_request_recording_context(monkeypatch) -> None:
    class ConcurrentClient:
        def __init__(self):
            self.barrier = Barrier(2)

        def complete(self, prompt, *, system=None):
            self.barrier.wait(timeout=5)
            return json.dumps(
                {"claims": [{"claim_type": "eligibility", "reasons": ["근거"]}]},
                ensure_ascii=False,
            )

    recorder = RecordingLLMClient(ConcurrentClient())
    extractor = LLMClaimExtractor(recorder)
    monkeypatch.setenv("LLM_PREFETCH_WORKERS", "2")

    with recorder.request_scope():
        extractor.prefetch(
            [("policy-a", "근거 하나"), ("policy-b", "근거 둘")]
        )
        summary = recorder.summary()

    assert summary["calls"] == 2
    assert summary["successes"] == 2
    assert summary["failures"] == 0
    assert recorder.summary()["calls"] == 0
