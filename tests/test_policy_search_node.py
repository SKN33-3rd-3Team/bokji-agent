from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
import gc
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag_design.chunking import chunk_document
from rag_design.contracts import (
    Chunk,
    Document,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from rag_chatbot.graph.nodes.policy_search import (
    SEMANTIC_CANDIDATE_LIMIT,
    _CONFIDENT_DISTANCE_THRESHOLD,
    _build_query,
    _filter_relevant_candidates,
    search_policies,
)
from rag_chatbot.llm import FailingLLMClient, FakeLLMClient, LLMCallError

try:
    import chromadb as _chromadb  # noqa: F401
except Exception:
    CHROMA_AVAILABLE = False
else:
    CHROMA_AVAILABLE = True

FIXTURES = Path(__file__).parent / "fixtures"


def load_documents() -> tuple[Document, ...]:
    return tuple(
        Document.from_dict(json.loads(line))
        for line in (FIXTURES / "documents.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )


@unittest.skipUnless(CHROMA_AVAILABLE, "chromadb is not installed")
class SearchPoliciesNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        config = VectorStoreConfig(
            persist_directory=Path(self.temporary_directory.name) / "index",
            collection_prefix="test_n4",
        )
        self.store = ChromaVectorStore(HashEmbeddingProvider(64), config)

        documents = load_documents()
        self.subsidy = next(d for d in documents if d.source_type is SourceType.SUBSIDY)
        subsidy_chunks = chunk_document(self.subsidy)
        self.store.sync_snapshot(
            SourceType.SUBSIDY, subsidy_chunks, snapshot_id="subsidy-001"
        )

    def tearDown(self) -> None:
        del self.store
        gc.collect()
        self.temporary_directory.cleanup()

    def test_search_policies_returns_subsidy_chunks(self) -> None:
        state = {
            "query_id": "q-1",
            "as_of": date(2026, 1, 1),
            "slots": {"interests": ["유아학비"], "region_names": []},
        }

        update = search_policies(state, self.store, top_k=3)

        self.assertIn("subsidy_chunks", update)
        chunks = update["subsidy_chunks"]
        self.assertGreater(len(chunks), 0)
        self.assertTrue(all(c.index_name == "subsidy" for c in chunks))
        self.assertTrue(all(c.query_id == "q-1" for c in chunks))

    def test_search_policies_falls_back_to_broad_query_when_no_interests(self) -> None:
        state = {
            "query_id": "q-2",
            "as_of": date(2026, 1, 1),
            "slots": {"region_names": []},
        }

        update = search_policies(state, self.store, top_k=3)

        self.assertIn("subsidy_chunks", update)
        self.assertGreater(len(update["subsidy_chunks"]), 0)

    def test_unknown_region_survives_n4_without_bypassing_profile_conditions(self) -> None:
        document = replace(self.subsidy, metadata={
            **self.subsidy.metadata, "region_scope": "unknown", "region_names": [],
        })
        source_chunks = chunk_document(document)
        self.store.sync_snapshot(SourceType.SUBSIDY, source_chunks,
                                 snapshot_id="n4-unknown-region")
        state = {"query_id": "q-unknown-n4", "as_of": date(2026, 1, 1),
                 "initial_user_input": "유아학비 지원", "slots": {
                     "region_names": ["부산광역시"], "gender": "female",
                 }}
        for required_gender, expected_count in (("JA0102", 1), ("JA0101", 0)):
            with self.subTest(required_gender=required_gender):
                result = search_policies(state, self.store, support_conditions={
                    document.source_id: ConfigurableTopKTests._conditions(required_gender),
                })
                self.assertEqual(len(result["subsidy_chunks"]), expected_count)
                if expected_count:
                    self.assertEqual(result["subsidy_chunks"][0].rank, 1)
                    self.assertEqual({c.chunk.chunk_id for c in result["subsidy_full_chunks"]},
                                     {c.chunk_id for c in source_chunks})
                    for candidate in result["subsidy_chunks"] + result["subsidy_full_chunks"]:
                        self.assertEqual(candidate.chunk.metadata["region_scope"], "unknown")
                        self.assertEqual(candidate.chunk.metadata["region_names"], [])
                else:
                    self.assertEqual(result["subsidy_full_chunks"], [])
                    self.assertEqual(result["subsidy_legal_basis_chunks"], [])

    def test_search_policies_requires_query_id(self) -> None:
        with self.assertRaises(ValueError):
            search_policies({"as_of": date(2026, 1, 1), "slots": {}}, self.store)

    def test_search_policies_requires_as_of(self) -> None:
        with self.assertRaises(ValueError):
            search_policies({"query_id": "q-3", "slots": {}}, self.store)

    def test_search_policies_rejects_non_date_as_of(self) -> None:
        for value in ("", 0, False, "2026-01-01", datetime(2026, 1, 1)):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, r"state\['as_of'\] must be a date"
                ):
                    search_policies(
                        {"query_id": "q-invalid-as-of", "as_of": value, "slots": {}},
                        self.store,
                    )


class BuildQueryTests(unittest.TestCase):
    """검색 질의 조립 (2026-08-31 변경).

    예전에는 interests 키워드만 썼고 사용자의 실제 문장은 검색에 전혀
    반영되지 않았다. "안녕"으로 시작한 대화가 고정 fallback 질의로 넘어가
    관계없는 정책 5건이 추천된 일이 있었다.
    """

    def test_interests_and_question_are_combined(self) -> None:
        query = _build_query({"interests": ["육아"]}, "아이 키우는데 지원 뭐 있나요")
        self.assertIn("육아", query)
        self.assertIn("아이 키우는데", query)

    def test_question_alone_is_enough_when_no_interest_keyword_matched(self) -> None:
        # 관심사 키워드 표에 없는 표현이어도 질문 자체로 검색할 수 있어야 한다.
        query = _build_query({"interests": []}, "혼자 사는데 월세가 너무 부담돼요")
        self.assertIn("월세", query)

    def test_greeting_too_short_to_be_query_material_uses_fallback(self) -> None:
        self.assertEqual(_build_query({"interests": []}, "안녕"), "생활 지원 복지 서비스")

    def test_no_material_at_all_uses_fallback(self) -> None:
        self.assertEqual(_build_query({"interests": []}, None), "생활 지원 복지 서비스")

    def test_query_never_carries_pii(self) -> None:
        # 검색 로그와 임베딩 provider로 PII가 나가면 안 된다.
        query = _build_query(
            {"interests": ["주거"]},
            "제 번호는 010-1234-5678이고 1990년 3월 15일생인데 월세 지원 있나요",
        )
        self.assertNotIn("010-1234-5678", query)
        self.assertNotIn("1990년 3월 15일", query)
        self.assertIn("월세", query)

    def test_long_question_is_truncated(self) -> None:
        query = _build_query({"interests": []}, "월세" * 300)
        self.assertLessEqual(len(query), 220)


class StripProfileFromQueryTests(unittest.TestCase):
    """질의에서 중복 인적사항을 걷어낸다 (2026-09-15 변경).

    지역·성별·소득구간·장애유무는 이미 슬롯으로 뽑혀 VectorSearchFilter와
    filter_candidates가 처리한다. 같은 정보를 임베딩에도 넣으면 주제어를
    밀어내는 중복 노이즈다(Dev 100문항: Top-5 적중 62/95 -> 85/95).
    """

    def test_profile_preamble_is_removed_but_topic_survives(self) -> None:
        query = _build_query(
            {"interests": []},
            "서울 거주 1990년 3월 1일생 남성입니다. 중위소득 50%이고 장애는 "
            "없으며 근로 중입니다. 근로장려금 지원 내용을 알려주세요.",
        )
        self.assertIn("근로장려금", query)
        for noise in ("서울", "남성", "중위소득", "장애는 없"):
            self.assertNotIn(noise, query)

    def test_topic_is_kept_when_it_sits_before_a_generic_closing_sentence(self) -> None:
        """마지막 문장이 일반 문형이어도 주제어를 잃지 않는다.

        "마지막 문장만 남긴다" 식의 규칙이 깨지는 자리다 - 그 규칙은 이
        질문에서 "관련 지원이 있나요?"만 남겨 상위권 정책을 권외로 보냈다.
        """

        query = _build_query(
            {"interests": []},
            "경남 거주 1980년 4월 18일생 여성입니다. 중위소득 100%, 장애 없음, "
            "어업에 종사하며 어선 집어등을 고효율 LED로 교체하려 합니다. "
            "관련 지원이 있나요?",
        )
        self.assertIn("집어등", query)
        self.assertIn("LED", query)
        # 직업·종사 분야는 주제어와 겹치므로 일부러 남긴다.
        self.assertIn("어업", query)
        self.assertNotIn("경남", query)

    def test_second_mention_is_the_question_not_the_profile(self) -> None:
        """같은 표현이 두 번 나오면 뒤엣것은 자기소개가 아니라 질문이다."""

        query = _build_query(
            {"interests": []},
            "울산 거주 2009년 9월 5일생 여성, 중위소득 80%, 비장애, 학생입니다. "
            "중위소득 60% 이하 대상 기준을 알려주세요.",
        )
        self.assertIn("중위소득 60% 이하", query)
        self.assertNotIn("80%", query)

    def test_disability_as_a_question_topic_is_kept(self) -> None:
        query = _build_query(
            {"interests": []},
            "전남 거주 1967년 6월 30일생 여성, 중위소득 60%, 비장애, 어업 "
            "종사자입니다. 청각장애가 있을 때 국선 심판변론인을 받을 수 있는지 "
            "알려주세요.",
        )
        self.assertIn("청각장애", query)
        self.assertIn("국선 심판변론인", query)

    def test_age_condition_in_the_question_is_not_stripped(self) -> None:
        # "70세 이상"은 신청인의 나이가 아니라 제도의 자격 요건이다.
        query = _build_query(
            {"interests": []}, "70세 이상 해양사고관련자의 지원 조건을 알려주세요."
        )
        self.assertIn("70세 이상", query)

    def test_profile_only_utterance_falls_back_to_the_original_text(self) -> None:
        # 전부 걷어내면 빈 질의가 되고, 빈 질의는 _FALLBACK_QUERY로 떨어져
        # 아무 정책이나 올라온다. 그럴 바엔 원문을 쓴다.
        query = _build_query({"interests": []}, "서울 거주 여성입니다.")
        self.assertNotEqual(query, "생활 지원 복지 서비스")
        self.assertIn("서울", query)

    def test_strip_can_be_disabled_for_before_after_diagnosis(self) -> None:
        raw = _build_query(
            {"interests": []}, "부산 거주 남성입니다. 월세보증 알려주세요.",
            strip_profile=False,
        )
        self.assertIn("부산", raw)


class _SequencedLLMClient:
    """호출마다 순서대로 다른 응답을 돌려주는 가짜 클라이언트.

    ``FakeLLMClient``는 매번 같은 응답만 돌려줘서, "첫 호출과 재확인 호출이
    다른 판정을 낸다"는 시나리오(2026-09-17 재확인 로직)를 테스트할 수 없다.
    목록이 바닥나면 마지막 응답을 계속 돌려준다.
    """

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def complete(self, prompt: str, *, system: str | None = None, max_tokens=None) -> str:
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        return self.responses[index]


class RelevanceFilterTests(unittest.TestCase):
    """N4 관련성 게이트 (2026-09-16, measure_retrieval_distance.py 실측 반영).

    top-1 cosine_distance로는 보류 대상과 정상 질문 분포가 겹쳐서
    (measure_retrieval_distance.py 실측: 정상 질문 142건 중 37건이 보류
    대상 최저 거리보다 멀다) 거리 임계값을 쓸 수 없다. 대신 LLM에게 직접
    "이 후보가 질문과 관련 있는가"를 묻는다.
    """

    @staticmethod
    def _candidate(source_id: str, rank: int = 1) -> RetrievedChunk:
        text = f"정책 {source_id} 원문"
        chunk = Chunk(
            schema_version=SCHEMA_VERSION,
            chunk_id=f"chunk-{source_id}",
            doc_id=f"subsidy:{source_id}:v1",
            source_type=SourceType.SUBSIDY,
            text=text,
            heading_path=("지원대상",),
            ordinal=0,
            citation_locator="지원대상",
            content_hash=compute_content_hash(text),
            metadata={"source_id": source_id, "source_name": f"{source_id} 지원사업"},
        )
        return RetrievedChunk(
            query_id="q-relevance",
            chunk=chunk,
            rank=rank,
            # _CONFIDENT_DISTANCE_THRESHOLD(0.118)보다 먼 값을 써야 이
            # 테스트들이 실제로 LLM 판정 경로를 태운다 - 그보다 가까우면
            # 거리 사전 필터가 LLM을 부르지도 않고 후보를 그대로 통과시킨다.
            score=0.2 + rank / 100,
            score_type="cosine_distance",
            retriever_version="test:fixture",
            index_name="subsidy",
        )

    def test_no_llm_client_keeps_all_candidates(self) -> None:
        candidates = [self._candidate("a"), self._candidate("b")]
        result = _filter_relevant_candidates(None, "질문", candidates)
        self.assertEqual(result, candidates)

    def test_confident_top1_distance_skips_llm_entirely(self) -> None:
        """거리 기반 사전 필터(2026-09-17) - 최상위 후보가 이미 충분히
        가까우면(측정된 정상 질문 분포의 중앙값 이하) LLM 판정 자체를
        건너뛴다. 150문항 전체 실측에서 스니펫 확장 뒤에도 남아있던
        관련성 게이트의 판정 편차(정상 질문 오탐)를, 애초에 판정을
        태우지 않는 절반가량의 안전 구간을 만들어 줄인다."""

        close = replace(
            self._candidate("a"), score=_CONFIDENT_DISTANCE_THRESHOLD - 0.01
        )
        llm = FakeLLMClient('{"relevant_policy_ids": []}')  # 불렸다면 다 걸러졌을 응답
        result = _filter_relevant_candidates(llm, "질문", [close])
        self.assertEqual(result, [close])
        self.assertEqual(llm.calls, [])

    def test_borderline_top1_distance_still_uses_llm_judgment(self) -> None:
        far = replace(
            self._candidate("a"), score=_CONFIDENT_DISTANCE_THRESHOLD + 0.01
        )
        llm = FakeLLMClient('{"relevant_policy_ids": []}')
        result = _filter_relevant_candidates(llm, "정부가 매달 300만원 준다는 정책", [far])
        self.assertEqual(result, [])
        self.assertGreater(len(llm.calls), 0)

    def test_confident_threshold_only_confirms_the_single_best_candidate(self) -> None:
        """1등만 거리로 확정하고, 나머지는 순서와 무관하게 LLM 판정을 거친다
        (2026-09-18 - 후보 전체를 봐주면 Citation Precision이 떨어지는 걸
        150+100+준-Holdout 250문항 실측으로 확인한 뒤 1등만으로 좁혔다)."""

        near = replace(self._candidate("a"), score=_CONFIDENT_DISTANCE_THRESHOLD - 0.01)
        far = replace(self._candidate("b"), score=_CONFIDENT_DISTANCE_THRESHOLD + 0.05)
        llm = FakeLLMClient('{"relevant_policy_ids": []}')
        result = _filter_relevant_candidates(llm, "질문", [far, near])
        # near(1등)는 확정되고, far(2등)는 판정받아 무관하다고 걸러진다.
        self.assertEqual(result, [near])
        self.assertEqual(len(llm.calls), 1)

    def test_confident_top1_still_lets_llm_keep_a_relevant_runner_up(self) -> None:
        """1등이 확정돼도 2등 이하가 실제로 관련 있다고 판정되면 같이 남는다 -
        1등 확정은 "2등 이하를 무조건 버린다"는 뜻이 아니라 "2등 이하도
        여전히 판정받는다"는 뜻이다."""

        near = replace(self._candidate("a"), score=_CONFIDENT_DISTANCE_THRESHOLD - 0.01)
        far = replace(self._candidate("b"), score=_CONFIDENT_DISTANCE_THRESHOLD + 0.05)
        llm = FakeLLMClient('{"relevant_policy_ids": ["b"]}')
        result = _filter_relevant_candidates(llm, "질문", [far, near])
        self.assertEqual([c.chunk.metadata["source_id"] for c in result], ["a", "b"])

    def test_only_candidate_confident_skips_llm_with_nothing_left_to_judge(self) -> None:
        candidates = [
            replace(self._candidate("a"), score=_CONFIDENT_DISTANCE_THRESHOLD - 0.01)
        ]
        llm = FakeLLMClient('{"relevant_policy_ids": []}')
        result = _filter_relevant_candidates(llm, "질문", candidates)
        self.assertEqual(result, candidates)
        self.assertEqual(llm.calls, [])

    def test_no_question_keeps_all_candidates(self) -> None:
        candidates = [self._candidate("a")]
        llm = FakeLLMClient('{"relevant_policy_ids": []}')
        result = _filter_relevant_candidates(llm, None, candidates)
        self.assertEqual(result, candidates)
        self.assertEqual(llm.calls, [])  # 애초에 부르지 않는다

    def test_keeps_only_ids_the_llm_marks_relevant(self) -> None:
        candidates = [self._candidate("a"), self._candidate("b"), self._candidate("c")]
        llm = FakeLLMClient('{"relevant_policy_ids": ["a", "c"]}')
        result = _filter_relevant_candidates(llm, "실제 질문", candidates)
        self.assertEqual([c.chunk.metadata["source_id"] for c in result], ["a", "c"])

    def test_empty_relevant_ids_drops_every_candidate_after_agreeing_twice(self) -> None:
        """허위 정책·프롬프트 인젝션처럼 후보가 전부 무관하면 다 걷어낸다 -
        이 결과가 빈 리스트로 claim_plan까지 이어지면 evidence_gate가 이미
        검증된 경로로 보류한다(claim_plan == [] -> NO_EVIDENCE).

        0건이라는 판정은 재확인을 한 번 거친다(아래 재확인 테스트들 참고) -
        두 번 다 무관하다고 해야 확정되므로 호출이 2번 일어난다."""

        candidates = [self._candidate("a"), self._candidate("b")]
        llm = FakeLLMClient('{"relevant_policy_ids": []}')
        result = _filter_relevant_candidates(llm, "정부가 매달 300만원 준다는 정책", candidates)
        self.assertEqual(result, [])
        self.assertEqual(len(llm.calls), 2)

    def test_llm_naming_unknown_id_is_ignored(self) -> None:
        candidates = [self._candidate("a")]
        llm = FakeLLMClient('{"relevant_policy_ids": ["not-a-real-id"]}')
        result = _filter_relevant_candidates(llm, "질문", candidates)
        self.assertEqual(result, [])

    def test_disagreement_on_recheck_trusts_the_call_that_kept_a_candidate(self) -> None:
        """0건이라는 첫 판정과 재확인 판정이 갈리면(자기일관성 없음),
        후보가 남는 쪽을 믿는다 - 보류가 답변 누락보다 되돌리기 어려운
        실패라는 우선순위 때문이다(2026-09-17, dev-earned-income-002 실측
        재현: 같은 코드가 같은 정상 질문에 대해 실행마다 다르게 판단했다)."""

        candidates = [self._candidate("a"), self._candidate("b")]
        llm = _SequencedLLMClient(
            [
                '{"relevant_policy_ids": []}',
                '{"relevant_policy_ids": ["a"]}',
            ]
        )
        result = _filter_relevant_candidates(llm, "근로장려금 지원 내용을 알려주세요", candidates)
        self.assertEqual([c.chunk.metadata["source_id"] for c in result], ["a"])
        self.assertEqual(len(llm.calls), 2)

    def test_agreement_on_recheck_confirms_zero_candidates(self) -> None:
        """재확인도 0건이면(자기일관성 있음) 그때만 전부 무관을 확정한다."""

        candidates = [self._candidate("a")]
        llm = _SequencedLLMClient(
            ['{"relevant_policy_ids": []}', '{"relevant_policy_ids": []}']
        )
        result = _filter_relevant_candidates(llm, "허위 정책 질문", candidates)
        self.assertEqual(result, [])
        self.assertEqual(len(llm.calls), 2)

    def test_first_call_with_a_kept_candidate_skips_recheck(self) -> None:
        """이미 후보가 남는 정상 경로는 재확인하지 않는다(비용 절감)."""

        candidates = [self._candidate("a"), self._candidate("b")]
        llm = FakeLLMClient('{"relevant_policy_ids": ["a", "b"]}')
        result = _filter_relevant_candidates(llm, "질문", candidates)
        self.assertEqual(len(result), 2)
        self.assertEqual(len(llm.calls), 1)

    def test_recheck_call_failure_fails_open(self) -> None:
        """0건이라 재확인하러 갔는데 그 재확인 호출 자체가 실패하면, 추측하지
        않고 원래 후보를 그대로 통과시킨다(fail-open)."""

        candidates = [self._candidate("a")]
        llm = _SequencedLLMClient(["{\"relevant_policy_ids\": []}"])
        original_complete = llm.complete
        calls = {"n": 0}

        def flaky_complete(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise LLMCallError("재확인 호출 실패")
            return original_complete(*args, **kwargs)

        llm.complete = flaky_complete
        result = _filter_relevant_candidates(llm, "질문", candidates)
        self.assertEqual(result, candidates)

    def test_call_failure_fails_open(self) -> None:
        candidates = [self._candidate("a")]
        result = _filter_relevant_candidates(FailingLLMClient(), "질문", candidates)
        self.assertEqual(result, candidates)

    def test_malformed_json_fails_open(self) -> None:
        candidates = [self._candidate("a")]
        llm = FakeLLMClient("이것은 JSON이 아닙니다")
        result = _filter_relevant_candidates(llm, "질문", candidates)
        self.assertEqual(result, candidates)

    def test_wrong_shaped_json_fails_open(self) -> None:
        candidates = [self._candidate("a")]
        llm = FakeLLMClient('{"relevant_policy_ids": "a"}')  # 리스트가 아님
        result = _filter_relevant_candidates(llm, "질문", candidates)
        self.assertEqual(result, candidates)

    def test_prompt_lists_every_candidate_and_the_question(self) -> None:
        candidates = [self._candidate("a"), self._candidate("b")]
        llm = FakeLLMClient('{"relevant_policy_ids": ["a", "b"]}')
        _filter_relevant_candidates(llm, "근로장려금 신청 조건이 뭔가요?", candidates)
        prompt = llm.calls[0]["prompt"]
        self.assertIn("근로장려금 신청 조건이 뭔가요?", prompt)
        self.assertIn("a", prompt)
        self.assertIn("b", prompt)


class ConfigurableTopKTests(unittest.TestCase):
    class _Store:
        def __init__(self, results=(), exact_chunks=()) -> None:
            self.top_k = None
            self.search_filter = None
            self.results = results
            self.exact_chunks = tuple(exact_chunks)
            self.exact_calls: list[tuple[SourceType, dict]] = []

        def search(self, source_type, query, *, query_id, top_k, search_filter):
            self.top_k = top_k
            self.search_filter = search_filter
            return self.results

        def get_chunks_by_metadata(self, source_type, *, metadata_equals, **kwargs):
            self.exact_calls.append((source_type, dict(metadata_equals)))
            return tuple(
                chunk
                for chunk in self.exact_chunks
                if chunk.source_type is source_type
                and all(
                    chunk.metadata.get(key) == value
                    for key, value in metadata_equals.items()
                )
            )

    def _state(self, top_k=7):
        return {
            "query_id": "q-top-k",
            "as_of": date(2026, 1, 1),
            "slots": {"region_names": []},
            "policy_top_k": top_k,
        }

    def test_uses_top_k_from_graph_state(self) -> None:
        store = self._Store(
            tuple(self._candidate(f"service-{rank}", rank) for rank in range(1, 11))
        )
        result = search_policies(self._state(7), store)
        self.assertEqual(store.top_k, SEMANTIC_CANDIDATE_LIMIT)
        self.assertEqual(len(result["subsidy_chunks"]), 7)

    def test_explicit_node_argument_overrides_state(self) -> None:
        store = self._Store(
            tuple(self._candidate(f"service-{rank}", rank) for rank in range(1, 11))
        )
        result = search_policies(self._state(7), store, top_k=3)
        self.assertEqual(store.top_k, SEMANTIC_CANDIDATE_LIMIT)
        self.assertEqual(len(result["subsidy_chunks"]), 3)

    def test_rejects_out_of_range_top_k(self) -> None:
        for value in (0, 21, True, "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                search_policies(self._state(value), self._Store())

    def test_llm_client_drops_irrelevant_candidates_end_to_end(self) -> None:
        """N4 관련성 게이트가 실제로 search_policies()에 배선돼 있는지 확인한다."""

        # rank 20 -> score 2.0: _CONFIDENT_DISTANCE_THRESHOLD(0.118)보다 멀어야
        # 거리 사전 필터를 통과해 실제로 LLM 판정 경로를 태운다.
        store = self._Store((self._candidate("service-1", 20),))
        state = self._state()
        state["initial_user_input"] = "정부가 매달 300만원 준다는 정책 알려줘"
        llm = FakeLLMClient('{"relevant_policy_ids": []}')

        result = search_policies(state, store, llm_client=llm)

        self.assertEqual(result["subsidy_chunks"], [])
        self.assertEqual(result["subsidy_full_chunks"], [])
        self.assertEqual(result["subsidy_legal_basis_chunks"], [])
        # 0건 판정은 재확인을 한 번 더 거친다(2026-09-17 재확인 로직) - 같은
        # FakeLLMClient가 두 번 다 무관하다고 답해서 최종적으로 0건이 된다.
        self.assertEqual(len(llm.calls), 2)

    def test_no_llm_client_preserves_previous_behaviour(self) -> None:
        store = self._Store((self._candidate("service-1", 1),))
        result = search_policies(self._state(), store)
        self.assertEqual(len(result["subsidy_chunks"]), 1)

    def test_self_international_age_is_connected_to_search_filter(self) -> None:
        store = self._Store()
        state = self._state()
        state["slots"].update(
            {
                "birth_date": "1960-12-31",
                "age": 65,
                "age_year_based": 66,
                "age_subject": "self",
            }
        )

        search_policies(state, store)

        self.assertEqual(store.search_filter.age, 65)
        self.assertTrue(store.search_filter.allow_missing_age)

    def test_non_self_age_is_not_used_as_search_filter(self) -> None:
        store = self._Store()
        state = self._state()
        state["slots"].update(
            {
                "birth_date": "2020-01-01",
                "age": 6,
                "age_year_based": 6,
                "age_subject": "child",
            }
        )

        search_policies(state, store)

        self.assertIsNone(store.search_filter.age)

    @staticmethod
    def _candidate(
        source_id: str, rank: int, *, chunk_suffix: str = ""
    ) -> RetrievedChunk:
        text = f"정책 {source_id}{chunk_suffix}"
        chunk = Chunk(
            schema_version=SCHEMA_VERSION,
            chunk_id=f"chunk-{source_id}{chunk_suffix}",
            doc_id=f"subsidy:{source_id}:v1",
            source_type=SourceType.SUBSIDY,
            text=text,
            heading_path=("지원대상",),
            ordinal=0,
            citation_locator="지원대상",
            content_hash=compute_content_hash(text),
            metadata={"source_id": source_id},
        )
        return RetrievedChunk(
            query_id="q-top-k",
            chunk=chunk,
            rank=rank,
            score=rank / 10,
            score_type="cosine_distance",
            retriever_version="test:fixture",
            index_name="subsidy",
        )

    @staticmethod
    def _legal_basis_chunk(
        source_id: str, *, ordinal: int, chunk_part: int, chunk_id: str
    ) -> Chunk:
        text = f"정책 {source_id}\n근거법령\n\n테스트법(제{chunk_part + 1}조)"
        return Chunk(
            schema_version=SCHEMA_VERSION,
            chunk_id=chunk_id,
            doc_id=f"subsidy:{source_id}:v1",
            source_type=SourceType.SUBSIDY,
            text=text,
            heading_path=("근거법령",),
            ordinal=ordinal,
            citation_locator="근거법령",
            content_hash=compute_content_hash(text),
            metadata={
                "source_id": source_id,
                "section_type": "legal_basis",
                "chunk_part": chunk_part,
                "chunk_part_count": 2,
            },
        )

    @staticmethod
    def _conditions(*active: str) -> dict[str, str | None]:
        codes = (
            "JA0101",
            "JA0102",
            "JA0201",
            "JA0202",
            "JA0203",
            "JA0204",
            "JA0205",
            "JA0326",
            "JA0327",
            "JA0328",
            "JA0313",
            "JA0314",
            "JA0315",
            "JA0316",
            "JA0317",
            "JA0318",
            "JA0319",
            "JA0320",
            "JA0322",
            "JA1101",
            "JA1102",
            "JA1103",
        )
        return {code: "Y" if code in active else None for code in codes}

    def test_sidecar_postfilter_backfills_and_reranks_semantic_candidates(self) -> None:
        candidates = tuple(
            self._candidate(source_id, rank)
            for rank, source_id in enumerate(
                ("male-only", "unknown-service", "female-1", "female-2"),
                start=1,
            )
        )
        store = self._Store(candidates)
        state = self._state(top_k=3)
        state["slots"]["gender"] = "female"
        sidecar = {
            "male-only": self._conditions("JA0101"),
            "female-1": self._conditions("JA0102"),
            "female-2": self._conditions("JA0102"),
        }

        result = search_policies(state, store, support_conditions=sidecar)

        kept = result["subsidy_chunks"]
        self.assertEqual(
            [item.chunk.metadata["source_id"] for item in kept],
            ["unknown-service", "female-1", "female-2"],
        )
        self.assertEqual([item.rank for item in kept], [1, 2, 3])
        # 근거법령 조회는 선택된 정책마다 정확히 한 번씩.
        self.assertEqual(
            [call for call in store.exact_calls if "section_type" in call[1]],
            [
                (
                    SourceType.SUBSIDY,
                    {"source_id": source_id, "section_type": "legal_basis"},
                )
                for source_id in ("unknown-service", "female-1", "female-2")
            ],
        )
        # 전체 섹션 조회도 선택된 정책마다 한 번씩(section_type 없이).
        self.assertEqual(
            [call for call in store.exact_calls if "section_type" not in call[1]],
            [
                (SourceType.SUBSIDY, {"source_id": source_id})
                for source_id in ("unknown-service", "female-1", "female-2")
            ],
        )

    def test_selected_sources_load_sorted_deduplicated_legal_basis_parts(self) -> None:
        candidates = (
            self._candidate("service-a", 7, chunk_suffix="-one"),
            self._candidate("service-a", 8, chunk_suffix="-two"),
            self._candidate("service-b", 9),
        )
        a_part_1 = self._legal_basis_chunk(
            "service-a", ordinal=2, chunk_part=1, chunk_id="basis-a-1"
        )
        a_part_0 = self._legal_basis_chunk(
            "service-a", ordinal=2, chunk_part=0, chunk_id="basis-a-0"
        )
        b_part = self._legal_basis_chunk(
            "service-b", ordinal=1, chunk_part=0, chunk_id="basis-b-0"
        )
        store = self._Store(
            candidates,
            exact_chunks=(a_part_1, a_part_0, a_part_0, b_part),
        )

        result = search_policies(self._state(3), store)

        # top_k=3이지만 후보 3개 중 둘(candidates[0], [1])이 같은 service-a라
        # 정책 단위로는 2건이다. 예전에는 청크를 잘라서 service-a가 두 번
        # 들어갔고, 그래서 "정책 후보 수 3"인데 실제로는 2개 정책만 나왔다.
        self.assertEqual(
            result["subsidy_chunks"],
            [replace(candidates[0], rank=1), replace(candidates[2], rank=2)],
        )
        self.assertEqual(
            [item.chunk.metadata["source_id"] for item in result["subsidy_chunks"]],
            ["service-a", "service-b"],
        )
        self.assertEqual([item.rank for item in candidates], [7, 8, 9])
        self.assertEqual(
            [chunk.chunk_id for chunk in result["subsidy_legal_basis_chunks"]],
            ["basis-a-0", "basis-a-1", "basis-b-0"],
        )
        self.assertEqual(
            [call for call in store.exact_calls if "section_type" in call[1]],
            [
                (
                    SourceType.SUBSIDY,
                    {"source_id": "service-a", "section_type": "legal_basis"},
                ),
                (
                    SourceType.SUBSIDY,
                    {"source_id": "service-b", "section_type": "legal_basis"},
                ),
            ],
        )

    def test_top_k_counts_policies_not_chunks(self) -> None:
        """같은 정책의 섹션이 여러 개 올라와도 정책 하나로 센다."""

        candidates = (
            self._candidate("service-a", 1, chunk_suffix="-one"),
            self._candidate("service-a", 2, chunk_suffix="-two"),
            self._candidate("service-a", 3, chunk_suffix="-three"),
            self._candidate("service-b", 4),
            self._candidate("service-c", 5),
        )
        store = self._Store(candidates)

        result = search_policies(self._state(3), store)

        self.assertEqual(
            [item.chunk.metadata["source_id"] for item in result["subsidy_chunks"]],
            ["service-a", "service-b", "service-c"],
        )
        self.assertEqual([item.rank for item in result["subsidy_chunks"]], [1, 2, 3])

    def test_full_policy_chunks_are_loaded_for_every_selected_policy(self) -> None:
        """N9~N11이 문서 전체를 볼 수 있도록 선택된 정책의 모든 섹션을 싣는다."""

        candidates = (
            self._candidate("service-a", 1),
            self._candidate("service-b", 2),
        )
        store = self._Store(candidates)

        result = search_policies(self._state(2), store)

        self.assertEqual(
            [call for call in store.exact_calls if "section_type" not in call[1]],
            [
                (SourceType.SUBSIDY, {"source_id": "service-a"}),
                (SourceType.SUBSIDY, {"source_id": "service-b"}),
            ],
        )
        self.assertIn("subsidy_full_chunks", result)

if __name__ == "__main__":
    unittest.main()
