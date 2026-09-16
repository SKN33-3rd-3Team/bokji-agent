"""API-01~08 요청/응답 스키마.

이메일 형식/비밀번호 정책/생년월일 범위/enum 값 검증은 여기서 다시
구현하지 않는다 - ``src/rag_chatbot/auth/service.py``가 서버 재검증까지
책임지는 단일 출처이고(``_normalize_username``/``validate_password``/
``parse_birth_date`` 등), 위반 시 던지는 예외를 ``app/core/errors.py``가
받아 정확한 HTTP 코드로 매핑한다(fail-closed, 이중 구현으로 인한 값
드리프트를 피함).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str
    password_confirm: str
    name: str
    region: str = ""
    gender: str = ""
    birth_date: str = ""
    interests: list[str] = Field(default_factory=list)
    disability_status: str = ""
    veteran_status: str = ""
    income_bracket: str = ""
    household_types: list[str] = Field(default_factory=list)
    marketing_opt_in: bool = False
    terms_agreed: bool
    privacy_agreed: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class DeleteAccountRequest(BaseModel):
    password: str


class UpdateProfileRequest(BaseModel):
    """API-05 (PATCH) 요청 바디.

    전부 선택 필드다. "필드를 안 보냄"(미수정) / "null 또는 빈 값을 보냄"
    (지움)의 의미가 다르므로(API_정의서.xlsx API-05: "필드를 아예 보내지
    않으면 '수정하지 않음', null/빈 값을 보내면 '값을 지움'") - 이 스키마
    자체는 기본값을 두지 않고, ``services/auth_adapter.py::
    _normalize_clear_semantics``가 그 의미 변환(안 보냄 vs null vs 빈 값)을
    전담한다.
    """

    display_name: str | None = None
    region: str | None = None
    gender: str | None = None
    birth_date: str | None = None
    interests: list[str] | None = None
    disability_status: str | None = None
    veteran_status: str | None = None
    income_bracket: str | None = None
    household_types: list[str] | None = None


class UserProfile(BaseModel):
    """API-01/02 응답의 ``user`` 객체 및 API-04 응답과 동일한 스키마."""

    id: int
    email: str
    display_name: str
    created_at: str
    region: str = ""
    gender: str = ""
    birth_date: str = ""
    disability_status: str = ""
    veteran_status: str = ""
    income_bracket: str = ""
    household_types: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    marketing_opt_in: bool = False


class SignupResponse(BaseModel):
    user: UserProfile


class LoginResponse(BaseModel):
    user: UserProfile


class MessageResponse(BaseModel):
    message: str


class ChatDefaultsResponse(BaseModel):
    """API-08 응답."""

    known_region: str | None = None
    known_gender: str | None = None
    known_birth_date: str | None = None
    known_disability_status: str | None = None
    known_income_bracket: str | None = None
    known_household_types: list[str] = Field(default_factory=list)
    known_veteran_status: str | None = None
    extra_interests: list[str] = Field(default_factory=list)
    employment_status_available: bool = False
