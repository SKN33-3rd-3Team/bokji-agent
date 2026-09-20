"""API-09(검색 조건 옵션 조회)."""

from __future__ import annotations

from fastapi import APIRouter

from ...core.options import build_search_options

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("/search-options")
def get_search_options() -> dict:
    return build_search_options()
