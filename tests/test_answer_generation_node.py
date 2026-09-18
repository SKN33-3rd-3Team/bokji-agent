"""src/rag_chatbot/graph/nodes/answer_generation.py (N13) 단위 테스트."""

from __future__ import annotations

from rag_design.contracts import (
    Chunk,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes import generate_answer
from src.rag_chatbot.llm import FailingLLMClient, FakeLLMClient


def test_law_validation_preserves_full_spaced_names_and_articles():
    from src.rag_chatbot.graph.nodes.answer_generation import _validate_structured_summaries

    cases = [
        ("노인 복지법", "장애인 복지법", False),
        ("노인\n복지법", "장애인\n복지법", False),
        ("노인복지법", "장애인복지법상 지원 대상입니다", False),
        ("상담 지원", "사회보장급여의 이용 및 제공에 관한 법률", False),
        ("국민기초생활 보장법", "국민기초생활보장법", True),
        ("국민기초생활보장법 제24조", "국민기초생활 보장법 제 24 조", True),
        ("국민기초생활보장법 제24조", "국민기초생활보장법 제25조", False),
        ("노인복지법", "노인복지법 및 장애인복지법", False),
        ("상담 지원", "사회보장급여의 이용ㆍ제공 및 수급권자 발굴에 관한 법률", False),
    ]
    for source, summary, accepted in cases:
        result = _validate_structured_summaries(
            {"policies": [{"policy_id": "p", "summary": summary}]}, {"p": source}
        )
        assert ("p" in result) == accepted, (source, summary, result)


def _chunk(chunk_id: str, doc_id: str, text: str, source_url: str | None) -> Chunk:
    return Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=chunk_id,
        doc_id=doc_id,
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("지원자격",),
        ordinal=0,
        citation_locator="지원자격",
        content_hash=compute_content_hash(text),
        metadata={"source_url": source_url},
    )


def _retrieved(chunk: Chunk) -> RetrievedChunk:
    return RetrievedChunk(
        query_id="q1",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="subsidy",
    )


def _state() -> dict:
    chunk = _chunk("policy-a-chunk-1", "policy-a", "정책 안내", "https://example.gov.kr/a")
    return {
        "assembled_result": {
            "policies": {
                "policy-a": {
                    "eligibility": {
                        "policy_id": "policy-a",
                        "verdict": "충족",
                        "reasons": ["근거 문장"],
                    },
                    "benefit_amount": {"policy_id": "policy-a", "amount": 10000.0},
                    "duplicate": {"policy_id": "policy-a", "status": "미확인"},
                }
            }
        },
        "claim_plan": [
            {
                "claim_id": "c1",
                "policy_id": "policy-a",
                "claim_type": "eligibility",
                "evidence_chunk_ids": ["policy-a-chunk-1"],
            }
        ],
        "subsidy_chunks": [_retrieved(chunk)],
        "law_chunks": [],
        "node_trace": ["N12"],
    }


def test_generate_answer_without_llm_uses_template() -> None:
    result = generate_answer(_state())

    assert "policy-a" in result["draft_answer"]
    # "지원 가능"은 과대 주장이라 "확인한 조건에서는 결격 없음"으로 바꿨다.
    # N9가 대조하는 건 문서 metadata의 연령 기준뿐이라, 나머지 조건은
    # 확인조차 못 한 상태에서 "지원 가능"이라고 말하면 안 된다.
    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert "지원 가능" not in result["draft_answer"]
    # 2026-09-17: citation label이 어느 사실(자격/금액/중복수급)의 근거인지
    # 밝히도록 바뀌었다 - "근거 문서"라는 뭉뚱그린 라벨은 화면에서 자격 근거와
    # 금액 근거를 구분할 수 없었다(Citation Precision 개선 작업).
    assert result["citations"] == [
        {
            "policy_id": "policy-a",
            "chunk_id": "policy-a-chunk-1",
            "source_url": "https://example.gov.kr/a",
            "label": "지원자격 근거",
        }
    ]
    assert result["node_trace"] == ["N12", "N13"]


def test_generate_answer_uses_validated_llm_summary_when_available() -> None:
    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-a", "summary": "다듬어진 안내문"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert result["draft_answer"].startswith("다듬어진 안내문")
    # 사실 라인(지원자격/지원금액/중복수급)은 검증에 통과한 정책이어도
    # 여전히 템플릿(규칙 기반)에서 나온다 - LLM이 다시 쓰지 않는다.
    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert len(llm.calls) == 1


def test_generate_answer_rejects_summary_with_fabricated_number() -> None:
    """rag_eval.ipynb 실험에서 실측된 위험(원문 없는 숫자로 자릿수 부풀림)을
    막는지 확인한다. 원문 지원금액은 10000.0인데, LLM이 100000이라고 쓰면
    그 정책의 summary는 버려지고 템플릿 문장만 남아야 한다."""

    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-a", '
        '"summary": "지원금 100000원을 받으실 수 있어요"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert "100000" not in result["draft_answer"]
    assert result["draft_answer"] == "\n\n".join(
        [
            "[policy-a]\n- 지원자격: 확인한 조건에서는 결격 없음\n"
            "  근거: 근거 문장\n- 지원금액: 10000.0\n- 중복수급: 미확인"
        ]
    )


def test_generate_answer_rejects_summary_with_fabricated_law_name() -> None:
    """150문항 실측(2026-09-17)에서 확인된 위험(원문에 없는 법령명을 지어냄,
    예: "관련된 법령은 조세특례제한법입니다")을 막는지 확인한다. 원문에는
    법령명이 전혀 없는데 LLM이 지어내면 그 정책의 summary는 버려진다."""

    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-a", '
        '"summary": "관련 법령은 조세특례제한법입니다"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert "조세특례제한법" not in result["draft_answer"]
    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]


def test_generate_answer_keeps_summary_that_reuses_the_source_law_name() -> None:
    """원문에 실제로 있는 법령명을 그대로 옮겨 쓴 summary는 버리지 않는다 -
    법령명 검증이 "새 법령명을 지어내는 것"만 잡아야지, 원문에 있는 이름을
    다시 언급했다는 이유로 정상 summary까지 버리면 안 된다."""

    state = _state()
    state["assembled_result"]["policies"]["policy-a"]["benefit_amount"] = None
    state["assembled_result"]["policies"]["policy-a"]["related_law"] = [
        {"law_name": "국민기초생활 보장법", "source_url": "https://law.example/1"}
    ]
    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-a", '
        '"summary": "국민기초생활 보장법에 근거한 지원 제도입니다"}]}'
    )

    result = generate_answer(state, llm_client=llm)

    assert result["draft_answer"].startswith("국민기초생활 보장법에 근거한 지원 제도입니다")


def test_generate_answer_keeps_summary_using_a_non_law_method_word() -> None:
    """"계산법"/"산정법"처럼 "OO법" 형태지만 법령명이 아닌 흔한 단어까지
    법령명으로 오인해 정상 summary를 버리면 안 된다."""

    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-a", '
        '"summary": "소득 산정법에 따라 계산됩니다"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert result["draft_answer"].startswith("소득 산정법에 따라 계산됩니다")


def test_generate_answer_ignores_summary_for_unknown_policy_id() -> None:
    llm = FakeLLMClient(
        response='{"policies": [{"policy_id": "policy-does-not-exist", '
        '"summary": "지어낸 정책 안내문"}]}'
    )

    result = generate_answer(_state(), llm_client=llm)

    assert "지어낸 정책" not in result["draft_answer"]


def test_generate_answer_falls_back_to_template_when_llm_returns_non_json() -> None:
    llm = FakeLLMClient(response="그냥 자유 텍스트, JSON 아님")

    result = generate_answer(_state(), llm_client=llm)

    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert "그냥 자유 텍스트" not in result["draft_answer"]


def test_generate_answer_falls_back_to_template_when_llm_fails() -> None:
    result = generate_answer(_state(), llm_client=FailingLLMClient())

    assert "policy-a" in result["draft_answer"]
    # "지원 가능"은 과대 주장이라 "확인한 조건에서는 결격 없음"으로 바꿨다.
    # N9가 대조하는 건 문서 metadata의 연령 기준뿐이라, 나머지 조건은
    # 확인조차 못 한 상태에서 "지원 가능"이라고 말하면 안 된다.
    assert "확인한 조건에서는 결격 없음" in result["draft_answer"]
    assert "지원 가능" not in result["draft_answer"]


def test_generate_answer_with_no_policies_returns_placeholder() -> None:
    result = generate_answer(
        {"assembled_result": {}, "claim_plan": [], "subsidy_chunks": [], "law_chunks": []}
    )

    assert result["draft_answer"] == "확인된 복지 제도 정보가 없습니다."
    assert result["citations"] == []


def test_generate_answer_drops_citation_without_source_url() -> None:
    state = _state()
    state["subsidy_chunks"] = [
        _retrieved(_chunk("policy-a-chunk-1", "policy-a", "정책 안내", None))
    ]

    result = generate_answer(state)

    assert result["citations"] == []


def test_generate_answer_ignores_citations_for_claims_of_other_policies() -> None:
    state = _state()
    state["claim_plan"].append(
        {
            "claim_id": "c2",
            "policy_id": "policy-other",
            "claim_type": "eligibility",
            "evidence_chunk_ids": ["policy-a-chunk-1"],
        }
    )

    result = generate_answer(state)

    assert [c["policy_id"] for c in result["citations"]] == ["policy-a"]


def test_generate_answer_cites_only_the_rule_chunk_for_amount() -> None:
    """지원금액 줄은 실제 계산에 쓰인 rule_chunk_id 하나만 인용해야 한다.

    RuleBasedClaimExtractor는 청크 하나당 eligibility/amount/duplicate claim을
    전부 만들어서, "지원대상" 섹션처럼 금액과 무관한 청크도 amount claim의
    evidence_chunk_ids에 들어갈 수 있었다 - 그게 그대로 "지원금액" 줄의
    근거로 나가면 화면에 보이는 숫자와 무관한 문서가 citation에 섞인다.
    """

    state = _state()
    state["assembled_result"]["policies"]["policy-a"]["benefit_amount"] = {
        "policy_id": "policy-a",
        "amount": 10000.0,
        "rule_chunk_id": "policy-a-chunk-amount",
    }
    state["claim_plan"].append(
        {
            "claim_id": "c-amount",
            "policy_id": "policy-a",
            "claim_type": "amount",
            # 금액과 무관한 "지원대상" 청크가 RuleBasedClaimExtractor 관례대로
            # amount claim의 evidence로 들어와 있다 - rule_chunk_id가 아니므로
            # 인용되면 안 된다.
            "evidence_chunk_ids": ["policy-a-chunk-1"],
        }
    )
    amount_chunk = _retrieved(
        _chunk(
            "policy-a-chunk-amount",
            "policy-a",
            "지원금액: 매월 1만원",
            "https://example.gov.kr/a-amount",
        )
    )
    state["subsidy_chunks"].append(amount_chunk)

    result = generate_answer(state)

    amount_citations = [c for c in result["citations"] if c["label"] == "지원금액 근거"]
    assert amount_citations == [
        {
            "policy_id": "policy-a",
            "chunk_id": "policy-a-chunk-amount",
            "source_url": "https://example.gov.kr/a-amount",
            "label": "지원금액 근거",
        }
    ]
    # amount claim이 지목한 policy-a-chunk-1은 금액 근거로 인용되지 않는다
    # (eligibility 근거로는 여전히 인용된다 - 그 줄의 실제 근거이므로).
    assert [c["chunk_id"] for c in amount_citations] == ["policy-a-chunk-amount"]


def test_generate_answer_cites_amount_even_when_same_chunk_as_eligibility() -> None:
    """자격과 금액이 같은 chunk에 함께 서술된 정책(예: "지원대상: 중위소득
    50% 이하, 월 10만원 지급")에서는 그 chunk가 eligibility citation으로
    먼저 추가되더라도 amount citation으로도 별도 라벨을 달고 나와야 한다.
    chunk_id만으로 중복 제거하면 뒤에 추가되는 amount citation이 조용히
    빠지고, 화면의 "지원금액" 줄에는 근거 citation이 하나도 안 달린다."""

    state = _state()
    state["assembled_result"]["policies"]["policy-a"]["benefit_amount"] = {
        "policy_id": "policy-a",
        "amount": 100000.0,
        # eligibility claim의 evidence_chunk_ids와 동일한 chunk_id.
        "rule_chunk_id": "policy-a-chunk-1",
    }

    result = generate_answer(state)

    labels_by_chunk = {(c["chunk_id"], c["label"]) for c in result["citations"]}
    assert ("policy-a-chunk-1", "지원자격 근거") in labels_by_chunk
    assert ("policy-a-chunk-1", "지원금액 근거") in labels_by_chunk


def test_generate_answer_omits_amount_citation_without_rule_chunk_id() -> None:
    """rule_chunk_id가 없으면(옛 계약, 테스트 fixture 등) 금액 근거를 추측해서
    붙이지 않고 그냥 건너뛴다 - 틀린 chunk를 인용하는 것보다 안전하다."""

    state = _state()  # benefit_amount에 rule_chunk_id가 없는 기본 fixture
    state["claim_plan"].append(
        {
            "claim_id": "c-amount",
            "policy_id": "policy-a",
            "claim_type": "amount",
            "evidence_chunk_ids": ["policy-a-chunk-1"],
        }
    )

    result = generate_answer(state)

    assert all(c["label"] != "지원금액 근거" for c in result["citations"])


def test_generate_answer_duplicate_citation_does_not_borrow_other_claim_types() -> None:
    """중복수급 줄의 근거는 duplicate claim의 evidence만 쓴다 - 같은 정책의
    eligibility/amount claim이 가리키는 청크를 빌려오지 않는다."""

    state = _state()
    dup_chunk = _retrieved(
        _chunk(
            "policy-a-chunk-dup",
            "policy-a",
            "다른 지원과 중복수급 불가",
            "https://example.gov.kr/a-dup",
        )
    )
    state["subsidy_chunks"].append(dup_chunk)
    state["claim_plan"].append(
        {
            "claim_id": "c-dup",
            "policy_id": "policy-a",
            "claim_type": "duplicate",
            "evidence_chunk_ids": ["policy-a-chunk-dup"],
        }
    )

    result = generate_answer(state)

    dup_citations = [c for c in result["citations"] if c["label"] == "중복수급 근거"]
    assert [c["chunk_id"] for c in dup_citations] == ["policy-a-chunk-dup"]


def test_generate_answer_skips_duplicate_citation_when_no_duplicate_verdict() -> None:
    """duplicate 판정 자체가 없는 정책은(entry["duplicate"] is None) 중복수급
    줄을 렌더링하지 않으므로, 실수로 duplicate claim이 있어도 인용하지 않는다."""

    state = _state()
    state["assembled_result"]["policies"]["policy-a"]["duplicate"] = None
    state["claim_plan"].append(
        {
            "claim_id": "c-dup",
            "policy_id": "policy-a",
            "claim_type": "duplicate",
            "evidence_chunk_ids": ["policy-a-chunk-1"],
        }
    )

    result = generate_answer(state)

    assert all(c["label"] != "중복수급 근거" for c in result["citations"])
