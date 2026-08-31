
"""답변 그래프 State 계약 (N1~N12 범위).

이 파일은 N1~N12 노드가 주고받는 필드만 정의한다. N1~N3, N13~N14가
쓰는 필드는
해당 노드를 만드는 Issue에서 이 파일에 이어서 추가한다.

공용 파일이므로 변경 시 담당자 1인이 제안 -> 리뷰 -> 반영 순서로만 수정한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, TypedDict

from rag_design.contracts import Citation, RetrievedChunk
from rag_design.contracts import RetrievedChunk
from rag_design.policy import AbstentionDecision


EvidenceGateVerdict = Literal[
    "pass",
    "insufficient_document",
    "insufficient_law",
    "fail",
]

# N7/N8 evidence binding에서 ClaimDraft.policy_id는 안정적인 subsidy 원천 ID인
# Chunk.metadata["source_id"]다. N7은 이 원천 ID와 Chunk.doc_id의 canonical
# 일관성도 검증한다.


class SlotState(TypedDict, total=False):
    age: int | None
    region_scope: str | None
    region_names: list[str]
    interests: list[str]
    household_size: int | None
    children_count: int | None


class RequiredLawSource(TypedDict):
    law_type: Literal["law", "admrul", "ordin"]
    source_id: str


class ClaimDraft(TypedDict, total=False):
    claim_id: str
    policy_id: str
    claim_type: str
    doc_check_required: bool
    law_check_required: bool
    evidence_chunk_ids: list[str]
    status: str
    reasons: list[str]
    search_query: str
    # law_check_required=True인 claim에만 채워짐 (N7 리뷰 피드백, Issue #16).
    # required_aspects: 정확히 뭘 법령으로 확인해야 하는지 (예: "나이 자격요건").
    # required_law_sources: 확인해야 할 법령 문서 {law_type, source_id} 목록.
    # 정책 원문의 "근거법령" 섹션에서 언급된 법령명을, 유나님이 수집한 법령
    # 데이터(law_documents.jsonl)와 이름으로 매칭해서 채운다. 매칭 안 되는
    # 이름은 그냥 빠진다 (N8이 더 정밀하게 찾을 수도 있음).
    required_aspects: list[str]
    required_law_sources: list[RequiredLawSource]
    # N7이 "근거 부족"으로 이 claim을 N6에 다시 보낼 때 1씩 늘어남
    # (N7 리뷰 피드백 #5, 그림 E11: N7 -> N6 근거 부족 재확인). N6은 이 값이
    # 0보다 크면 "재시도"로 판단해서, N4가 처음 가져온 top-K 범위 밖까지
    # 더 넓게 재검색한다. 0이면(첫 시도) 기존 subsidy_chunks 범위 내에서만
    # 검증한다.
    doc_retry_count: int


class EligibilityVerdict(TypedDict, total=False):
    policy_id: str
    verdict: str
    reasons: list[str]


class BenefitAmount(TypedDict, total=False):
    policy_id: str
    amount: float | None
    rule_chunk_id: str
    calculation_note: str


class DuplicateVerdict(TypedDict, total=False):
    policy_id: str
    status: str
    conflicts_with: list[str]
    condition_note: str


class GraphState(TypedDict, total=False):
    query_id: str
    user_input: str
    # 이번 그래프 실행 전체의 공통 기준일. N4(검색 필터)와 N7(시행일 검증)이
    # 각자 date.today()를 따로 계산하면 자정 경계 등에서 어긋날 수 있어서,
    # 그래프 시작 시점에 한 번 정해서 모든 노드가 이 값을 공유한다.
    as_of: date
    slots: SlotState
    missing_slots: list[str]
    general_law_references: list[Citation]
    needs_input: bool
    followup_question: str | None
    subsidy_chunks: list[RetrievedChunk]
    law_chunks: list[RetrievedChunk]
    claim_plan: list[ClaimDraft]
    eligibility_verdicts: list[EligibilityVerdict]
    benefit_amounts: list[BenefitAmount]
    duplicate_verdicts: list[DuplicateVerdict]
    assembled_result: dict[str, Any]
    node_trace: list[str]
    safety_blocked: bool
    evidence_gate_verdict: EvidenceGateVerdict
    abstention_decision: AbstentionDecision
    missing_document_claim_ids: list[str]
    missing_law_claim_ids: list[str]
    doc_retry_count: int
    law_retry_count: int
