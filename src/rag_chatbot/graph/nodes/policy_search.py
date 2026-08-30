"""N4 정책검색 Agent.

Issue #16 (N4~N6): slots로 지원제도 Top-N 후보를 검색해 subsidy_chunks를
반환한다.

입력: GraphState["slots"], GraphState["query_id"]
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

from datetime import date

from rag_design.contracts import SourceType
from rag_design.vector_store import ChromaVectorStore, VectorSearchFilter

from rag_chatbot.graph.state import GraphState

DEFAULT_TOP_K = 5
# interests가 비어있을 때 쓰는 넓은 검색어. 결정사항 로그의 "interests 없음
# 처리: 넓게 검색 후 안내문구만 첨부, 재질문 없음" 정책을 따른다.
_FALLBACK_QUERY = "생활 지원 복지 서비스"


def _build_query(slots: dict) -> str:
    """slots.interests를 이어붙여 검색 질의를 만든다. 없으면 넓게 검색."""

    interests = (slots or {}).get("interests") or []
    text = " ".join(str(item).strip() for item in interests if str(item).strip())
    return text or _FALLBACK_QUERY


def search_policies(
    state: GraphState,
    store: ChromaVectorStore,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> dict:
    """slots 기반으로 지원제도 후보를 검색해 subsidy_chunks를 채운다.

    region은 N2 하드 게이트를 통과한 뒤에만 이 노드가 실행되므로
    slots["region_names"]가 비어있는 경우를 별도로 처리하지 않는다
    (E3: N2 -> N4는 "충분: slots" 경로에서만 온다).
    """

    slots = state.get("slots") or {}
    query_id = state.get("query_id")
    if not query_id:
        raise ValueError("state['query_id'] is required to search policies")

    query = _build_query(slots)
    region_names = tuple(slots.get("region_names") or ())
    search_filter = VectorSearchFilter(region_names=region_names, as_of=date.today())

    results = store.search(
        SourceType.SUBSIDY,
        query,
        query_id=query_id,
        top_k=top_k,
        search_filter=search_filter,
    )
    return {"subsidy_chunks": list(results)}
