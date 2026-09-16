"""API-10(상담 시작)/11(되묻기)/13(세션 초기화)의 ``service.ask``/
``answer_followup``/체크포인터 삭제 호출 어댑터.

새 비즈니스 로직을 만들지 않는다 - ``src.rag_chatbot.service``를 그대로
호출하고, 채팅 세션 소유권 확인·응답 캐싱·에러 매핑만 이 계층이 더한다.
"""

from __future__ import annotations

import uuid

from fastapi import status

from rag_design.embeddings import EmbeddingProviderError
from rag_design.vector_store import VectorStoreError
from src.rag_chatbot.service import answer_followup, ask, get_graph

from ..core.document_parsing import split_document_items
from ..core.errors import ApiError
from ..schemas.chat import ChatRequest, ChatResponse
from ..session_store.chat_session import chat_session_store


def _run(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except (VectorStoreError, EmbeddingProviderError):
        # 503 VECTOR_STORE_UNAVAILABLE 매핑은 app/core/errors.py의 전역
        # 핸들러가 담당한다 - 여기서는 그대로 다시 던진다.
        raise
    except SystemExit as exc:
        # service.connect_store()/build_embedding_provider()는 실제
        # data/vector_db나 EMBEDDING_PROVIDER 설정이 없으면 SystemExit을
        # 던진다(CLI 스크립트 전제로 설계된 기존 관례 - service.py 참고).
        # 웹 서버 프로세스를 그대로 종료시키면 안 되므로 503으로 변환한다.
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "VECTOR_STORE_UNAVAILABLE",
            "검색 서비스에 일시적으로 연결할 수 없습니다.",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 그래프 실행 중 예외를 폭넓게 잡아 500으로 통일
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "GRAPH_EXECUTION_ERROR",
            "일시적인 오류가 발생했습니다. 다시 시도해주세요.",
        ) from exc


def _augment_required_documents(raw: dict) -> dict:
    """S07-06/S10-01(2026-09-16 옵션 ② 확정) - policies[].detail에 파싱된
    구비서류 배열(``required_documents*_items``)을 덧붙인다.
    ``service.py``의 ``ChatResponse`` 계약(원문 문자열 필드)은 그대로 두고,
    백엔드 응답에서만 파생 필드로 추가한다(``core/document_parsing.py``).
    """

    for policy in raw.get("policies") or []:
        detail = policy.get("detail")
        if not isinstance(detail, dict):
            continue
        detail["required_documents_items"] = split_document_items(
            detail.get("required_documents")
        )
        detail["required_documents_official_items"] = split_document_items(
            detail.get("required_documents_official")
        )
        detail["required_documents_self_items"] = split_document_items(
            detail.get("required_documents_self")
        )
    return raw


def _cache_last_response(session_id: str, raw: dict) -> None:
    """API-12(정책 문의)가 세션의 마지막 policies/profile을 조회할 수 있게 캐시."""

    if raw.get("status") == "answered":
        chat_session_store.update_last_response(
            session_id,
            policies=list(raw.get("policies") or []),
            profile=list((raw.get("output_json") or {}).get("profile") or []),
        )


def start_chat(payload: ChatRequest, *, user_id: int) -> ChatResponse:
    session_id = str(uuid.uuid4())
    chat_session_store.create(session_id, user_id=user_id)
    raw = _run(
        ask,
        payload.message,
        session_id,
        top_k=payload.top_k if payload.top_k is not None else 5,
        extra_interests=payload.extra_interests or None,
        known_region=payload.known_region,
        known_gender=payload.known_gender,
        known_birth_date=payload.known_birth_date,
        known_disability_status=payload.known_disability_status,
        known_income_bracket=payload.known_income_bracket,
        known_household_types=payload.known_household_types or None,
        known_veteran_status=payload.known_veteran_status,
    )
    raw = _augment_required_documents(raw)
    _cache_last_response(session_id, raw)
    return ChatResponse.model_validate(raw)


def continue_chat(session_id: str, message: str, *, user_id: int) -> ChatResponse:
    if chat_session_store.get(session_id, user_id=user_id) is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "SESSION_NOT_FOUND",
            "세션이 만료되었거나 존재하지 않습니다. 새로 상담을 시작해주세요.",
        )
    raw = _run(answer_followup, session_id, message)
    raw = _augment_required_documents(raw)
    _cache_last_response(session_id, raw)
    return ChatResponse.model_validate(raw)


def delete_chat_session(session_id: str, *, user_id: int) -> None:
    if chat_session_store.get(session_id, user_id=user_id) is None:
        # 소유권이 없거나(다른 사용자 세션) 이미 없는 세션 - 존재 여부를
        # 노출하지 않기 위해 아무것도 지우지 않고 조용히 멱등 성공 처리한다
        # (API_정의서.xlsx API-13: "이미 없는 세션도 200으로 응답해도 무방").
        return
    graph = get_graph()
    if graph.checkpointer is not None:
        graph.checkpointer.delete_thread(session_id)
    chat_session_store.delete(session_id)
