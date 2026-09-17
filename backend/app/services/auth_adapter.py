"""API-01~08의 ``src.rag_chatbot.auth.service`` 호출 어댑터.

각 함수는 auth.service를 그대로 호출하고 예외만 ``ApiError``로 변환한다.
같은 예외 클래스라도 API마다 다른 코드로 매핑해야 하는 경우가 있어(예:
``InvalidCredentialsError``가 로그인=INVALID_CREDENTIALS, 비밀번호 변경=
INVALID_CURRENT_PASSWORD - API_정의서.xlsx API-02/API-06 비교) 함수를
라우트별로 나눴다. 새 비즈니스 로직은 여기서 만들지 않는다.
"""

from __future__ import annotations

from fastapi import status

from src.rag_chatbot.auth import service as auth_service
from src.rag_chatbot.auth.service import (
    AccountLockedError,
    AuthBackendUnavailableError,
    AuthUser,
    InvalidCredentialsError,
    PasswordPolicyError,
    UserNotFoundError,
    UsernameTakenError,
)

from ..core.errors import ApiError
from ..schemas.auth import ChatDefaultsResponse, SignupRequest, UpdateProfileRequest, UserProfile

# auth/service.py:684-685 - change_password()가 "새 비밀번호가 현재와 같음"을
# 별도 예외 없이 이 정확한 문구를 담은 PasswordPolicyError로 던진다. API-06은
# 이 경우만 SAME_AS_CURRENT로 구분해야 하므로 문자열로 판별한다.
_SAME_AS_CURRENT_VIOLATION = "새 비밀번호는 현재 비밀번호와 달라야 합니다."


def _to_user_profile(user: AuthUser) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.username,
        display_name=user.display_name,
        created_at=user.created_at,
        region=user.region,
        gender=user.gender,
        birth_date=user.birth_date,
        disability_status=user.disability_status,
        veteran_status=user.veteran_status,
        income_bracket=user.income_bracket,
        household_types=list(user.household_types),
        interests=list(user.interests),
        marketing_opt_in=user.marketing_opt_in,
    )


def signup(payload: SignupRequest) -> UserProfile:
    if not payload.terms_agreed or not payload.privacy_agreed:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "VALIDATION_ERROR",
            "서비스 이용약관과 개인정보 수집·이용에 모두 동의해야 합니다.",
        )
    if payload.password != payload.password_confirm:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "비밀번호가 일치하지 않습니다."
        )
    if not auth_service._clean_display_name(payload.name):
        # SignupRequest.name 은 pydantic에서 필수지만 공백/제어문자만 있는
        # 문자열은 통과한다 - API가 직접 호출될 수 있으므로 실제 저장 시
        # 쓰는 것과 같은 정리 기준(auth.service._clean_display_name)으로
        # 여기서 막는다. auth.service.sign_up()은 다른 내부 호출부(테스트
        # 등)를 위해 빈 이름을 그대로 허용하므로(마이페이지 "이름 지움" 같은
        # 의미가 아님) 서비스가 아니라 API 어댑터가 필수값을 검사한다.
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "이름을 입력해 주세요."
        )
    try:
        user = auth_service.sign_up(
            payload.email,
            payload.password,
            payload.name,
            region=payload.region,
            gender=payload.gender,
            birth_date=payload.birth_date,
            interests=payload.interests,
            disability_status=payload.disability_status,
            veteran_status=payload.veteran_status,
            income_bracket=payload.income_bracket,
            household_types=payload.household_types,
            marketing_opt_in=payload.marketing_opt_in,
        )
    except UsernameTakenError as exc:
        raise ApiError(status.HTTP_409_CONFLICT, "USERNAME_TAKEN", str(exc)) from exc
    except PasswordPolicyError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "PASSWORD_POLICY_VIOLATION",
            str(exc),
            violations=exc.violations,
        ) from exc
    except AuthBackendUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AUTH_BACKEND_UNAVAILABLE", str(exc)
        ) from exc
    return _to_user_profile(user)


def login(email: str, password: str) -> UserProfile:
    try:
        user = auth_service.authenticate(email, password)
    except AccountLockedError as exc:
        raise ApiError(
            status.HTTP_423_LOCKED,
            "ACCOUNT_LOCKED",
            str(exc),
            remaining_seconds=exc.retry_after_seconds,
        ) from exc
    except InvalidCredentialsError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", str(exc)) from exc
    except AuthBackendUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AUTH_BACKEND_UNAVAILABLE", str(exc)
        ) from exc
    return _to_user_profile(user)


def get_profile(username: str, *, user_id: int) -> UserProfile:
    try:
        user = auth_service.get_profile(username, expected_user_id=user_id)
    except UserNotFoundError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.") from exc
    except AuthBackendUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AUTH_BACKEND_UNAVAILABLE", str(exc)
        ) from exc
    return _to_user_profile(user)


# UpdateProfileRequest의 list 타입 필드 - null이 오면 무엇으로 "지움"을
# 표현해야 하는지 구분하는 데 쓴다(_normalize_clear_semantics 참고).
_LIST_FIELDS = frozenset({"interests", "household_types"})


def _normalize_clear_semantics(fields: dict) -> dict:
    """API-05 PATCH 의미론: "필드를 안 보냄" = 미수정, "null 또는 빈 값을
    보냄" = 지움(API_정의서.xlsx API-05: "필드를 아예 보내지 않으면
    '수정하지 않음', null/빈 값을 보내면 '값을 지움'").

    ``payload.model_dump(exclude_unset=True)``는 "안 보냄"과 "보냄"만
    구분하고, "보냄"으로 판정된 값이 ``None``(JSON null)이든 빈
    문자열/배열이든 그대로 통과시킨다. 하지만 아래 ``auth_service.
    update_profile()``은 ``None``을 "수정하지 않음"으로 해석하므로
    (``src/rag_chatbot/auth/service.py`` docstring: "``None``인 인자는
    수정하지 않음") - 이 둘 사이의 의미 차이를 여기서 메운다: 클라이언트가
    보낸 ``None``을 실제 "지움" 신호(문자열 필드는 ``""``, 리스트 필드는
    ``[]``)로 변환한 뒤에만 넘긴다.
    """

    normalized = dict(fields)
    for key, value in fields.items():
        if value is None:
            normalized[key] = [] if key in _LIST_FIELDS else ""
    return normalized


def update_profile(username: str, payload: UpdateProfileRequest, *, user_id: int) -> UserProfile:
    # exclude_unset=True: "필드를 안 보냄"(미수정)과 "보냄"(수정 대상)을
    # 구분한다. "보냄"으로 판정된 값 중 None(JSON null)은 _normalize_clear_
    # semantics가 "지움" 신호로 다시 변환한다 - update_profile()의 PATCH
    # 의미론과 동일하게 맞추기 위함(위 함수 docstring 참고).
    fields = _normalize_clear_semantics(payload.model_dump(exclude_unset=True))
    try:
        user = auth_service.update_profile(username, expected_user_id=user_id, **fields)
    except UserNotFoundError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.") from exc
    except AuthBackendUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AUTH_BACKEND_UNAVAILABLE", str(exc)
        ) from exc
    return _to_user_profile(user)


def change_password(
    username: str, current_password: str, new_password: str, *, user_id: int
) -> None:
    try:
        auth_service.change_password(
            username, current_password, new_password, expected_user_id=user_id
        )
    except PasswordPolicyError as exc:
        if exc.violations == [_SAME_AS_CURRENT_VIOLATION]:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "SAME_AS_CURRENT", str(exc)) from exc
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "PASSWORD_POLICY_VIOLATION",
            str(exc),
            violations=exc.violations,
        ) from exc
    except InvalidCredentialsError as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "INVALID_CURRENT_PASSWORD", str(exc)
        ) from exc
    except UserNotFoundError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.") from exc
    except AuthBackendUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AUTH_BACKEND_UNAVAILABLE", str(exc)
        ) from exc


def delete_account(username: str, password: str, *, user_id: int) -> None:
    try:
        auth_service.delete_account(username, password, expected_user_id=user_id)
    except InvalidCredentialsError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", str(exc)) from exc
    except UserNotFoundError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.") from exc
    except AuthBackendUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AUTH_BACKEND_UNAVAILABLE", str(exc)
        ) from exc


def _build_chat_defaults(user: AuthUser) -> ChatDefaultsResponse:
    """API-08 - 마이페이지 값을 ``service.ask()``의 known_* 계약 형태로 가공."""

    return ChatDefaultsResponse(
        known_region=user.region or None,
        known_gender=user.gender or None,
        known_birth_date=user.birth_date or None,
        known_disability_status=user.disability_status or None,
        known_income_bracket=user.income_bracket or None,
        known_household_types=list(user.household_types),
        known_veteran_status=user.veteran_status or None,
        extra_interests=list(user.interests),
        # 마이페이지 테이블에 취업 상태 컬럼이 없어 항상 False 고정
        # (API_정의서.xlsx API-08 응답 필드 설명 그대로).
        employment_status_available=False,
    )


def get_chat_defaults(username: str, *, user_id: int) -> ChatDefaultsResponse:
    try:
        user = auth_service.get_profile(username, expected_user_id=user_id)
    except UserNotFoundError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.") from exc
    except AuthBackendUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AUTH_BACKEND_UNAVAILABLE", str(exc)
        ) from exc
    return _build_chat_defaults(user)
