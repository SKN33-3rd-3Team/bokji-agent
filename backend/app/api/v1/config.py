"""API-09(검색 조건 옵션 조회) + 서버 준비 상태 조회."""

from __future__ import annotations

from fastapi import APIRouter

from ...core.options import build_search_options

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("/search-options")
def get_search_options() -> dict:
    return build_search_options()


@router.get("/status")
def get_status() -> dict:
    """서버 구동 워밍업(임베딩 모델 로드 등)이 끝났는지 알려준다.

    ``main.lifespan``이 워밍업을 별도 스레드에서 돌리기 때문에(첫 상담을
    빠르게 만들면서도 로그인/회원가입은 곧바로 되게 하려고), 그 사이에 상담을
    시작하면 첫 응답만 유난히 느리다. 화면이 그 사실을 "검색 엔진 준비 중"으로
    미리 알려줄 수 있게 상태를 그대로 노출한다.

    인증을 요구하지 않는다 - 노출하는 값이 준비 상태/소요 시간뿐이고,
    로그인 화면에서도 읽어야 하기 때문이다.
    """

    from src.rag_chatbot.service import warmup_state

    return warmup_state()
