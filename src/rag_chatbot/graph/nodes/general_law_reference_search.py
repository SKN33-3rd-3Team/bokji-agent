"""N2a: 일반 법령 참고 검색 노드.

N2가 지역 정보를 부족하다고 판정했을 때만(그래프 조건부 엣지, Edge E4)
실행된다. 지역 확인 없이도 답할 수 있는 전국 적용 법률(``law_type ==
"law"``)만 대상으로 ``slots.interests``와 ``user_input``을 기준으로 참고
링크를 찾는다. 자치법규(``ordin``)는 지역 종속이라 이 경로에서 제외한다
(참고자료 "노드_Agent" 시트 N2a "확인 필요" 비고).

State 계약(참고자료 "State_연결부" 시트 E4/E5):
- 입력: ``slots.interests``, ``user_input``
- 출력: ``general_law_references``
"""

from __future__ import annotations

from ..llm_gateway import redact_sensitive_text
from ..retrieval_gateway import search_general_law_citations
from ..state import GraphState


def search_general_law_references(state: GraphState) -> dict:
    """``interests``/``user_input``으로 전국 적용 법률 참고 링크를 찾는다."""

    slots = state.get("slots", {})
    interests = slots.get("interests", [])
    # 쿼리는 embedding provider와 vector DB 검색 로그로 나가므로 원문이 아니라
    # 마스킹된 텍스트를 쓴다. 슬롯 추출(N1)과 같은 규칙을 공유한다.
    user_input = redact_sensitive_text(state.get("user_input", ""))

    query = " ".join([*interests, user_input]).split()
    query = " ".join(query)
    if not query:
        return {"general_law_references": []}

    return {"general_law_references": search_general_law_citations(query)}
