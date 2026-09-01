"""src/rag_chatbot/service.py 단위 테스트 (FakeStore - vectorDB/네트워크 없이
PolicyView 조립 로직만 검증. graph.stream()을 실제로 돌리는 통합 테스트는
tests/test_graph_builder.py의 인터럽트/재개 스모크 테스트가 이미 커버한다 -
여기서는 ``ask``/``answer_followup``이 아니라 그 결과를 정책 카드로 바꾸는
``_to_chat_response``/``_build_policy_view``/``_fetch_policy_detail``만
검증한다).
"""

from __future__ import annotations

from rag_design.contracts import Chunk, RetrievedChunk, SCHEMA_VERSION, SourceType, compute_content_hash
from src.rag_chatbot.llm import (
    FailingLLMClient,
    FakeLLMClient,
    LLMCallError,
    RecordingLLMClient,
    diagnose_hf_error,
)
from src.rag_chatbot.service import (
    _build_output_markdown,
    _build_output_text,
    _build_policy_view,
    _extract_title,
    _fetch_policy_detail,
    _format_amount_label,
    _rank_policies,
    _strip_prefix,
    _to_chat_response,
)


def _section_chunk(policy_id: str, section_type: str, label: str, body: str) -> RetrievedChunk:
    text = f"영유아보육료 지원\n지역: 전국\n{label}\n\n{body}"
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"{policy_id}-{section_type}-chunk-1",
        doc_id=f"subsidy:{policy_id}:v1",
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=(label,),
        ordinal=0,
        citation_locator=label,
        content_hash=compute_content_hash(text),
        metadata={
            "source_id": policy_id,
            "source_url": f"https://gov.kr/{policy_id}",
            "source_name": "대한민국 공공서비스(혜택) 정보",
            "section_type": section_type,
            "organization": "보건복지부",
            "region_names": ["전국"],
            "region_scope": "national",
            "age_start": 3,
            "age_end": 5,
        },
    )
    return RetrievedChunk(
        query_id="test", chunk=chunk, rank=1, score=0.1,
        score_type="cosine_distance", retriever_version="test:fixture", index_name="subsidy",
    )


class FakeDetailStore:
    """(source_id, section_type) 조합만 알고 있는 최소 store 대체 구현."""

    def __init__(self, sections: dict[str, RetrievedChunk]):
        self._sections = sections  # key: f"{policy_id}:{section_type}"
        self.calls: list[dict] = []

    def search(self, source_type, query, *, query_id, top_k, search_filter):
        me = search_filter.metadata_equals
        self.calls.append({"source_id": me.get("source_id"), "section_type": me.get("section_type")})
        key = f"{me.get('source_id')}:{me.get('section_type')}"
        hit = self._sections.get(key)
        return (hit,) if hit else ()


def test_extract_title_and_strip_prefix():
    text = "영유아보육료 지원\n지역: 전국\n지원대상\n\n만 3~5세 어린이가 대상이다."
    assert _extract_title(text) == "영유아보육료 지원"
    assert _strip_prefix(text) == "만 3~5세 어린이가 대상이다."


def test_strip_prefix_falls_back_to_original_when_no_separator():
    text = "구분자가 없는 텍스트"
    assert _strip_prefix(text) == text


def test_format_amount_label_uses_note_when_amount_missing():
    assert _format_amount_label(None, "정보 부족: 지원금 계산 결과 없음") == "정보 부족: 지원금 계산 결과 없음"
    assert _format_amount_label(280000.0, None) == "280,000원"
    assert _format_amount_label(133333, None) == "133,333원"  # 정수형 amount도 지원


def test_fetch_policy_detail_collects_all_sections_and_metadata():
    store = FakeDetailStore(
        {
            "policy-a:purpose": _section_chunk("policy-a", "purpose", "목적", "영유아 보육을 지원한다."),
            "policy-a:support_target": _section_chunk("policy-a", "support_target", "지원대상", "만 3~5세 아동."),
        }
    )
    detail = _fetch_policy_detail("policy-a", store, "q1")
    assert detail["sections"]["purpose"] == "영유아 보육을 지원한다."
    assert detail["sections"]["support_target"] == "만 3~5세 아동."
    assert "eligibility_criteria" not in detail["sections"]  # 원천에 없으면 지어내지 않고 빠짐
    assert detail["title"] == "영유아보육료 지원"
    assert detail["source_url"] == "https://gov.kr/policy-a"
    assert detail["age_start"] == 3 and detail["age_end"] == 5
    # 7개 section_type 전부에 대해 재검색을 시도했는지 (일부만 있어도 전부 확인)
    assert len(store.calls) == 7
    assert all(call["source_id"] == "policy-a" for call in store.calls)


def test_build_policy_view_maps_eligibility_and_marks_top_as_most_suitable():
    store = FakeDetailStore(
        {"policy-a:purpose": _section_chunk("policy-a", "purpose", "목적", "영유아 보육을 지원한다.")}
    )
    entry = {
        "eligibility": {
            "policy_id": "policy-a",
            "verdict": "충족",
            "reasons": ["근거 문장"],
            "checked": ["연령"],
            "unchecked": ["장애 여부", "성별", "소득 수준", "취업 상태"],
        },
        "benefit_amount": {"policy_id": "policy-a", "amount": 280000.0},
        "duplicate": {"policy_id": "policy-a", "status": "미확인", "conflicts_with": [], "condition_note": "중복수급 근거가 없거나 불확실함"},
    }
    view = _build_policy_view("policy-a", entry, store=store, query_id="q1", rank=1, is_top=True)

    # "가장 적합"은 확인한 조건이 연령뿐인데 최적이라고 단정하는 표현이라 뺐다.
    assert view["badge"] == "우선 검토"
    assert view["verification_checked"] == ["연령"]
    assert "장애 여부" in view["verification_note"]
    assert view["verification_note"] in view["needs_confirmation"]
    assert view["eligibility_status"] == "충족"
    assert view["amount"] == 280000.0
    assert view["amount_label"] == "280,000원"
    assert view["duplicate_status"] == "미확인"
    assert "중복수급 근거가 없거나 불확실함" in view["needs_confirmation"]
    assert view["title"] == "영유아보육료 지원"
    assert view["detail"]["purpose"] == "영유아 보육을 지원한다."
    assert view["detail"]["source_url"] == "https://gov.kr/policy-a"


def test_build_policy_view_badge_for_non_top_and_uncertain_verdict():
    store = FakeDetailStore({})
    entry = {
        "eligibility": {"policy_id": "policy-b", "verdict": "미확인", "reasons": ["재검색에서 해당 정책 근거를 다시 찾지 못함"]},
        "benefit_amount": None,
        "status_note": "정보 부족: 지원금 계산 결과 없음",
        "duplicate": None,
    }
    view = _build_policy_view("policy-b", entry, store=store, query_id="q1", rank=2, is_top=False)

    assert view["badge"] == "확인 필요"
    assert view["amount"] is None
    assert view["amount_label"] == "정보 부족: 지원금 계산 결과 없음"
    assert "재검색에서 해당 정책 근거를 다시 찾지 못함" in view["needs_confirmation"]
    assert view["title"] == "policy-b"  # 상세 섹션을 하나도 못 찾으면 policy_id로 대체


def test_rank_policies_prefers_충족_then_larger_amount():
    policies = {
        "b": {"eligibility": {"verdict": "충족"}, "benefit_amount": {"amount": 100000.0}},
        "a": {"eligibility": {"verdict": "충족"}, "benefit_amount": {"amount": 280000.0}},
        "c": {"eligibility": {"verdict": "미확인"}, "benefit_amount": None},
        "d": {"eligibility": {"verdict": "미충족"}, "benefit_amount": None},
    }
    ranked_ids = [policy_id for policy_id, _ in _rank_policies(policies)]
    assert ranked_ids == ["a", "b", "c", "d"]


def test_to_chat_response_needs_input_shape():
    class _Interrupt:
        def __init__(self, value):
            self.value = value

    result = {"__interrupt__": (_Interrupt("어느 지역에 거주하시나요?"),), "missing_slots": ["region_names"]}
    response = _to_chat_response(result, session_id="s1", store=FakeDetailStore({}))

    assert response["status"] == "needs_input"
    assert response["question"] == "어느 지역에 거주하시나요?"
    assert response["missing_slots"] == ["region_names"]
    assert response["session_id"] == "s1"
    assert response["output_json"]["question"] == "어느 지역에 거주하시나요?"
    assert "추가 정보가 필요합니다" in response["output_text"]
    assert "region_names" in response["output_text"]
    assert "| 상태 | 추가 질문 | 부족한 정보 |" in response["output_markdown"]


def test_to_chat_response_answered_shape_with_no_policies():
    result = {
        "answer_status": "abstained",
        "final_answer": "확인된 근거가 부족해 답변을 제공할 수 없습니다.",
        "final_citations": [],
    }
    response = _to_chat_response(result, session_id="s1", store=FakeDetailStore({}))

    assert response["status"] == "answered"
    assert response["answer_status"] == "abstained"
    assert response["policies"] == []
    assert response["output_json"]["policies"] == []
    assert response["output_text"] == "확인된 근거가 부족해 답변을 제공할 수 없습니다."
    assert "확인된 정책 없음" in response["output_markdown"]


def test_build_output_markdown_returns_policy_table_and_escapes_cells():
    markdown = _build_output_markdown(
        [
            {
                "rank": 1,
                "title": "청년 | 주거 지원",
                "eligibility_status": "미확인",
                "amount_label": "월 최대 200,000원",
                "duplicate_status": "확인 필요",
                "detail": {"source_url": "https://gov.example/policy-1"},
            }
        ]
    )

    assert "| 순위 | 정책명 | 자격 확인 | 지원금 | 중복수급 | 출처 |" in markdown
    assert "청년 \\| 주거 지원" in markdown
    assert "[원문](https://gov.example/policy-1)" in markdown


def test_build_output_text_returns_plain_policy_comparison():
    text = _build_output_text(
        [
            {
                "rank": 1,
                "title": "청년 주거 지원",
                "eligibility_status": "충족",
                "amount_label": "월 200,000원",
                "duplicate_status": "미확인",
                "detail": {"source_url": "https://gov.example/policy-1"},
            }
        ],
        "추천 결과입니다.",
    )

    assert text.startswith("추천 결과입니다.")
    assert "정책 비교" in text
    assert "[1] 청년 주거 지원" in text
    assert "자격: 충족" in text
    assert "https://gov.example/policy-1" in text


# --- LLM 실패 표기 (2026-08-31 추가) ---------------------------------------
#
# 노드들은 LLM 호출이 실패해도 규칙 기반으로 폴백해서 그래프를 끝까지 돌린다.
# 그 폴백이 조용해서 "LLM이 한 번도 안 돌았는데 결과는 멀쩡히 나오는" 상태를
# 아무도 모르는 문제가 있었다 - RecordingLLMClient가 그걸 드러낸다.


def test_recording_client_passes_through_and_counts_success():
    recorder = RecordingLLMClient(FakeLLMClient("응답"))
    assert recorder.complete("프롬프트", system="시스템") == "응답"
    summary = recorder.summary()
    assert summary["enabled"] is True
    assert summary["calls"] == 1 and summary["successes"] == 1
    assert summary["failures"] == 0 and summary["messages"] == []


def test_recording_client_records_failures_without_swallowing_them():
    # 실패를 삼키면 노드가 폴백 여부를 판단할 수 없다 - 반드시 다시 던져야 한다.
    recorder = RecordingLLMClient(FailingLLMClient("토큰 만료"))
    for _ in range(2):
        try:
            recorder.complete("프롬프트")
        except LLMCallError:
            pass
    summary = recorder.summary()
    assert summary["calls"] == 2 and summary["successes"] == 0
    # 같은 원인이 노드마다 반복되므로 중복은 한 번만 남긴다.
    assert summary["failures"] == 1
    assert "토큰 만료" in summary["messages"][0]


def test_recording_client_reset_clears_previous_request():
    recorder = RecordingLLMClient(FailingLLMClient())
    try:
        recorder.complete("프롬프트")
    except LLMCallError:
        pass
    recorder.reset()
    assert recorder.summary()["calls"] == 0
    assert recorder.summary()["messages"] == []


def test_recording_client_exposes_inner_model_name():
    class _Inner:
        model = "some/model"

        def complete(self, prompt, *, system=None):
            return "{}"

    assert RecordingLLMClient(_Inner()).summary()["model"] == "some/model"


# --- HuggingFace 실패 원인 진단 --------------------------------------------


def test_diagnose_hf_error_maps_status_codes_to_actionable_causes():
    # "403 Forbidden"만 보고는 뭘 고쳐야 할지 알 수 없다 - 실제로 토큰/크레딧/
    # provider 중 무엇이 문제인지 몰라 한참 헤맸다.
    cases = {
        401: "토큰",
        402: "크레딧",
        403: "권한",
        404: "찾을 수 없음",
        429: "한도",
    }
    for status, expected in cases.items():
        message = diagnose_hf_error(Exception(f"{status} Something"), "some/model")
        assert f"HTTP {status}" in message
        assert expected in message
        assert "some/model" in message


def test_diagnose_hf_error_reads_status_from_a_response_object():
    class _Response:
        status_code = 402

    class _Exc(Exception):
        response = _Response()

    assert "HTTP 402" in diagnose_hf_error(_Exc("결제 필요"), "m")


def test_diagnose_hf_error_handles_timeout_and_network_without_a_status():
    assert "시간 초과" in diagnose_hf_error(Exception("Read timed out"), "m")
    assert "네트워크" in diagnose_hf_error(Exception("Connection refused"), "m")


def test_build_policy_view_marks_unverified_when_nothing_was_checked():
    """대조한 조건이 하나도 없으면 "충족"이어도 "미검증"으로 표시한다.

    실제로 비장애인 사용자에게 장애인 정책이 "가장 적합"으로 떴던 상황 -
    그 문서에는 age_start/age_end가 없어 N9가 아무것도 대조하지 못했다.
    """

    store = FakeDetailStore({})
    entry = {
        "eligibility": {
            "policy_id": "policy-c",
            "verdict": "충족",
            "reasons": ["근거 문장"],
            "checked": [],
            "unchecked": ["연령", "장애 여부", "성별", "소득 수준", "취업 상태"],
        },
        "benefit_amount": None,
        "duplicate": None,
    }
    view = _build_policy_view("policy-c", entry, store=store, query_id="q1", rank=1, is_top=True)

    assert view["badge"] == "미검증"
    assert view["verification_checked"] == []
    assert "확인하지 못했습니다" in view["verification_note"]


def test_build_policy_view_omits_verification_note_for_legacy_verdicts():
    # checked/unchecked가 없는 옛 형식 판정에는 없는 사실을 지어내지 않는다.
    store = FakeDetailStore({})
    entry = {
        "eligibility": {"policy_id": "policy-d", "verdict": "충족", "reasons": []},
        "benefit_amount": None,
        "duplicate": None,
    }
    view = _build_policy_view("policy-d", entry, store=store, query_id="q1", rank=1, is_top=True)

    assert view["verification_note"] is None
