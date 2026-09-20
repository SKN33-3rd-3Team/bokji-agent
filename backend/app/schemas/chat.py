"""API-10~13 요청/응답 스키마.

``PolicyDetail``/``PolicyView``/``ChatResponse``의 필드명·타입은
``src/rag_chatbot/service.py``의 동명 TypedDict(라인 412~507)와 100% 동일하게
맞춘다(모듈 docstring이 "필드를 지우거나 이름을 바꾸지 않는다"를 명시한
계약). ``PolicyDetail``의 런타임 dict는 ``rag_design.contracts.
SUBSIDY_DETAIL_SECTIONS`` 기반으로 정적 TypedDict보다 키가 더 많을 수 있어
(``required_documents*`` 포함) ``extra="allow"``로 열어 둔다 - 여기 없는
키도 그대로 통과시켜 프론트에 전달한다.

``required_documents*_items``(S07-06/S10-01, 2026-09-16 옵션 ② 확정)는
``service.py``가 주는 원문 문자열이 아니라 백엔드가 파생시켜 추가한 필드다
(``app/core/document_parsing.py``, ``services/chat_adapter.py::
_augment_required_documents`` 참고) - 원본 문자열 필드는 그대로 유지된다.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from src.rag_chatbot.graph.slot_schema import parse_birth_date

from ..core.options import (
    DISABILITY_LABELS_KO,
    GENDER_LABELS_KO,
    HOUSEHOLD_TYPE_LABELS_KO,
    INCOME_BRACKET_LABELS_KO,
    SIDO_OPTIONS,
    VETERAN_LABELS_KO,
)
from .common import BirthDateString


def _require_non_blank(value: str) -> str:
    """``Field(min_length=1)``은 공백뿐인 문자열("   ")도 통과시킨다 - API_정의서.xlsx
    API-10/11/12가 "빈 메시지/빈 질문"을 400 VALIDATION_ERROR로 요구하는데,
    공백만 보내면 이 체크를 우회해 그대로 그래프까지 넘어간다. 여기서
    strip() 후 한 번 더 막는다.
    """

    if not value.strip():
        raise ValueError("빈 값은 허용되지 않습니다.")
    return value


_NonBlankStr = Annotated[str, Field(min_length=1), AfterValidator(_require_non_blank)]


def _validate_public_choice(choices: Collection[str], message: str):
    """API-09 공개 선택지만 허용한다. 그래프 내부 unknown은 공개 코드가 아니다."""

    def _validator(value: str | None) -> str | None:
        if value is not None and value not in choices:
            raise ValueError(message)
        return value

    return _validator


def _validate_known_birth_date(value: str | None) -> str | None:
    if value is not None and parse_birth_date(value) is None:
        raise ValueError("생년월일 형식이 올바르지 않거나 허용 범위를 벗어났습니다.")
    return value


_KnownRegion = Annotated[
    str | None, AfterValidator(_validate_public_choice(SIDO_OPTIONS, "거주 지역 값이 올바르지 않습니다."))
]
_KnownGender = Annotated[
    str | None, AfterValidator(_validate_public_choice(GENDER_LABELS_KO, "성별 값이 올바르지 않습니다."))
]
_KnownDisabilityStatus = Annotated[
    str | None,
    AfterValidator(_validate_public_choice(DISABILITY_LABELS_KO, "장애 등록 여부 값이 올바르지 않습니다.")),
]
_KnownVeteranStatus = Annotated[
    str | None,
    AfterValidator(_validate_public_choice(VETERAN_LABELS_KO, "보훈대상자 여부 값이 올바르지 않습니다.")),
]
_KnownIncomeBracket = Annotated[
    str | None,
    AfterValidator(_validate_public_choice(INCOME_BRACKET_LABELS_KO, "소득 구간 값이 올바르지 않습니다.")),
]
_KnownHouseholdType = Annotated[
    str, AfterValidator(_validate_public_choice(HOUSEHOLD_TYPE_LABELS_KO, "가구 유형 값이 올바르지 않습니다."))
]
_KnownBirthDate = Annotated[BirthDateString | None, AfterValidator(_validate_known_birth_date)]


class ChatRequest(BaseModel):
    """API-10 요청 바디."""

    message: _NonBlankStr
    top_k: int | None = Field(default=None, ge=1, le=20)
    extra_interests: list[str] = Field(default_factory=list)
    known_region: _KnownRegion = None
    known_gender: _KnownGender = None
    known_birth_date: _KnownBirthDate = None
    known_disability_status: _KnownDisabilityStatus = None
    known_income_bracket: _KnownIncomeBracket = None
    known_household_types: list[_KnownHouseholdType] = Field(default_factory=list)
    known_veteran_status: _KnownVeteranStatus = None


_CalcSlotName = Literal["marital_status", "pregnancy_status", "children_count", "household_size"]


class CalculationAnswers(BaseModel):
    """API-11 계산 되묻기 답변.

    ``unknown_slots``/``unknown_choices``는 "이 항목은 모름/해당 없음"이다.
    그래프 내부 센티넬(``slot_schema.UNKNOWN``) 문자열을 ``slots``/``choices``
    값으로 받지 않고 별도 목록으로 받는 이유는, 공개 API에서는 열거형 계약에
    있는 값만 받는다는 기존 원칙 때문이다(``_validate_public_choice``의
    "그래프 내부 unknown은 공개 코드가 아니다"와 같은 이유). 센티넬 변환은
    ``request_calc_info.merge_structured_calc_answer``가 한다.
    """

    model_config = ConfigDict(extra="forbid")

    interrupt_id: _NonBlankStr
    slots: dict[_CalcSlotName, StrictStr | StrictInt] = Field(default_factory=dict)
    choices: dict[str, StrictStr] = Field(default_factory=dict)
    unknown_slots: list[_CalcSlotName] = Field(default_factory=list)
    unknown_choices: list[StrictStr] = Field(default_factory=list)


class FollowupRequest(BaseModel):
    """API-11 요청 바디."""

    message: _NonBlankStr | None = None
    calc_answers: CalculationAnswers | None = None

    @model_validator(mode="after")
    def require_one_answer(self):
        if (self.message is None) == (self.calc_answers is None):
            raise ValueError("message 또는 calc_answers 중 하나를 입력해주세요.")
        return self


class PolicyQuestionRequest(BaseModel):
    """API-12 요청 바디."""

    question: _NonBlankStr


class PolicyQuestionResponse(BaseModel):
    """API-12 응답.

    ``reason``/``reason_message``/``llm_status``는 2026-09-20 추가된 진단용
    필드다. ``kind="guidance"``(안내로 물러남)일 때 화면 문구는 어느 경우든
    같아서, 이 값들이 없으면 "계속 응답 불가"가 LLM 미연결 때문인지, 호출
    실패인지, 근거 검증 탈락인지 구분할 방법이 없었다
    (``light_followup.REASON_*`` 참고).
    """

    kind: Literal["answer", "guidance"]
    text: str
    evidence_quotes: list[str] = Field(default_factory=list)
    reason: str | None = None
    reason_message: str | None = None
    llm_status: dict[str, Any] = Field(default_factory=dict)


class PolicyDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    purpose: str | None = None
    support_target: str | None = None
    eligibility_criteria: str | None = None
    support_details: str | None = None
    application_method: str | None = None
    application_period: str | None = None
    legal_basis: str | None = None
    region_names: list[str] | None = None
    region_scope: str | None = None
    age_start: int | None = None
    age_end: int | None = None
    organization: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    # S07-06/S10-01(2026-09-16 옵션 ② 확정): 원본 원문 문자열은 그대로 두고,
    # 백엔드가 항목 배열로 구조화한 *_items를 추가로 함께 내려준다
    # (app/core/document_parsing.py - "지어내지 않는다" 원칙에 따라 원문이
    # 이미 표현한 구분만 인식, 애매하면 원문 그대로 1개 항목).
    required_documents: str | None = None
    required_documents_items: list[str] | None = None
    required_documents_official: str | None = None
    required_documents_official_items: list[str] | None = None
    required_documents_self: str | None = None
    required_documents_self_items: list[str] | None = None


class PolicyView(BaseModel):
    model_config = ConfigDict(extra="allow")

    rank: int | None = None
    policy_id: str
    title: str
    badge: str | None = None
    eligibility_status: str | None = None
    eligibility_reasons: list[str] = Field(default_factory=list)
    verification_checked: list[str] = Field(default_factory=list)
    verification_unchecked: list[str] = Field(default_factory=list)
    verification_note: str | None = None
    amount: float | None = None
    amount_label: str | None = None
    amount_period: str | None = None
    amount_is_maximum: bool | None = None
    amount_per_unit: str | None = None
    amount_total: float | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    total_amount_min: float | None = None
    total_amount_max: float | None = None
    duplicate_status: str | None = None
    duplicate_note: str | None = None
    duplicate_clause_kind: str | None = None
    duplicate_conflicts: list[dict] = Field(default_factory=list)
    household_limit_clauses: list[str] = Field(default_factory=list)
    needs_confirmation: list[str] = Field(default_factory=list)
    related_law: list[dict] = Field(default_factory=list)
    detail: PolicyDetail = Field(default_factory=PolicyDetail)


class CalculationSlotInput(BaseModel):
    slot: str
    label: str
    input_type: Literal["select", "number"]
    options: list[dict[str, str]] = Field(default_factory=list)
    minimum: int | None = None
    maximum: int | None = None


class CalculationChoice(BaseModel):
    policy_id: str
    labels: list[str]
    policy_title: str


class ChatProgressResponse(BaseModel):
    """``GET /api/v1/chat/progress/{token}`` - 진행 막대 한 칸의 상태.

    ``src/rag_chatbot/progress.py``의 ``snapshot()`` 반환값을 그대로 받는다
    (``status="unknown"``만 라우터가 직접 만든다 - 기록이 없을 때).
    ``fraction``은 **어림값**이다: 실제 노드 수는 조건부 분기 때문에 끝나봐야
    알 수 있어서, 끝나기 전에는 0.95를 넘지 않는다.
    """

    status: Literal["running", "done", "failed", "unknown"]
    fraction: float = 0.0
    message: str | None = None
    completed_steps: int = 0
    total_steps: int = 0
    elapsed_seconds: float = 0.0


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["needs_input", "answered"]
    session_id: str
    question: str | None = None
    missing_slots: list[str] = Field(default_factory=list)
    interrupt_id: str | None = None
    calc_missing_slots: list[str] = Field(default_factory=list)
    calc_missing_choices: list[CalculationChoice] = Field(default_factory=list)
    calc_slot_inputs: list[CalculationSlotInput] = Field(default_factory=list)
    slot_conflicts: dict[str, dict[str, str]] | None = None
    answer_status: str | None = None
    final_answer: str | None = None
    final_citations: list[dict] = Field(default_factory=list)
    policies: list[PolicyView] = Field(default_factory=list)
    output_json: dict[str, Any] = Field(default_factory=dict)
    output_text: str | None = None
    output_markdown: str | None = None
    llm_status: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)
