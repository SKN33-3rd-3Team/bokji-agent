"""N2a(일반 법령 참고 검색)가 사용하는 단일 검색 진입점.

Vector DB·embedding provider 선택은 아직 runtime 미결정 사항이다
(``docs/RAG_DESIGN_PLAN.md`` "동결 계약과 runtime 미결정 사항" 표 참고).
노드 파일이 이 모듈을 거쳐서만 검색을 호출하게 분리해 두면, provider가
정해진 뒤에는 이 파일의 구현부만 교체하면 되고 노드 시그니처는 바뀌지
않는다.

실제로 연결할 때는 다음을 따른다(참고자료 "노드_Agent" 시트 N2a 역할·비고):
1. ``rag_design.index_policy.route_indexes(QueryScope.LAW)``로 법령 논리
   인덱스만 선택한다.
2. 검색 결과 ``RetrievedChunk`` 중 ``chunk.metadata["law_type"] == "law"``
   인 것만 남긴다(전국 적용 법률·시행령만; ``admrul``/``ordin``은 지역
   종속이라 이 경로에서 제외).
3. 남은 chunk의 metadata(``law_type``, ``source_sequence``,
   ``effective_date``/``effective_from``, ``law_name``)로
   ``rag_design.citation.legal_citation_url``을 호출해 인용을 구성한다.

지금은 색인·embedding이 아직 없으므로 근거 없는 결과를 지어내지 않기
위해 항상 빈 리스트를 반환한다.
"""

from __future__ import annotations

from rag_design.contracts import Citation


def search_general_law_citations(query: str, *, top_k: int = 5) -> list[Citation]:
    """전국 적용 법률(``law_type == "law"``)만 대상으로 참고 인용을 찾는다.

    Vector DB 연결 전까지는 항상 빈 리스트를 반환한다.
    """

    del query, top_k  # 실제 연결 전까지는 사용하지 않음.
    return []
