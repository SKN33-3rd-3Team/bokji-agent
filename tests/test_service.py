"""src/rag_chatbot/service.py 단위 테스트 (FakeStore - vectorDB/네트워크 없이
PolicyView 조립 로직만 검증. graph.stream()을 실제로 돌리는 통합 테스트는
tests/test_graph_builder.py의 인터럽트/재개 스모크 테스트가 이미 커버한다 -
여기서는 ``ask``/``answer_followup``이 아니라 그 결과를 정책 카드로 바꾸는
``_to_chat_response``/``_build_policy_view``/``_fetch_policy_detail``만
검증한다).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import os
from threading import Barrier, Event, Lock, Thread
from unittest.mock import patch

import pytest

from rag_design.contracts import Chunk, RetrievedChunk, SCHEMA_VERSION, SourceType, compute_content_hash
from rag_design.embeddings import HashEmbeddingProvider, SentenceTransformerKoreanProvider
from src.rag_chatbot import service as service_module
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
    _build_profile,
    _build_summary,
    _build_policy_view,
    _extract_title,
    _fetch_policy_detail,
    _format_amount_label,
    _rank_policies,
    _strip_prefix,
    _to_chat_response,
    build_embedding_provider,
)
from src.rag_chatbot.timing import PhaseTimer


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


def test_embedding_provider_defaults_to_korean_with_explicit_hash_override():
    with patch.dict(os.environ, {}, clear=True):
        korean = build_embedding_provider()
    assert isinstance(korean, SentenceTransformerKoreanProvider)
    assert korean.model_name == "intfloat/multilingual-e5-base"
    assert korean.dimension == 768

    with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "hash"}, clear=True):
        offline = build_embedding_provider()
    assert isinstance(offline, HashEmbeddingProvider)
    assert offline.dimension == 128


def test_connect_store_rejects_an_empty_directory(tmp_path):
    with patch.object(service_module, "_REAL_VECTOR_DB_PATH", tmp_path):
        with pytest.raises(SystemExit, match="사전 구축된 vectorDB"):
            service_module.connect_store()


def test_get_graph_loads_support_conditions_once_and_injects_them():
    original_cache = dict(service_module._runtime_cache)
    service_module._runtime_cache.clear()
    store = object()
    llm_client = object()
    conditions = {"service-1": {"JA0101": "Y"}}
    # 2026-09-11: user_types 색인(policy_conditions.load_policy_user_types)이
    # startup에 추가됐다 - support_conditions와 별개 소스(처리된 subsidy
    # jsonl)에서 읽는 사용자구분("개인"/"법인/시설/단체" 등)매핑이다.
    user_types = {"service-1": frozenset({"개인"})}
    graph = object()
    try:
        with (
            patch.object(service_module, "connect_store", return_value=store),
            patch.object(
                service_module, "build_llm_client", return_value=llm_client
            ),
            patch.object(
                service_module,
                "load_support_conditions",
                return_value=conditions,
            ) as load_mock,
            patch.object(
                service_module,
                "load_policy_user_types",
                return_value=user_types,
            ) as load_user_types_mock,
            patch.object(
                service_module, "build_graph", return_value=graph
            ) as build_mock,
        ):
            assert service_module.get_graph() is graph
            assert service_module.get_graph() is graph

        load_mock.assert_called_once_with(service_module._REAL_SUPPORT_CONDITIONS_PATH)
        load_user_types_mock.assert_called_once_with(
            service_module._REAL_SUBSIDY_DOCUMENTS_PATH
        )
        build_mock.assert_called_once_with(
            store,
            llm_client=llm_client,
            support_conditions=conditions,
            user_types=user_types,
        )
    finally:
        service_module._runtime_cache.clear()
        service_module._runtime_cache.update(original_cache)


def test_concurrent_first_requests_share_runtime_and_followup_checkpointer():
    class BarrierCache(dict):
        def __init__(self):
            super().__init__()
            self._first_checks = Barrier(2)
            self._check_count = 0
            self._check_lock = Lock()

        def __contains__(self, key):
            with self._check_lock:
                self._check_count += 1
                check_count = self._check_count
                present = super().__contains__(key)
            if key == "graph" and check_count <= 2:
                self._first_checks.wait(timeout=5)
            return present

    class FakeTimer:
        def reset(self):
            pass

        def measure(self, _name):
            return nullcontext()

        def summary(self):
            return []

        def path(self):
            return []

    class Interrupt:
        value = "어느 지역에 거주하시나요?"

    class FakeGraph:
        def __init__(self, store):
            self.store = store
            self.pending: set[str] = set()
            self.lock = Lock()

    connected_stores: list[object] = []
    built_graphs: list[FakeGraph] = []

    def connect_store():
        store = object()
        connected_stores.append(store)
        return store

    def build_graph(store, **_kwargs):
        graph = FakeGraph(store)
        built_graphs.append(graph)
        return graph

    def run_graph(graph, *, session_id, **_kwargs):
        with graph.lock:
            graph.pending.add(session_id)
        return {
            "__interrupt__": (Interrupt(),),
            "missing_slots": ["region_names"],
            "_runtime_store": graph.store,
        }

    def resume_graph(graph, *, session_id, **_kwargs):
        with graph.lock:
            assert session_id in graph.pending
            graph.pending.remove(session_id)
        return {
            "query_id": session_id,
            "assembled_result": {"policies": {}},
            "answer_status": "abstained",
            "final_answer": "확인 완료",
            "final_citations": [],
            "_runtime_store": graph.store,
        }

    original_to_chat_response = service_module._to_chat_response

    def to_chat_response(result, *, session_id, store):
        assert store is result["_runtime_store"]
        payload = dict(result)
        payload.pop("_runtime_store")
        return original_to_chat_response(payload, session_id=session_id, store=store)

    with (
        patch.object(service_module, "_runtime_cache", BarrierCache()),
        patch.object(service_module, "_runtime_lock", Lock()),
        patch.object(service_module, "TIMER", FakeTimer()),
        patch.object(service_module, "connect_store", side_effect=connect_store),
        patch.object(service_module, "build_llm_client", return_value=None),
        patch.object(service_module, "load_support_conditions", return_value={}),
        patch.object(service_module, "load_policy_user_types", return_value={}),
        patch.object(service_module, "build_graph", side_effect=build_graph),
        patch.object(service_module, "run_graph", side_effect=run_graph),
        patch.object(service_module, "resume_graph", side_effect=resume_graph),
        patch.object(
            service_module, "_to_chat_response", side_effect=to_chat_response
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        sessions = ("session-a", "session-b")
        first_futures = [
            executor.submit(service_module.ask, "첫 질문", session_id)
            for session_id in sessions
        ]
        first_responses = [
            future.result(timeout=10) for future in first_futures
        ]
        followup_futures = [
            executor.submit(service_module.answer_followup, session_id, "서울")
            for session_id in sessions
        ]
        followup_responses = [
            future.result(timeout=10) for future in followup_futures
        ]

        assert [response["status"] for response in first_responses] == [
            "needs_input",
            "needs_input",
        ]
        assert [response["status"] for response in followup_responses] == [
            "answered",
            "answered",
        ]
        assert len(connected_stores) == 1
        assert len(built_graphs) == 1
        assert built_graphs[0].store is service_module._runtime_cache["store"]
        assert not built_graphs[0].pending


def test_simultaneous_ask_and_followup_keep_llm_status_separate():
    class MarkerClient:
        model = "marker-model"

        def __init__(self):
            self.barrier = Barrier(2)

        def complete(self, prompt, *, system=None, max_tokens=None):
            self.barrier.wait(timeout=5)
            if prompt == "failure-b":
                raise LLMCallError("failure-b")
            return prompt

    class FakeTimer:
        def reset(self):
            pass

        def measure(self, _name):
            return nullcontext()

        def summary(self):
            return []

        def path(self):
            return []

    class Interrupt:
        value = "추가 정보를 알려주세요."

    recorder = RecordingLLMClient(MarkerClient())
    graph = object()
    store = object()

    def run_or_resume_graph(_graph, *, user_input, **_kwargs):
        try:
            recorder.complete(user_input)
        except LLMCallError:
            pass
        return {
            "__interrupt__": (Interrupt(),),
            "missing_slots": ["region_names"],
        }

    with (
        patch.object(
            service_module,
            "_runtime_cache",
            {
                "store": store,
                "llm_client": recorder,
                "support_conditions": {},
                "graph": graph,
            },
        ),
        patch.object(service_module, "TIMER", FakeTimer()),
        patch.object(
            service_module, "run_graph", side_effect=run_or_resume_graph
        ),
        patch.object(
            service_module, "resume_graph", side_effect=run_or_resume_graph
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [
            executor.submit(service_module.ask, "success-a", "session-success-a"),
            executor.submit(
                service_module.answer_followup, "session-failure-b", "failure-b"
            ),
        ]
        success_response, failure_response = [
            future.result(timeout=10) for future in futures
        ]

    assert success_response["llm_status"]["calls"] == 1
    assert success_response["llm_status"]["successes"] == 1
    assert success_response["llm_status"]["failures"] == 0
    assert success_response["llm_status"]["messages"] == []
    assert failure_response["llm_status"]["calls"] == 1
    assert failure_response["llm_status"]["successes"] == 0
    assert failure_response["llm_status"]["failures"] == 1
    assert failure_response["llm_status"]["messages"] == ["failure-b"]
    assert recorder.summary()["calls"] == 0


def test_service_response_includes_completed_request_total_timing():
    class Interrupt:
        value = "추가 정보를 알려주세요."

    with (
        patch.object(
            service_module,
            "_runtime_cache",
            {
                "store": object(),
                "llm_client": None,
                "support_conditions": {},
                "graph": object(),
            },
        ),
        patch.object(service_module, "TIMER", PhaseTimer()),
        patch.object(
            service_module,
            "run_graph",
            return_value={
                "__interrupt__": (Interrupt(),),
                "missing_slots": ["region_names"],
            },
        ),
    ):
        response = service_module.ask("첫 질문", "timing-session")

    assert any(
        phase["name"] == "request_total" for phase in response["timing"]["phases"]
    )


def test_phase_timer_serializes_summary_with_concurrent_record():
    timer = PhaseTimer()
    timer.record("initial", 1.0)
    writer_started = Event()
    writer_finished = Event()
    worker = None

    class CoordinatedTotals(dict):
        def items(self):
            nonlocal worker

            def write_during_summary():
                writer_started.set()
                timer.record("concurrent", 2.0)
                writer_finished.set()

            worker = Thread(target=write_during_summary)
            worker.start()
            assert writer_started.wait(timeout=1)
            assert not writer_finished.wait(timeout=0.05)
            return super().items()

    timer._totals = CoordinatedTotals(timer._totals)
    summary = timer.summary()
    worker.join(timeout=1)

    assert writer_finished.is_set()
    assert [row["name"] for row in summary] == ["initial"]


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
    # 10개 section_type 전부에 대해 재검색을 시도했는지 (일부만 있어도 전부 확인)
    assert len(store.calls) == 10
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
    # amount_label은 st.metric 위젯용으로 고정된 짧은 문구만 담는다(2026-09-11) -
    # 실제 사유는 needs_confirmation에서 보여준다.
    assert view["amount_label"] == "지원금액 확인 필요"
    assert "재검색에서 해당 정책 근거를 다시 찾지 못함" in view["needs_confirmation"]
    assert "정보 부족: 지원금 계산 결과 없음" in view["needs_confirmation"]
    assert view["title"] == "policy-b"  # 상세 섹션을 하나도 못 찾으면 policy_id로 대체


def test_build_policy_view_amount_note_does_not_leak_into_duplicate_note():
    """금액 계산 실패 사유가 중복수급 캡션 자리로 새면 안 된다 (2026-09-11 회귀).

    이 정책에는 중복수급 조항이 아예 없다(duplicate=None). 그런데도 예전
    코드는 duplicate가 비어 있으면 entry["status_note"](금액 계산 실패 사유,
    예: 대출 한도라 확정 불가)를 duplicate_note 자리에 채워 넣었다 - 그
    결과 화면의 "중복수급" 캡션에 대출 한도 얘기가 뜨는, 맥락이 완전히
    다른 문구가 노출됐다(streamlit_ui/rendering.py가 duplicate_note를
    중복수급 캡션으로 그린다). status_note는 needs_confirmation이 올바른
    자리에서 보여주므로(아래 test_build_policy_view_amount_note_moves_to_
    needs_confirmation 참고), duplicate_note는 실제 중복수급 조항이
    있을 때만 채워야 한다.
    """
    store = FakeDetailStore({})
    entry = {
        "eligibility": {"policy_id": "policy-e", "verdict": "충족", "reasons": [], "checked": ["연령"]},
        "benefit_amount": {"policy_id": "policy-e", "amount": None},
        "status_note": (
            "금액처럼 보이는 값이 대출·보증 한도이거나 자격 문턱값이라 "
            "지원금으로 확정할 수 없음 (규칙 추출)"
        ),
        "duplicate": None,
    }
    view = _build_policy_view("policy-e", entry, store=store, query_id="q1", rank=1, is_top=False)

    assert view["duplicate_note"] is None
    assert view["duplicate_status"] == "미확인"
    # 금액 계산 실패 사유는 사라지지 않는다 - needs_confirmation이라는
    # 올바른 자리로 옮겨서 그대로 보인다(2026-09-11: amount_label 자리는
    # st.metric 위젯용으로 고정된 짧은 문구만 남기도록 바꿨다 - 아래
    # test_build_policy_view_amount_note_moves_to_needs_confirmation 참고).
    assert "대출·보증 한도" not in view["amount_label"]
    assert any("대출·보증 한도" in item for item in (view["needs_confirmation"] or []))


def test_build_policy_view_amount_note_moves_to_needs_confirmation():
    """금액 미확정 사유는 amount_label이 아니라 needs_confirmation에 담긴다
    (2026-09-11 추가).

    streamlit_ui/rendering.py는 amount_label을 st.metric 위젯 값으로
    그린다 - 이 위젯은 짧은 값 한 줄용이다. amount가 None일 때
    status_note를 그대로 amount_label에 넣으면(예전 동작), LLM이 자유
    서술형으로 쓴 사유나 LLM 호출 실패 메시지처럼 길이가 들쭉날쭉한
    문장이 위젯 안에 그대로 들어가 카드 레이아웃이 깨진다. 이제
    amount_label은 amount가 없을 때 고정된 짧은 문구만 담고, 실제
    사유는 needs_confirmation(문장 길이 제약이 없는 자리)으로 옮긴다 -
    정보는 그대로 보여주되 위젯이 깨지지 않는 자리로 보낸다.
    """
    store = FakeDetailStore({})
    entry = {
        "eligibility": {"policy_id": "policy-f", "verdict": "충족", "reasons": [], "checked": ["연령"]},
        "benefit_amount": {"policy_id": "policy-f", "amount": None},
        "status_note": (
            "LLM이 원문에서 확정 금액을 추출하지 못함(조건부이거나 명시 안 됨): "
            "소득 구간에 따라 10만원 또는 30만원으로 차등 지급되어 원문만으로는 "
            "단일 금액을 확정할 수 없음"
        ),
        "duplicate": {"status": "없음"},
    }
    view = _build_policy_view("policy-f", entry, store=store, query_id="q1", rank=1, is_top=False)

    assert view["amount_label"] == "지원금액 확인 필요"
    assert "소득 구간에 따라" in " ".join(view["needs_confirmation"] or [])


def test_build_policy_view_shows_range_when_amount_is_unconfirmed_but_bounded():
    """T1 실사용 후속 요청(2026-09-11): '90-110만원'처럼 원문에 범위로만
    적힌 금액은 "확인 필요" 대신 범위 그대로 보여준다.

    amount는 여전히 None이다(대표값을 임의로 고르지 않는다는 원칙은
    유지) - 대신 benefit_amount에 실려온 amount_min/amount_max로
    amount_label을 채운다.
    """
    store = FakeDetailStore({})
    entry = {
        "eligibility": {"policy_id": "policy-g", "verdict": "충족", "reasons": [], "checked": ["연령"]},
        "benefit_amount": {
            "policy_id": "policy-g",
            "amount": None,
            "amount_min": 900000.0,
            "amount_max": 1100000.0,
            "period": "month",
        },
        "status_note": "원문에 범위(하한~상한)로만 금액이 명시되어 단일 금액을 확정할 수 없음",
        "duplicate": {"status": "없음"},
    }
    view = _build_policy_view("policy-g", entry, store=store, query_id="q1", rank=1, is_top=False)

    assert view["amount_label"] == "월 900,000원~1,100,000원"
    assert view["amount"] is None
    assert view["amount_min"] == 900000.0
    assert view["amount_max"] == 1100000.0
    # 사유도 needs_confirmation에 그대로 남아, 왜 대표값이 아니라 범위인지
    # 설명한다.
    assert "범위" in " ".join(view["needs_confirmation"] or [])


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

        def complete(self, prompt, *, system=None, max_tokens=None):
            return "{}"

    assert RecordingLLMClient(_Inner()).summary()["model"] == "some/model"


def test_recording_client_isolates_overlapping_request_scopes():
    class MarkerClient:
        def __init__(self):
            self.barrier = Barrier(2)

        def complete(self, prompt, *, system=None, max_tokens=None):
            self.barrier.wait(timeout=5)
            if prompt == "failure-b":
                raise LLMCallError("failure-b")
            return prompt

    recorder = RecordingLLMClient(MarkerClient())

    def run_request(marker):
        with recorder.request_scope():
            try:
                recorder.complete(marker)
            except LLMCallError:
                pass
            return recorder.summary()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_request, marker)
            for marker in ("success-a", "failure-b")
        ]
        success_summary, failure_summary = [
            future.result(timeout=10) for future in futures
        ]

    assert success_summary["calls"] == 1
    assert success_summary["successes"] == 1
    assert success_summary["failures"] == 0
    assert success_summary["messages"] == []
    assert failure_summary["calls"] == 1
    assert failure_summary["successes"] == 0
    assert failure_summary["failures"] == 1
    assert failure_summary["messages"] == ["failure-b"]
    assert recorder.summary()["calls"] == 0


def test_recording_client_restores_nested_default_and_exception_stats():
    recorder = RecordingLLMClient(FakeLLMClient("응답"))
    recorder.complete("default")

    with recorder.request_scope():
        recorder.complete("outer")
        with recorder.request_scope():
            recorder.complete("inner")
            assert recorder.summary()["calls"] == 1
        assert recorder.summary()["calls"] == 1

    assert recorder.summary()["calls"] == 1

    with pytest.raises(RuntimeError, match="scope failure"):
        with recorder.request_scope():
            recorder.complete("discarded")
            raise RuntimeError("scope failure")

    assert recorder.summary()["calls"] == 1


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


# ── output_json 구조 (첨부 화면과 같은 항목) ────────────────────────


def test_build_profile_uses_korean_labels_and_never_leaks_birth_date():
    """N1 슬롯 -> "파악한 정보". 생년월일 원문은 절대 실리지 않는다
    (docs/PII_LOGGING.md). 만 나이 파생값만 나간다."""

    profile = _build_profile(
        {
            "region_names": ["서울특별시"],
            "birth_date": "2021-03-05",
            "age": 5,
            "gender": "male",
            "income_bracket": "under_30",
            "disability_status": "not_registered",
            "employment_status": "not_working",
        }
    )

    assert profile == [
        {"key": "region", "label": "지역", "value": "서울특별시"},
        {"key": "age", "label": "나이", "value": "만 5세"},
        {"key": "gender", "label": "성별", "value": "남성"},
        {"key": "income_bracket", "label": "소득", "value": "기초생활수급 수준(중위소득 30% 이하)"},
        {"key": "disability_status", "label": "장애", "value": "장애 없음"},
        {"key": "employment_status", "label": "취업 상태", "value": "무직"},
    ]
    assert "2021-03-05" not in str(profile)


def test_build_profile_omits_age_when_only_birth_date_is_known():
    """생년월일은 있는데 만 나이 파생이 없으면 나이 항목을 그냥 비운다 -
    원본을 대신 노출하지 않는다."""

    assert _build_profile({"birth_date": "2021-03-05"}) == []


def test_build_profile_skips_unknown_sentinel_and_handles_non_mapping():
    assert _build_profile({"gender": "unknown", "employment_status": "unknown"}) == []
    assert _build_profile(None) == []


def test_build_summary_merges_unmet_and_unknown():
    """미충족과 미확인은 한 칸으로 합친다 - 둘 다 "받을 수 있다고 말할 수
    없는" 상태라서 따로 세면 사용자가 미확인을 통과로 읽는다."""

    summary = _build_summary(
        [
            {"eligibility_status": "충족"},
            {"eligibility_status": "미충족"},
            {"eligibility_status": "미확인"},
            {},  # eligibility_status가 없으면 미확인으로 센다
        ]
    )
    assert summary == {"checked": 4, "eligible": 1, "not_eligible_or_unknown": 3}


def test_to_chat_response_output_json_carries_summary_profile_and_evidence_count():
    result = {
        "answer_status": "complete",
        "final_answer": "확인된 범위의 안내입니다.",
        "final_citations": [
            {"label": "정책 공식 페이지", "source_url": "https://gov.example/p1"},
            {"label": "근거 법령", "source_url": "https://law.go.kr/x"},
        ],
        "slots": {"region_names": ["서울특별시"], "age": 5, "gender": "male"},
        "assembled_result": {"policies": {}},
    }

    response = _to_chat_response(result, session_id="s1", store=FakeDetailStore({}))
    output_json = response["output_json"]

    assert output_json["summary"] == {
        "checked": 0,
        "eligible": 0,
        "not_eligible_or_unknown": 0,
    }
    assert output_json["profile"] == [
        {"key": "region", "label": "지역", "value": "서울특별시"},
        {"key": "age", "label": "나이", "value": "만 5세"},
        {"key": "gender", "label": "성별", "value": "남성"},
    ]
    assert output_json["evidence_count"] == 2
    # 최상위 final_citations와 output_json 안의 값이 같은 목록이어야 한다.
    assert output_json["final_citations"] == response["final_citations"]


def test_to_chat_response_needs_input_output_json_carries_profile():
    class _Interrupt:
        def __init__(self, value):
            self.value = value

    result = {
        "__interrupt__": (_Interrupt("소득 수준을 알려주세요."),),
        "missing_slots": ["income_bracket"],
        "slots": {"region_names": ["부산광역시"]},
    }

    response = _to_chat_response(result, session_id="s1", store=FakeDetailStore({}))

    assert response["output_json"]["profile"] == [
        {"key": "region", "label": "지역", "value": "부산광역시"}
    ]


def test_build_output_markdown_prefixes_summary_line():
    markdown = _build_output_markdown(
        [
            {
                "rank": 1,
                "title": "유아학비 지원",
                "eligibility_status": "미확인",
                "amount_label": "지원금액 확인 필요",
                "duplicate_status": "미확인",
                "detail": {},
            }
        ]
    )

    assert markdown.startswith("**확인한 제도 1건** · 자격 충족 0건 · 미충족·미확인 1건")
    assert "| 순위 | 정책명 | 자격 확인 | 지원금 | 중복수급 | 출처 |" in markdown


def test_build_output_markdown_summary_line_when_no_policies():
    markdown = _build_output_markdown([])
    assert markdown.startswith("**확인한 제도 0건** · 자격 충족 0건 · 미충족·미확인 0건")
    assert "확인된 정책 없음" in markdown


# ── 화면에서 고른 지원조건·관심 분야(extra_interests) ────────────────


def _ask_capturing_run_graph(*args, **kwargs):
    """``ask``를 그래프 없이 호출하고 run_graph 가 받은 인자를 돌려준다."""

    class Interrupt:
        value = "추가 정보가 필요합니다."

    captured: dict = {}

    def fake_run_graph(graph, **graph_kwargs):
        captured.update(graph_kwargs)
        return {"__interrupt__": (Interrupt(),), "missing_slots": []}

    with (
        patch.object(
            service_module,
            "_runtime_cache",
            {
                "store": object(),
                "llm_client": None,
                "support_conditions": {},
                "graph": object(),
            },
        ),
        patch.object(service_module, "TIMER", PhaseTimer()),
        patch.object(service_module, "run_graph", side_effect=fake_run_graph),
    ):
        service_module.ask(*args, **kwargs)
    return captured


def test_ask_seeds_selected_interests_as_initial_slots():
    captured = _ask_capturing_run_graph(
        "질문", "s1", top_k=5, extra_interests=["청년", "주거"]
    )
    assert captured["slots"] == {"interests": ["청년", "주거"]}


def test_ask_passes_no_slots_when_nothing_selected():
    """아무것도 안 고르면 빈 interests 를 억지로 넣지 않는다 - 슬롯이
    "채워졌다"고 오해될 여지를 만들지 않는다."""

    assert _ask_capturing_run_graph("질문", "s1", top_k=5)["slots"] is None
    assert _ask_capturing_run_graph(
        "질문", "s1", top_k=5, extra_interests=[]
    )["slots"] is None
    assert _ask_capturing_run_graph(
        "질문", "s1", top_k=5, extra_interests=["", None]
    )["slots"] is None


# ── 토큰 한도로 잘린 LLM 응답 ───────────────────────────────────────


class _StubChoice:
    def __init__(self, content, finish_reason):
        self.message = type("_M", (), {"content": content})()
        self.finish_reason = finish_reason


class _StubResponse:
    def __init__(self, content, finish_reason):
        self.choices = [_StubChoice(content, finish_reason)]


def _complete_with(content, finish_reason, max_tokens=None, captured_kwargs=None):
    """HuggingFaceInferenceClient.complete()를 네트워크 없이 한 번 돌린다.

    max_tokens: 2026-09-11 추가 - complete() 호출 시 이번 호출에만 넘길
    per-call 오버라이드. None이면 인스턴스 기본값(max_new_tokens=1024)이
    그대로 쓰인다.
    captured_kwargs: 넘기면 실제 chat_completion()에 전달된 kwargs를
    이 dict에 채워 넣어 테스트에서 검증할 수 있게 한다.
    """

    import huggingface_hub

    from src.rag_chatbot.llm.client import HuggingFaceInferenceClient

    client = HuggingFaceInferenceClient(
        model="test/model", token="t", max_new_tokens=1024
    )

    class _StubInferenceClient:
        def __init__(self, **_kwargs):
            pass

        def chat_completion(self, **kwargs):
            if captured_kwargs is not None:
                captured_kwargs.update(kwargs)
            return _StubResponse(content, finish_reason)

    with patch.object(huggingface_hub, "InferenceClient", _StubInferenceClient):
        return client.complete("prompt", max_tokens=max_tokens)


def test_truncated_llm_answer_is_treated_as_a_failure_not_returned():
    """``finish_reason="length"``면 문장 중간에서 끊긴 출력이다.

    예전에는 content가 비었을 때만 실패로 봤다. 그래서 반쯤 찬 응답이 그대로
    화면까지 올라가 "근거 법령: 부모성" 처럼 문장이 뚝 끊긴 답이 보였다
    (2026-09-02 실측). 실패로 올려야 노드가 규칙 기반으로 폴백해 짧더라도
    완결된 답이 나가고, llm_status에 실패로 남아 화면에도 표시된다.
    """

    with pytest.raises(LLMCallError) as caught:
        _complete_with("3. 부모성장을 위한 심리지원서비스\n근거 법령: 부모성", "length")

    message = str(caught.value)
    assert "잘림" in message
    assert "max_new_tokens=1024" in message
    assert "다 못 씀" in message


def test_empty_llm_answer_from_length_limit_still_explains_reasoning_tokens():
    with pytest.raises(LLMCallError) as caught:
        _complete_with("", "length")

    assert "시작도 못 함" in str(caught.value)


def test_completed_llm_answer_is_returned_as_is():
    assert _complete_with("완결된 답변입니다.", "stop") == "완결된 답변입니다."


# ── max_tokens per-call 오버라이드 (2026-09-11 추가) ───────────────────────


def test_complete_uses_instance_default_when_max_tokens_not_given():
    captured: dict = {}
    _complete_with("완결된 답변입니다.", "stop", captured_kwargs=captured)
    assert captured["max_tokens"] == 1024


def test_complete_max_tokens_override_is_passed_through_to_chat_completion():
    """benefit_calculator.py의 두 LLM 호출은 전역 기본값(1024)보다 높은
    예산을 이 파라미터로 넘겨야 한다 - 다른 노드(N1/N5/N9/N13)의
    속도는 그대로 유지하면서 이 호출만 더 넉넉한 토큰 예산을
    쓰게 하는 수단이다.
    """
    captured: dict = {}
    _complete_with("완결된 답변입니다.", "stop", max_tokens=8192, captured_kwargs=captured)
    assert captured["max_tokens"] == 8192


def test_length_truncation_error_message_reflects_overridden_max_tokens():
    """오류 메시지가 인스턴스 기본값(1024)이 아니라 실제로 쓴
    예산(오버라이드값)을 보고해야 운영자가 혼동하지 않는다."""
    with pytest.raises(LLMCallError) as caught:
        _complete_with("중간에 끊긴 답변", "length", max_tokens=8192)

    message = str(caught.value)
    assert "max_new_tokens=8192" in message
    assert "max_new_tokens=1024" not in message
