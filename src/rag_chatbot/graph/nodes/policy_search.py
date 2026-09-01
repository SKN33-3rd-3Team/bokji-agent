"""N4 정책검색 Agent.

Issue #16 (N4~N6): slots로 지원제도 Top-N 후보를 검색해 subsidy_chunks를
반환한다.

입력: GraphState["slots"], GraphState["query_id"],
      GraphState["initial_user_input"](첫 질문 - 검색 질의에 함께 쓴다)
출력: {"subsidy_chunks": list[RetrievedChunk]}  (LangGraph 노드 관례대로
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

from rag_design.contracts import SourceType
from rag_design.vector_store import ChromaVectorStore, VectorSearchFilter

from ..llm_gateway import redact_sensitive_text
from ..slot_schema import resolve_filter_slots
from ..state import GraphState

DEFAULT_TOP_K = 5
MIN_TOP_K = 1
MAX_TOP_K = 20
# interests가 비어있을 때 쓰는 넓은 검색어. 결정사항 로그의 "interests 없음
# 처리: 넓게 검색 후 안내문구만 첨부, 재질문 없음" 정책을 따른다.
_FALLBACK_QUERY = "생활 지원 복지 서비스"


# 사용자 질문을 질의에 쓸 때의 상한. 되묻기 답변이나 장문이 통째로 들어와
# 임베딩이 흐려지는 것을 막는다.
_MAX_QUESTION_CHARS = 200
# 이보다 짧은 질문은 검색 재료로 보지 않는다. "안녕"(2자) 같은 인사말을
# 그대로 질의로 쓰면 의미 없는 벡터로 검색하게 되기 때문이다.
# 형태만 보는 휴리스틱이라 완벽하지 않다 - 긴 인사말은 걸러지지 않는다.
_MIN_QUESTION_CHARS = 6

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
    if len(cleaned_question) >= _MIN_QUESTION_CHARS:
        parts.append(cleaned_question[:_MAX_QUESTION_CHARS])

    return " ".join(parts) or _FALLBACK_QUERY


def search_policies(
    state: GraphState,
    store: ChromaVectorStore,
    *,
    top_k: int | None = None,
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

    query = _build_query(slots, state.get("initial_user_input"))
    region_names = tuple(slots.get("region_names") or ())
    filter_plan = resolve_filter_slots(slots)
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
        top_k=resolved_top_k,
        search_filter=search_filter,
    )
    return {"subsidy_chunks": list(results)}
