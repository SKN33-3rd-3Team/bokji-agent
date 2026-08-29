"""N4 정책검색 Agent.

Issue #16 (N4~N6): slots로 지원제도 Top-N 후보를 검색해 subsidy_chunks를
반환한다.

입력: GraphState["slots"]
출력: GraphState["subsidy_chunks"] (list[RetrievedChunk])

참고할 rag_design 모듈:
    - rag_design.vector_store.ChromaVectorStore.search(
          source_type=SourceType.SUBSIDY, query=..., query_id=...,
          top_k=..., search_filter=VectorSearchFilter(region_names=..., as_of=...),
      )
    - rag_design.index_policy.MetadataFilter (포터블 필터 표현이 필요하면)

TODO(N4): slots -> 검색 질의(query) 변환 로직, top_k 결정, VectorSearchFilter
구성(region_names, as_of=오늘 날짜) 구현.
"""

from __future__ import annotations

from rag_chatbot.graph.state import GraphState


def search_policies(state: GraphState) -> GraphState:
    """slots 기반으로 지원제도 후보를 검색해 subsidy_chunks를 채운다."""

    raise NotImplementedError("N4 정책검색 Agent 미구현 (Issue #16)")
