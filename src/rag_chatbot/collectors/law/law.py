"""국가법령정보센터 Open API에서 법령·행정규칙·자치법규를 검색/목록조회한다.

본문(상세조회) 없이 이름·ID 검색/목록조회만 담당한다. build_index.py가
list_all()로 전체 목록을 캐시하고, filter_index.py/generate_law_targets.py가
search_law()/search_law_all()로 키워드 검색을 한다.

주의: BASE_URL과 파라미터 이름(target 코드, 검색 응답의 ID 필드명)은
공개된 문서 기준 흔히 쓰이는 값으로 적어둔 것이다. OC 발급 후 마이페이지에서
받는 실제 활용가이드 문서로 최종 검증 필요.
"""

from __future__ import annotations

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://www.law.go.kr/DRF"
OC = os.getenv("LAW_OC")

TARGET_ORDER = ["law", "admrul", "ordin"]
TARGET_LABEL = {"law": "법령", "admrul": "행정규칙", "ordin": "자치법규"}

MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 1.5
REQUEST_TIMEOUT = 10
REQUEST_INTERVAL_SEC = 0.2


class LawApiError(Exception):
    pass


def _request_with_retry(url: str, params: dict) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise LawApiError(f"{url} 호출 {MAX_RETRIES}회 실패: {last_error}")


def _search_law_page(
    name: str, target: str, *, display: int, search: int, page: int
) -> tuple[list[dict], int]:
    """한 페이지만 검색해서 (후보 목록, totalCnt)를 돌려준다."""

    params = {
        "OC": OC,
        "target": target,
        "type": "JSON",
        "query": name,
        "search": search,
        "display": display,
        "page": page,
    }
    data = _request_with_retry(f"{BASE_URL}/lawSearch.do", params)
    # 응답 루트 키가 target별로 달라서(LawSearch/AdmRulSearch/OrdinSearch 등)
    # 값을 유연하게 찾는다.
    root = next(iter(data.values())) if data else {}
    total_cnt = int(root.get("totalCnt", 0)) if isinstance(root, dict) else 0

    for value in data.values():
        if isinstance(value, list):
            return value, total_cnt
        if isinstance(value, dict):
            for inner in value.values():
                if isinstance(inner, list):
                    return inner, total_cnt
    return [], total_cnt


def search_law(name: str, target: str, *, display: int = 20, search: int = 1) -> list[dict]:
    """법령명으로 검색해서 후보 목록을 돌려준다 (없으면 빈 리스트).

    search=1(기본값)은 법령명 검색, search=2는 본문검색. 결과가 display
    개수보다 많아도 첫 페이지만 가져온다 — 전체를 다 받으려면
    search_law_all()을 쓴다.
    """

    candidates, _ = _search_law_page(name, target, display=display, search=search, page=1)
    return candidates


def search_law_all(
    name: str, target: str, *, page_size: int = 100, search: int = 1
) -> list[dict]:
    """검색 결과가 display 개수보다 많아도 페이지를 넘기면서 전부 가져온다.

    "기본법"처럼 흔한 키워드는 결과가 page_size보다 훨씬 많을 수 있어서
    (예: 205건), 페이지네이션 없이 한 번만 호출하면 뒷부분을 놓친다.
    """

    all_candidates: list[dict] = []
    page = 1
    while True:
        candidates, total_cnt = _search_law_page(
            name, target, display=page_size, search=search, page=page
        )
        if not candidates:
            break
        all_candidates.extend(candidates)
        if len(all_candidates) >= total_cnt or len(candidates) < page_size:
            break
        page += 1
    return all_candidates


def list_all(target: str, *, page_size: int = 100, on_progress=None) -> list[dict]:
    """query 없이(=전체) 페이지를 넘기며 이름·ID만 있는 목록 전체를 가져온다.

    본문(상세조회)은 안 부르고 목록만 받아오는 거라 훨씬 빠르고 싸다
    (법령 5,612건이면 페이지 57번, 자치법규 161,191건이면 1,612번 정도).
    이렇게 받은 전체 목록을 로컬에서 공식 ID 기준으로 중복 제거하면 API 쪽
    검색 한계(결과 잘림 등) 없이 목록 전체를 보존할 수 있다.
    """

    all_items: list[dict] = []
    page = 1
    while True:
        params = {
            "OC": OC,
            "target": target,
            "type": "JSON",
            "display": page_size,
            "page": page,
        }
        data = _request_with_retry(f"{BASE_URL}/lawSearch.do", params)
        root = next(iter(data.values())) if data else {}
        total_cnt = int(root.get("totalCnt", 0)) if isinstance(root, dict) else 0

        items: list[dict] = []
        for value in data.values():
            if isinstance(value, list):
                items = value
                break
            if isinstance(value, dict):
                for inner in value.values():
                    if isinstance(inner, list):
                        items = inner
                        break

        if not items:
            break
        all_items.extend(items)
        if on_progress:
            on_progress(len(all_items), total_cnt)
        if len(all_items) >= total_cnt or len(items) < page_size:
            break
        page += 1
    return all_items


def _extract_law_id(candidate: dict) -> str | None:
    for key in ("법령일련번호", "MST", "행정규칙일련번호", "자치법규일련번호", "ID"):
        if key in candidate:
            return str(candidate[key])
    return None
