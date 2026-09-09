"""N4 정책검색 Agent.

Issue #16 (N4~N6): slots로 지원제도 Top-N 후보를 검색해 subsidy_chunks를
반환한다.

입력: GraphState["slots"], GraphState["query_id"],
      GraphState["initial_user_input"](첫 질문 - 검색 질의에 함께 쓴다)
출력: {"subsidy_chunks": list[RetrievedChunk],
      "subsidy_legal_basis_chunks": list[Chunk]}  (LangGraph 노드 관례대로
      전체 State가 아니라 갱신할 필드만 dict로 반환한다)

사용하는 rag_design 모듈:
    - rag_design.vector_store.ChromaVectorStore.search(
          source_type=SourceType.SUBSIDY, query=..., query_id=...,
          top_k=..., search_filter=VectorSearchFilter(region_names=..., as_of=...),
      )
    - rag_design.contracts.SourceType

ChromaVectorStore는 graph.py 조립 시점에 한 번 생성해서 이 노드에 주입한다
(임베딩 모델 로딩 비용이 있어서 매 호출마다 새로 만들지 않는다).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from rag_design.contracts import Chunk, RetrievedChunk, SourceType
from rag_design.vector_store import ChromaVectorStore, VectorSearchFilter

from ..llm_gateway import redact_sensitive_text
from ..policy_conditions import SupportConditionsIndex, filter_candidates
from ..slot_schema import resolve_filter_slots
from ..state import GraphState

DEFAULT_TOP_K = 5
MIN_TOP_K = 1
MAX_TOP_K = 20
SEMANTIC_CANDIDATE_LIMIT = 2_000
# interests가 비어있을 때 쓰는 넓은 검색어. 결정사항 로그의 "interests 없음
# 처리: 넓게 검색 후 안내문구만 첨부, 재질문 없음" 정책을 따른다.
_FALLBACK_QUERY = "생활 지원 복지 서비스"


# 사용자 질문을 질의에 쓸 때의 상한. 되묻기 답변이나 장문이 통째로 들어와
# 임베딩이 흐려지는 것을 막는다.
_MAX_QUESTION_CHARS = 200
# 길이 대신 인사만 제외해 짧은 정책명도 검색에 반영한다.
_GREETINGS = {"안녕", "안녕하세요", "안녕하십니까", "반갑습니다", "hello", "hi"}


def _load_legal_basis_chunks(
    store: ChromaVectorStore, selected: list[RetrievedChunk]
) -> list[Chunk]:
    """Load canonical legal-basis parts for selected policies only."""

    source_ids: list[str] = []
    seen_sources: set[str] = set()
    for candidate in selected:
        source_id = candidate.chunk.metadata["source_id"]
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("selected subsidy chunk must have a non-empty source_id")
        if source_id not in seen_sources:
            seen_sources.add(source_id)
            source_ids.append(source_id)

    legal_basis_chunks: list[Chunk] = []
    seen_chunks: set[str] = set()
    for source_id in source_ids:
        matches = store.get_chunks_by_metadata(
            SourceType.SUBSIDY,
            metadata_equals={
                "source_id": source_id,
                "section_type": "legal_basis",
            },
        )
        for chunk in sorted(
            matches,
            key=lambda item: (
                item.ordinal,
                item.metadata["chunk_part"],
                item.chunk_id,
            ),
        ):
            if chunk.chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk.chunk_id)
            legal_basis_chunks.append(chunk)
    return legal_basis_chunks


def _select_top_policies(
    candidates: list[RetrievedChunk], top_k: int
) -> list[RetrievedChunk]:
    """정책(document) 단위로 상위 ``top_k``개를 고른다.

    예전에는 ``candidates[:top_k]``로 **청크**를 잘랐다. 한 정책은 여러 섹션
    청크로 쪼개져 있고 같은 정책의 섹션들은 서로 텍스트가 비슷해서 유사도
    순위에서 나란히 붙는다. 그래서 "정책 후보 수 5"로 검색해도 상위 5청크가
    전부 한 정책이면 결과가 1건만 나왔다(실측으로 확인된 증상).

    candidates는 이미 유사도순이므로, 정책마다 **가장 잘 맞은 청크 하나**를
    대표로 잡고 서로 다른 정책이 top_k개 찰 때까지 내려간다. rank도 청크
    순위가 아니라 정책 순위가 된다.
    """

    selected: list[RetrievedChunk] = []
    seen_policies: set[str] = set()
    for candidate in candidates:
        policy_id = candidate.chunk.metadata.get("source_id")
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("subsidy chunk must have a non-empty source_id")
        if policy_id in seen_policies:
            continue
        seen_policies.add(policy_id)
        selected.append(replace(candidate, rank=len(selected) + 1))
        if len(selected) >= top_k:
            break
    return selected


def _load_full_policy_chunks(
    store: ChromaVectorStore, selected: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """선택된 정책들의 **모든 섹션 청크**를 metadata 조회로 가져온다.

    유사도 검색이 아니라 ``get_chunks_by_metadata``를 쓴다 - 어떤 섹션이
    질의와 비슷한지는 여기서 중요하지 않고, 그 정책의 전부가 필요하다.
    임베딩을 돌리지 않으므로 검색보다 싸다.

    N9~N11이 각자 top_k를 정해 재검색하던 것을 대체한다. 그쪽 방식은
    top_k에 걸려 조항이 있는 섹션을 아예 못 가져오는 경우가 있었다
    (N11의 ``_RECHECK_TOP_K = 8`` < 긴 문서의 청크 수).

    RetrievedChunk로 감싸 돌려주는 이유는 N9~N11이 이미 그 형태를 기대하기
    때문이다. score는 유사도가 아니므로 0.0으로 두고, 유사도로 정렬하거나
    비교하는 데 쓰지 않는다.
    """

    if not selected:
        return []

    query_id = selected[0].query_id
    full: list[RetrievedChunk] = []
    seen_chunk_ids: set[str] = set()
    for candidate in selected:
        policy_id = candidate.chunk.metadata["source_id"]
        matches = store.get_chunks_by_metadata(
            SourceType.SUBSIDY, metadata_equals={"source_id": policy_id}
        )
        for rank, chunk in enumerate(
            sorted(matches, key=lambda item: (item.ordinal, item.chunk_id)), start=1
        ):
            if chunk.chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk.chunk_id)
            full.append(
                RetrievedChunk(
                    query_id=query_id,
                    chunk=chunk,
                    rank=rank,
                    score=0.0,
                    score_type="not_ranked",
                    retriever_version="metadata:full_document",
                    index_name="subsidy",
                )
            )
    return full


def _build_query(slots: dict, question: str | None = None) -> str:
    """검색 질의를 만든다: 관심사 키워드 + 사용자의 원래 질문.

    예전에는 ``slots["interests"]``만 썼다. 그 결과 **사용자가 실제로 쓴
    문장이 검색에 전혀 반영되지 않았고**, 관심사 키워드가 하나도 안 잡히면
    무조건 ``_FALLBACK_QUERY``로 넓게 검색해서 엉뚱한 정책이 올라왔다
    (2026-08-31 실측: "안녕"으로 시작한 대화에서 관계없는 정책 5건 추천).

    관심사를 앞에 두는 이유는 그것이 이미 정제된 신호이기 때문이고, 질문
    원문을 뒤에 붙이는 이유는 키워드 목록이 놓친 맥락("혼자 사는데 월세가
    부담돼요")을 임베딩이 잡을 수 있기 때문이다.

    ``question``은 이번 턴 발화가 아니라 **첫 질문**이어야 한다
    (``state["initial_user_input"]``). 되묻기 답변("서울, 2000-03-26,
    여성...")을 넣으면 질의가 인적사항으로 오염된다.
    """

    interests = (slots or {}).get("interests") or []
    parts = [str(item).strip() for item in interests if str(item).strip()]

    # 검색 로그·임베딩 provider로 PII가 나가면 안 된다(CONTRIBUTING.md 보안 항목).
    cleaned_question = redact_sensitive_text(question or "").strip()
    if cleaned_question and cleaned_question.rstrip(".!?~ ").casefold() not in _GREETINGS:
        parts.append(cleaned_question[:_MAX_QUESTION_CHARS])

    return " ".join(parts) or _FALLBACK_QUERY


def search_policies(
    state: GraphState,
    store: ChromaVectorStore,
    *,
    top_k: int | None = None,
    support_conditions: SupportConditionsIndex | None = None,
) -> dict:
    """slots 기반으로 지원제도 후보를 검색해 subsidy_chunks를 채운다.

    region은 N2 하드 게이트를 통과한 뒤에만 이 노드가 실행되므로
    slots["region_names"]가 비어있는 경우를 별도로 처리하지 않는다
    (E3: N2 -> N4는 "충분: slots" 경로에서만 온다).

    검색 기준일은 date.today()를 직접 계산하지 않고 state["as_of"]를 쓴다
    (N7 리뷰 피드백 반영) - N4 검색과 N7 시행일 검증이 같은 기준일을
    보게 하기 위함. as_of는 그래프 시작 시점(N1 이전)에 한 번 정해서
    State에 넣어둔다고 가정한다.
    """

    slots = state.get("slots") or {}
    # 테스트나 내부 호출에서 명시한 인자가 가장 우선이고, 서비스 경로에서는
    # 첫 요청에 저장한 GraphState 값을 쓴다. 둘 다 없으면 기존 기본값 5다.
    resolved_top_k = (
        top_k if top_k is not None else state.get("policy_top_k", DEFAULT_TOP_K)
    )
    if isinstance(resolved_top_k, bool) or not isinstance(resolved_top_k, int):
        raise ValueError("policy top_k must be an integer")
    if not MIN_TOP_K <= resolved_top_k <= MAX_TOP_K:
        raise ValueError(f"policy top_k must be between {MIN_TOP_K} and {MAX_TOP_K}")
    query_id = state.get("query_id")
    if not query_id:
        raise ValueError("state['query_id'] is required to search policies")
    as_of = state.get("as_of")
    if as_of is None:
        raise ValueError("state['as_of'] is required to search policies")
    if type(as_of) is not date:
        raise ValueError("state['as_of'] must be a date")

    filter_plan = resolve_filter_slots(slots, reference_date=as_of)
    query = _build_query(slots, state.get("initial_user_input"))
    region_condition = filter_plan["hard"].get("region")
    region_names = tuple(region_condition.get("any_of", ())) if region_condition else ()
    age_condition = filter_plan["hard"].get("birth_date")
    age = age_condition.get("age") if age_condition else None
    search_filter = VectorSearchFilter(
        region_names=region_names,
        as_of=as_of,
        age=age if isinstance(age, int) else None,
        allow_missing_age=True,
    )

    results = store.search(
        SourceType.SUBSIDY,
        query,
        query_id=query_id,
        top_k=SEMANTIC_CANDIDATE_LIMIT,
        search_filter=search_filter,
    )
    filtered = filter_candidates(results, support_conditions, filter_plan)
    selected = _select_top_policies(filtered, resolved_top_k)
    return {
        "subsidy_chunks": selected,
        "subsidy_legal_basis_chunks": _load_legal_basis_chunks(store, selected),
        "subsidy_full_chunks": _load_full_policy_chunks(store, selected),
    }
