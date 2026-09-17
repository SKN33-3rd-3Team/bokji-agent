"""공통 요청 타입과 에러 응답 스키마.

API_정의서.xlsx는 각 API마다 "HTTP 상태/에러 코드/메시지" 표만 정의하고
에러 응답 바디의 정확한 JSON 모양은 명시하지 않는다 - 프로젝트 관례상
합리적인 기본형을 다음과 같이 정한다: ``code``/``message``는 항상 있고,
violations(비밀번호 정책 위반 목록)나 remaining_seconds(계정 잠금 잔여
시간) 같은 에러별 추가 필드는 최상위에 덧붙인다.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

# HTTP 날짜 표기만 제한한다. 달력·미래·나이 검증은 각 기존 경로가 맡는다.
BirthDateString = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class ErrorResponse(BaseModel):
    code: str
    message: str
    violations: list[str] | None = None
    remaining_seconds: int | None = None
