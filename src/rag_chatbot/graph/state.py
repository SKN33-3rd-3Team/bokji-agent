"""답변 그래프 State 계약 (N9~N12 범위, Issue #11).

이 파일은 N9~N12 노드가 주고받는 필드만 정의한다. N1~N8, N13~N14가
쓰는 필드(slots 세부, claim_plan의 생성 단계, subsidy_chunks/law_chunks 등)는
해당 노드를 만드는 Issue에서 이 파일에 이어서 추가한다.

공용 파일이므로 변경 시 담당자 1인이 제안 -> 리뷰 -> 반영 순서로만 수정한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from rag_design.contracts import RetrievedChunk


class SlotState(TypedDict, total=False):
    age: int | None
    region_scope: str | None
    region_names: list[str]
    interests: list[str]
    household_size: int | None
    children_count: int | None


class ClaimDraft(TypedDict, total=False):
    claim_id: str
    policy_id: str
    claim_type: str
    doc_check_required: bool
    law_check_required: bool
    evidence_chunk_ids: list[str]
    status: str
    reasons: list[str]


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
    # 이번 그래프 실행 전체의 공통 기준일. N4(검색 필터)와 N7(시행일 검증)이
    # 각자 date.today()를 따로 계산하면 자정 경계 등에서 어긋날 수 있어서,
    # 그래프 시작 시점에 한 번 정해서 모든 노드가 이 값을 공유한다.
    as_of: date
    slots: SlotState
    subsidy_chunks: list[RetrievedChunk]
    law_chunks: list[RetrievedChunk]
    claim_plan: list[ClaimDraft]
    eligibility_verdicts: list[EligibilityVerdict]
    benefit_amounts: list[BenefitAmount]
    duplicate_verdicts: list[DuplicateVerdict]
    assembled_result: dict[str, Any]
    node_trace: list[str]
