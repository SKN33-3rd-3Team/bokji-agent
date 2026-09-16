"""예외 -> HTTP 응답 매핑.

라우터/어댑터는 이 모듈의 ``ApiError``를 명시적으로 올려서 API 정의서의
정확한 HTTP 상태/에러 코드를 내려보낸다. 같은 예외 클래스라도 어느 API인지에
따라 다른 코드를 매핑해야 하는 경우가 있다 - 예: ``InvalidCredentialsError``가
로그인에서는 ``INVALID_CREDENTIALS``, 비밀번호 변경에서는
``INVALID_CURRENT_PASSWORD``로 다르게 내려가야 한다(API_정의서.xlsx API-02/
API-06 비교). 그래서 auth 예외 -> HTTP 매핑은 전역 한 곳이 아니라
``services/auth_adapter.py``가 라우트별 컨텍스트를 알고 상황별로 결정한다.
전역 핸들러는 ``ApiError`` 자체와, 어댑터가 놓친 경우를 위한 안전망(bare
``AuthError``/벡터스토어 예외/미분류 예외)만 처리한다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rag_design.embeddings import EmbeddingProviderError
from rag_design.vector_store import VectorStoreError
from src.rag_chatbot.auth.service import AuthError

_log = logging.getLogger(__name__)


class ApiError(Exception):
    """라우터/어댑터가 의도적으로 올리는, API 정의서 표와 1:1인 에러."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        violations: list[str] | None = None,
        remaining_seconds: int | None = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.violations = violations
        self.remaining_seconds = remaining_seconds
        super().__init__(message)

    def body(self) -> dict:
        payload: dict = {"code": self.code, "message": self.message}
        if self.violations is not None:
            payload["violations"] = self.violations
        if self.remaining_seconds is not None:
            payload["remaining_seconds"] = self.remaining_seconds
        return payload


def _json_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _json_error(
            status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다."
        )

    @app.exception_handler(AuthError)
    async def _handle_bare_auth_error(request: Request, exc: AuthError) -> JSONResponse:
        # 어댑터가 세분화해서 잡지 못한 나머지(주로 sign_up/update_profile의
        # enum/형식 검증 실패가 bare AuthError로 올라온다) - service.py가 던지는
        # 원문 메시지를 그대로 노출한다(별도 attribute가 없는 케이스라 str(exc)뿐).
        return _json_error(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", str(exc))

    @app.exception_handler(VectorStoreError)
    async def _handle_vector_store_error(request: Request, exc: VectorStoreError) -> JSONResponse:
        _log.warning("vector store unavailable: %s", exc)
        return _json_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "VECTOR_STORE_UNAVAILABLE",
            "검색 서비스에 일시적으로 연결할 수 없습니다.",
        )

    @app.exception_handler(EmbeddingProviderError)
    async def _handle_embedding_error(
        request: Request, exc: EmbeddingProviderError
    ) -> JSONResponse:
        _log.warning("embedding provider unavailable: %s", exc)
        return _json_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "VECTOR_STORE_UNAVAILABLE",
            "검색 서비스에 일시적으로 연결할 수 없습니다.",
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 채팅 라우트의 "그래프 실행 중 예외"(GRAPH_EXECUTION_ERROR, 500)는
        # chat_adapter가 이 지점에 닿기 전에 ApiError로 먼저 변환한다 - 여기는
        # 그 외 전 구간(라우팅/스키마 버그 등)을 위한 최종 안전망이다.
        _log.exception("unhandled error on %s", request.url.path)
        return _json_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "일시적인 오류가 발생했습니다. 다시 시도해주세요.",
        )
