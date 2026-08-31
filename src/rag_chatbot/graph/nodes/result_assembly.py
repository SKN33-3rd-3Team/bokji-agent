"""N12 결과 조립 노드.

xlsx 설계표 기준 초안은 N12를 "Node (결정론)" - N9/N10/N11과 달리 vectorDB를
검색하지 않는 노드 - 로 봤었다. 그런데 Issue #11 스펙을 다시 확인해보니
예시가 명확하다: "지원금 계산 불가한 경우 관련 법령 검색하여 본문 링크
추가". 즉 N12도 딱 한 가지 경우에는 검색을 한다 - N10이 금액을 계산 못 한
정책에 대해, 관련 법령을 찾아 답변에 링크를 보충해주는 경우.

이전 노드들의 출력을 정책별 구조로 모아 N13(답변 생성)에 넘길
assembled_result를 만든다.

- 정책 간 금액 총합 등 근거 없는 합산을 만들지 않는다 - 정책별로 분리된 구조를
  유지한다.
- eligibility_verdicts, benefit_amounts, duplicate_verdicts 중 하나라도 없는
  정책은 방어적으로 필터링하지 않고 "정보 부족"으로 표시한다 (누락을 숨기지
  않는다 - compliance 원칙).
- 어떤 노드를 거쳐왔는지 state["node_trace"]에 "N12"를 추가해 기록한다.

법령 검색 로직 (지원금 계산 불가 시):
1. 그 정책(SUBSIDY) 문서의 근거법령(section_type="legal_basis") chunk를
   doc_id로 좁혀 재검색한다. 본문이 "유아교육법(제24조)||영유아보육법(제34조)"
   처럼 "||"로 여러 법령을 이어붙인 텍스트라, 각 항목에서 조항 표기
   "(제24조)" 같은 괄호 부분을 떼어내 법령명만 뽑아낸다.
   TODO(팀 확인 필요): 이 파싱은 현재 원문 표기 방식에 맞춘 휴리스틱이라
   표기 형식이 바뀌면 깨질 수 있다. 원천 데이터에 법령명이 구조화된 필드로
   따로 있다면 그걸 쓰는 게 더 안전하다.
2. 뽑아낸 법령명 각각으로 LAW 컬렉션을 law_name 정확히 일치하는 chunk로
   재검색한다(임베딩 유사도만으로는 부정확해서, 이미 알고 있는 법령명으로
   정확히 필터링). 찾은 법령마다 이름/원문 링크(source_url)를 related_law로
   모은다.
3. 근거법령 자체가 없거나(재검색 결과 없음), 그 이름으로 LAW 컬렉션에서
   못 찾으면(아직 수집 안 된 법령) related_law는 빈 리스트로 두고 그 사실을
   숨기지 않는다.
"""

from __future__ import annotations

import re

from rag_design.contracts import SourceType
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
)

from ..state import GraphState

_LEGAL_BASIS_SEPARATOR = "||"
_LAW_NAME_PATTERN = re.compile(r"\s*\([^)]*\)\s*")


def _extract_law_names(legal_basis_text: str) -> list[str]:
    """근거법령 chunk 본문에서 조항 표기를 뗀 법령명 목록을 뽑아낸다.

    예: "유아교육법(제24조)||영유아보육법(제34조)" -> ["유아교육법", "영유아보육법"]
    """
    # 본문 첫 줄들(제목/지역/"근거법령" 헤딩)은 건너뛰고, "||"가 포함된
    # 마지막 줄(실제 법령 목록)만 사용한다.
    candidate_line = ""
    for line in legal_basis_text.splitlines():
        if _LEGAL_BASIS_SEPARATOR in line or line.strip():
            candidate_line = line
    names = []
    for raw in candidate_line.split(_LEGAL_BASIS_SEPARATOR):
        name = _LAW_NAME_PATTERN.sub("", raw).strip()
        if name:
            names.append(name)
    return names


def _find_related_law(policy_id: str, store: ChromaVectorStore, query_id: str) -> list[dict]:
    """지원금 계산이 안 된 정책의 근거법령을 찾아 (법령명, 원문 링크) 목록을 만든다."""
    try:
        legal_basis_chunks = store.search(
            SourceType.SUBSIDY,
            f"{policy_id} 근거법령",
            query_id=f"{query_id}-legal-basis",
            top_k=1,
            search_filter=VectorSearchFilter(
                metadata_equals={"doc_id": policy_id, "section_type": "legal_basis"}
            ),
        )
    except CollectionNotFoundError:
        return []
    if not legal_basis_chunks:
        return []

    law_names = _extract_law_names(legal_basis_chunks[0].chunk.text)
    related_law: list[dict] = []
    for law_name in law_names:
        try:
            law_chunks = store.search(
                SourceType.LAW,
                law_name,
                query_id=f"{query_id}-law-{law_name}",
                top_k=1,
                search_filter=VectorSearchFilter(metadata_equals={"law_name": law_name}),
            )
        except CollectionNotFoundError:
            continue
        if not law_chunks:
            continue
        chunk = law_chunks[0].chunk
        related_law.append(
            {
                "law_name": law_name,
                "source_url": chunk.metadata.get("source_url"),
                "source_name": chunk.metadata.get("source_name"),
            }
        )
    return related_law


def assemble_result(state: GraphState, store: ChromaVectorStore) -> dict:
    """state["eligibility_verdicts"], state["benefit_amounts"],
    state["duplicate_verdicts"]를 정책 단위로 묶어 state["assembled_result"],
    state["node_trace"]를 채워 반환한다 (partial state update).

    store: 지원금 계산 불가 정책의 관련 법령을 찾을 때만 쓰는
    ChromaVectorStore. LangGraph 그래프 조립 시
    ``functools.partial(assemble_result, store=store)``로 주입한다.
    """
    query_id = state.get("query_id", "n12")
    eligibility_by_policy = {
        verdict["policy_id"]: verdict
        for verdict in state.get("eligibility_verdicts", [])
    }
    amount_by_policy = {
        entry["policy_id"]: entry for entry in state.get("benefit_amounts", [])
    }
    duplicate_by_policy = {
        entry["policy_id"]: entry for entry in state.get("duplicate_verdicts", [])
    }

    policies: dict[str, dict] = {}
    for policy_id, eligibility in eligibility_by_policy.items():
        entry: dict = {"eligibility": eligibility}

        if eligibility.get("verdict") == "충족":
            amount = amount_by_policy.get(policy_id)
            calculation_failed = amount is None or amount.get("amount") is None
            if amount is None:
                entry["benefit_amount"] = None
                entry["status_note"] = "정보 부족: 지원금 계산 결과 없음"
            else:
                entry["benefit_amount"] = amount

            if calculation_failed:
                entry["related_law"] = _find_related_law(policy_id, store, query_id)

        duplicate = duplicate_by_policy.get(policy_id)
        if duplicate is None:
            entry["duplicate"] = None
            entry.setdefault("status_note", "정보 부족: 중복수급 판정 결과 없음")
        else:
            entry["duplicate"] = duplicate

        policies[policy_id] = entry

    assembled_result = {"policies": policies}

    node_trace = list(state.get("node_trace", []))
    node_trace.append("N12")

    return {"assembled_result": assembled_result, "node_trace": node_trace}
