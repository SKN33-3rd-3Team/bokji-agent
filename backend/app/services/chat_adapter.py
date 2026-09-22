"""API-10(상담 시작)/11(되묻기)/13(세션 초기화)의 ``service.ask``/
``answer_followup``/체크포인터 삭제 호출 어댑터.

새 비즈니스 로직을 만들지 않는다 - ``src.rag_chatbot.service``를 그대로
호출하고, 채팅 세션 소유권 확인·응답 캐싱·에러 매핑만 이 계층이 더한다.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import status

from rag_design.embeddings import EmbeddingProviderError
from rag_design.vector_store import VectorStoreError
from src.rag_chatbot.service import answer_followup, ask, get_graph, resumes_forward
from src.rag_chatbot.graph.builder import FailedCheckpointError
from src.rag_chatbot.graph.nodes.request_calc_info import CalculationInputError
from src.rag_chatbot.progress import PROGRESS
from src.rag_chatbot.timing import EXPECTED_NODE_COUNT, EXPECTED_RESUME_NODE_COUNT

from ..core.document_parsing import split_document_items
from ..core.errors import ApiError
from ..schemas.chat import ChatRequest, ChatResponse
from ..schemas.auth import UserProfile
from ..session_store.chat_session import chat_session_store

_log = logging.getLogger(__name__)


def _run(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except CalculationInputError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", str(exc)) from exc
    except FailedCheckpointError as exc:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "GRAPH_EXECUTION_ERROR",
            "이 상담을 계속할 수 없습니다. 새 상담을 시작해 주세요.",
        ) from exc
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


def _cache_last_response(session_id: str, raw: dict, *, user_id: int) -> None:
    """API-12(정책 문의)가 세션의 마지막 policies/profile을 조회할 수 있게 캐시."""

    record = chat_session_store.get(session_id, user_id=user_id)
    if record is not None:
        record.awaiting_input = raw.get("status") == "needs_input"
    if raw.get("status") == "answered":
        chat_session_store.update_last_response(
            session_id,
            policies=list(raw.get("policies") or []),
            profile=list((raw.get("output_json") or {}).get("profile") or []),
        )


def start_chat(payload: ChatRequest, *, user_id: int, progress_token: str | None = None) -> ChatResponse:
    return _start_chat(
        payload.message, user_id=user_id, progress_token=progress_token,
        top_k=payload.top_k if payload.top_k is not None else 5,
        extra_interests=payload.extra_interests or None,
        known_household_types=payload.known_household_types or None,
        **{field: getattr(payload, field) for field in (
            "known_region", "known_gender", "known_birth_date", "known_disability_status",
            "known_income_bracket", "known_veteran_status",
        )},
    )


def start_recommendations(
    profile: UserProfile, *, user_id: int, progress_token: str | None = None
) -> ChatResponse:
    return _start_chat(
        "", user_id=user_id, progress_token=progress_token, automatic_recommendation=True,
        extra_interests=profile.interests or None,
        **{f"known_{field}": getattr(profile, field) for field in (
            "region", "gender", "birth_date", "disability_status", "income_bracket",
            "household_types", "veteran_status",
        )},
    )


def _begin_progress(
    session_id: str, *, user_id: int, progress_token: str | None,
    resuming: bool = False, carry_elapsed: bool = False
) -> None:
    """진행률 추적을 켠다(``GET /api/v1/chat/progress/{token}``가 읽는다).

    추적 키는 ``session_id``다 - 그래프 노드가 자기 state의 ``query_id``로
    진행 상황을 기록하고, 그 값이 곧 ``session_id``이기 때문이다(progress.py).
    첫 상담(API-10/14)에서는 이 ``session_id``를 서버가 방금 만들어 클라이언트가
    아직 모르므로, 클라이언트가 보낸 토큰을 같은 기록의 별칭으로 걸어 둔다.
    ``owner``로 다른 회원이 남의 진행 상황을 조회하는 것을 막는다.

    ``resuming``이면(되묻기에 답해 재개하는 요청) 이전 턴에서 끝낸 단계 수를
    물려받아 막대를 **이어서** 그린다. 그래프도 처음부터 다시 돌지 않고 멈춘
    자리에서 이어가므로(N10a -> N9, builder.py E18b), 0부터 다시 그리면
    "답했더니 처음부터 다시 하네"로 보인다. 분모도 그만큼 키운다 - 남은
    구간이 실제로는 몇 노드 안 되는데 분모가 14 그대로면 막대가 거의 안
    움직인다.
    """

    record = chat_session_store.get(session_id, user_id=user_id)
    carried = record.completed_steps if resuming and record is not None else 0
    PROGRESS.start(
        session_id,
        total_steps=max(18, carried + EXPECTED_RESUME_NODE_COUNT) if resuming else EXPECTED_NODE_COUNT,
        owner=user_id,
        aliases=(progress_token,) if progress_token else (),
        carried_steps=carried,
        # 새 정책 검색은 0초부터 시작하고, 기존 상담의 추가 질문은 같은
        # 상담의 누적 처리 시간을 이어서 표시한다.
        carried_seconds=record.elapsed_seconds if (resuming or carry_elapsed) and record else 0.0,
    )


def _finish_progress(session_id: str, *, user_id: int, failed: bool = False) -> None:
    PROGRESS.finish(session_id, failed=failed)
    record = chat_session_store.get(session_id, user_id=user_id)
    progress = PROGRESS.snapshot(session_id, owner=user_id)
    if record is not None and progress is not None:
        record.elapsed_seconds = progress["elapsed_seconds"]
        record.completed_steps = progress["completed_steps"]


def _start_chat(
    message: str, *, user_id: int, progress_token: str | None = None, **kwargs
) -> ChatResponse:
    session_id = str(uuid.uuid4())
    graph = None

    def capture_graph(instance):
        nonlocal graph
        graph = instance

    _begin_progress(session_id, user_id=user_id, progress_token=progress_token)
    try:
        raw = _run(
            ask,
            message,
            session_id,
            **kwargs,
            _on_graph_ready=capture_graph,
        )
        raw = _augment_required_documents(raw)
        response = ChatResponse.model_validate(raw)
        chat_session_store.create(session_id, user_id=user_id)
        _cache_last_response(session_id, raw, user_id=user_id)
        _finish_progress(session_id, user_id=user_id)
        return response
    except Exception:
        _finish_progress(session_id, user_id=user_id, failed=True)
        # 초기화 실패 때 get_graph()를 다시 호출하면 다른 그래프를 만들 수 있다.
        # 두 정리는 독립적으로 시도하고 원래 HTTP 오류는 그대로 전달한다.
        try:
            if graph is not None and graph.checkpointer is not None:
                graph.checkpointer.delete_thread(session_id)
        except Exception:
            _log.warning("실패한 새 상담의 체크포인트 정리에 실패했습니다.")
        try:
            chat_session_store.delete(session_id)
        except Exception:
            _log.warning("실패한 새 상담의 소유권 정리에 실패했습니다.")
        raise


def continue_chat(
    session_id: str, message: str | dict, *, user_id: int, progress_token: str | None = None,
    top_k: int | None = None, extra_interests: list[str] | None = None,
) -> ChatResponse:
    # start_chat()과 동일하게 빈 배열은 "관심사 없음 전달"이 아니라 "관심사
    # 미변경"으로 취급한다 - 그대로 넘기면 builder.resume_graph()가
    # `is not None` 체크만 하므로 빈 배열도 갱신 신호로 보고 기존/프로필
    # 관심사(예: 보훈 회원의 국가유공자 검색어)를 지워버린다.
    extra_interests = extra_interests or None
    with chat_session_store.locked(session_id, user_id=user_id) as record:
        if record is None:
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                "SESSION_NOT_FOUND",
                "세션이 만료되었거나 존재하지 않습니다. 새로 상담을 시작해주세요.",
            )
        # 소유권을 확인한 뒤에 켠다 - 남의 session_id를 찍어보는 요청이
        # 진행률 기록만 남기고 가지 않게 한다. "이어서 진행하는" 재개인지는
        # answer_followup()을 부르기 **전에** 봐야 안다 - 부르고 나면 이미
        # 재개가 끝나 interrupt가 사라진다. 개인정보 폼(N3)은 N1부터 다시
        # 돌므로 이어붙이지 않는다(resumes_forward 참고).
        _begin_progress(
            session_id,
            user_id=user_id,
            progress_token=progress_token,
            resuming=resumes_forward(session_id),
            carry_elapsed=record.awaiting_input,
        )
        try:
            raw = _run(
                answer_followup, session_id, message,
                top_k=top_k, extra_interests=extra_interests,
            )
            raw = _augment_required_documents(raw)
            response = ChatResponse.model_validate(raw)
            _cache_last_response(session_id, raw, user_id=user_id)
        except Exception:
            _finish_progress(session_id, user_id=user_id, failed=True)
            raise
        _finish_progress(session_id, user_id=user_id)
        return response


def delete_chat_session(session_id: str, *, user_id: int) -> None:
    with chat_session_store.locked(session_id, user_id=user_id) as record:
        if record is None:
            # 소유권이 없거나 이미 없는 세션은 멱등 성공 처리한다(API-13).
            return
        graph = get_graph()
        if graph.checkpointer is not None:
            graph.checkpointer.delete_thread(session_id)
        chat_session_store.delete(session_id)
        PROGRESS.discard(session_id)


def delete_all_chat_sessions(*, user_id: int) -> None:
    """탈퇴 완료 후 호출한다. 호출자는 회원 잠금을 보유해 새 상담을 막는다."""

    for session_id in chat_session_store.session_ids_for_user(user_id):
        try:
            delete_chat_session(session_id, user_id=user_id)
        except (Exception, SystemExit):
            _log.warning("탈퇴한 회원의 상담 체크포인트 정리에 실패했습니다.")
        finally:
            # 그래프 조회/삭제 실패가 탈퇴 결과를 가리거나 개인정보 캐시를 남기면 안 된다.
            chat_session_store.delete(session_id)
