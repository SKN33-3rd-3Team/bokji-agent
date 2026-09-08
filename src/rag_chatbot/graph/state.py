"""답변 그래프 State 계약 (N1~N14 범위).

이 파일은 N1~N14 노드가 주고받는 필드를 정의한다. N1~N3(슬롯 파싱/적합성
체크/추가 정보 요청) 필드는 Issue #21에서, N13~N14(답변 생성/최종 검증)
필드는 Issue #25(graph builder 조립)에서 이어서 추가했다.

공용 파일이므로 변경 시 담당자 1인이 제안 -> 리뷰 -> 반영 순서로만 수정한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, TypedDict

from rag_design.contracts import Chunk, Citation, EvidenceStatus, RetrievedChunk
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
    # 나이는 사용자가 말한 숫자를 그대로 쓰지 않는다. N1(slot_parser)이
    # birth_date에서 파생해 채우는 값만 판정에 쓴다(Issue #21 리뷰 피드백:
    # 복지제도 자격기준은 대부분 만 나이라, 자기신고 숫자를 그대로 쓰면
    # 한국식 세는 나이와 헷갈려 경계에서 오판정이 난다).
    birth_date: str | None
    age: int | None  # 만 나이(파생값)
    age_year_based: int | None  # 연 나이(파생값). 일부 청년 정책은 출생연도 기준.
    age_ref_date: str | None  # 위 두 파생값을 계산한 기준일(ISO 문자열)
    age_self_reported: int | None  # 사용자가 말한 숫자("30세" 등). 판정에는 쓰지 않음.
    age_subject: str | None  # 연령 조건이 가리키는 대상(본인/자녀/가구원/unknown)
    # 하드 게이트 슬롯(slot_schema.PROFILE_HARD_GATE_SLOTS): 값이 없으면 N3가
    # 사용자에게 되묻는다.
    gender: str | None
    income_bracket: str | None
    disability_status: str | None
    employment_status: str | None
    # 소프트 슬롯(slot_schema.SOFT_SLOTS): 있으면 쓰고 없으면 그냥 넘어간다.
    marital_status: str | None
    household_types: list[str]
    pregnancy_status: str | None
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
    status: EvidenceStatus  # rag_design.contracts.EvidenceStatus 값
    reasons: list[str]
    search_query: str
    # law_check_required=True인 claim에만 채워짐 (N7 리뷰 피드백, Issue #16).
    # required_aspects: 정확히 뭘 법령으로 확인해야 하는지 (예: "나이 자격요건").
    # required_law_sources: 확인해야 할 법령 문서 {law_type, source_id} 목록.
    # 정책 원문의 "근거법령" 섹션에서 언급된 법령명을 active LAW vector
    # collection의 canonical law_name과 exact 매칭해서 채운다. 매칭 안 되는
    # 이름은 그냥 빠지고, 한 이름이 여러 identity면 fail-closed 한다.
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
    # N9가 실제로 대조한 조건과, 대조하지 못한 조건(한국어 이름).
    # "충족"이 "모든 자격 조건 만족"으로 읽히지 않게 하려고 함께 싣는다 -
    # 지금 문서 metadata에는 연령 기준밖에 없어서, 장애·성별·소득·취업은
    # 슬롯이 있어도 비교 자체를 못 한다(eligibility_verdict.py 참고).
    checked: list[str]
    unchecked: list[str]


class BenefitAmount(TypedDict, total=False):
    policy_id: str
    amount: float | None
    rule_chunk_id: str
    calculation_note: str
    # 금액의 성격. amount만 화면에 띄우면 "200,000원"이 월인지 연인지 1회인지,
    # 확정인지 상한인지 알 수 없어서 사용자가 그만큼 받는다고 읽는다.
    # period: "month" | "year" | "once" | None
    period: str | None
    # 원문이 "최대"/"한도"/"이내"로 적은 금액인지. 원천 데이터의 42.5%가 이렇다.
    is_maximum: bool
    # per_unit: "person"(1인당) | "household"(가구당) | None
    per_unit: str | None
    # 원문에 근거가 있을 때만 계산한 총액(월 단가 x 개월수, 1인당 x 가구원수).
    # 근거가 없으면 None - 기간을 모르는데 12를 곱하지 않는다.
    total_amount: float | None


class DuplicateVerdict(TypedDict, total=False):
    policy_id: str
    status: str
    # 같은 답변에 함께 나온 정책 중 이 정책과 중복수급이 안 되는 것들의 id.
    conflicts_with: list[str]
    condition_note: str
    # 조항의 성격. 원천 문서의 "중복" 표현은 세 가지가 섞여 있어서
    # (실측 247개 조항: other 66% / household 26% / header 7%),
    # 하나로 뭉뚱그리면 "1가구 1회"를 "다른 제도와 중복 불가"로 오해한다.
    #   other     : 다른 제도와의 중복 제한 (중복수급 판정 대상)
    #   household : 같은 제도 재신청·가구/세대 단위 제한 (신청 횟수 안내)
    #   header    : "※ 중복수혜불가 조건"처럼 조건 내용이 비어 있는 제목
    clause_kind: str | None
    # 사용자에게 그대로 보여줄 근거 원문(other/household 각각).
    restriction_clauses: list[str]
    household_clauses: list[str]


class CitationEntry(TypedDict, total=False):
    """N13/N14가 사용자에게 노출하는 인용 한 건.

    LLM 출력에서 직접 만들지 않는다 - state["claim_plan"]의 evidence_chunk_ids
    (N7이 이미 검증한 근거)와 subsidy_chunks/law_chunks의
    chunk.metadata["source_url"]만으로 조립한다 (answer_generation.py 참고).
    """

    policy_id: str
    chunk_id: str
    source_url: str | None
    label: str


AnswerStatus = Literal["complete", "partial", "abstained"]
"""N14 최종 검증 결과.

complete: 모든 인용이 검증됐고 assembled_result에 "정보 부족" 표시가 없음.
partial: 답변은 나가지만 일부 인용이 걸러졌거나 일부 정책이 정보 부족임 -
    사용자에게 부분 응답임을 알려야 한다.
abstained: 검증된 근거가 아예 없어 답변 자체를 노출하지 않음 (확인 불가).
"""


class GraphState(TypedDict, total=False):
    query_id: str
    # N4가 반환할 정책 후보 수. 프론트엔드의 "정책 후보 수" 설정을 첫 요청에
    # 받아 체크포인터에 보존한다. 값 검증은 run_graph/search_policies가 맡는다.
    policy_top_k: int
    user_input: str
    # 이번 턴 발화(user_input)는 되묻기에 답할 때마다 덮어써진다
    # ("서울, 2000-03-26, 여성..."). 그래서 N4 검색에 쓸 **원래 질문**을
    # 따로 보존한다 - N1이 첫 턴에 한 번만 채우고 이후 턴에는 건드리지 않는다.
    initial_user_input: str
    # 이번 그래프 실행 전체의 공통 기준일. N4(검색 필터)와 N7(시행일 검증)이
    # 각자 date.today()를 따로 계산하면 자정 경계 등에서 어긋날 수 있어서,
    # 그래프 시작 시점에 한 번 정해서 모든 노드가 이 값을 공유한다.
    as_of: date
    slots: SlotState
    missing_slots: list[str]
    # N2가 되묻기 상한(slot_schema.MAX_SLOT_ASKS)을 판단하는 데 쓰는 슬롯별
    # 재질문 횟수. N3(request_missing_slots)가 슬롯을 물을 때마다 올린다.
    slot_ask_counts: dict[str, int]
    # 지역을 끝내 받지 못해 N2가 전국 범위로 좁혀 진행했을 때 True.
    region_fallback_applied: bool
    general_law_references: list[Citation]
    needs_input: bool
    followup_question: str | None
    subsidy_chunks: list[RetrievedChunk]
    # N4 semantic 후보의 rank/score/provenance를 건드리지 않고 N5에 넘기는
    # canonical exact legal_basis parts. source_id/ordinal/chunk_part 순서로 보존한다.
    subsidy_legal_basis_chunks: list[Chunk]
    # N4가 고른 정책들의 **전체 섹션**. subsidy_chunks가 정책마다 대표 청크
    # 하나만 담는 것과 달리, 이쪽은 그 정책 문서의 모든 청크를 담는다.
    #
    # 왜 나눠 두는가: N5(claim_plan)는 청크 하나당 LLM을 한 번씩 부르므로
    # subsidy_chunks가 커지면 응답 시간이 그대로 배수로 늘어난다. 반면
    # N9~N11은 판정을 위해 문서 전체를 봐야 한다(중복수급 제한 조항이
    # 지원대상/지원내용/선정기준 어디에 있을지 모른다 - 실측 분포가 103/102/22).
    # 그래서 LLM 입력(subsidy_chunks)과 판정 입력(subsidy_full_chunks)을
    # 분리한다. 이 필드를 쓰면 N9~N11이 각자 하던 재검색도 없앨 수 있다.
    subsidy_full_chunks: list[RetrievedChunk]
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
    # --- N13 (답변 생성) ---
    draft_answer: str
    citations: list[CitationEntry]
    # --- N14 (최종 Claim-Citation 검증) ---
    final_answer: str
    final_citations: list[CitationEntry]
    answer_status: AnswerStatus
