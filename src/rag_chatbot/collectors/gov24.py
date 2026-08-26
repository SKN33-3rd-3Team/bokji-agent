"""보조금24 API 수집 스크립트 — 목록조회/상세조회/지원조건조회 3종.

흐름:
    1. serviceList (목록조회) — 페이지를 넘기면서 전체 정책 목록을 모은다.
    2. serviceDetail (상세조회) — 1에서 모은 서비스ID마다 상세정보를 하나씩 조회한다.
    3. supportConditions (지원조건조회) — 마찬가지로 서비스ID마다 JA코드 조건을 조회한다.

2, 3번은 서비스ID 하나당 결과 하나라서, 여러 개를 동시에(병렬로) 요청해서
속도를 낸다. 진행 상황은 실시간으로 출력되고(flush=True), 일정 건수마다
중간 저장(체크포인트)도 하기 때문에 중간에 중단돼도 처음부터 다시 하지
않아도 된다.

사용법:
    python -m rag_chatbot.collectors.gov24 list       # 1번만
    python -m rag_chatbot.collectors.gov24 detail      # 2번만 (1번 결과 파일 필요)
    python -m rag_chatbot.collectors.gov24 conditions  # 3번만 (1번 결과 파일 필요)
    python -m rag_chatbot.collectors.gov24 all         # 1 -> 2 -> 3 순서로 전부
    python -m rag_chatbot.collectors.gov24 detail 50   # 테스트: 앞 50건만

주의: BASE_URL과 파라미터 이름(serviceKey/page/perPage/servId 등)은 공공데이터
API에서 흔히 쓰이는 패턴을 예시로 적어둔 것이다. data.go.kr 활용가이드 문서를
보고 실제 값으로 꼭 확인해야 한다. (지난 실행에서 실제로 이 값들이 맞았다.)
"""

import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GOV24_SERVICE_KEY = os.getenv("GOV24_SERVICE_KEY", "")

BASE = "https://api.odcloud.kr/api/gov24/v3"
LIST_URL = f"{BASE}/serviceList"
DETAIL_URL = f"{BASE}/serviceDetail"
CONDITIONS_URL = f"{BASE}/supportConditions"

LIST_OUT = "data/raw/gov24_service_list.json"
DETAIL_OUT = "data/raw/gov24_service_detail.json"
CONDITIONS_OUT = "data/raw/gov24_support_conditions.json"

MAX_WORKERS = 6          # 동시에 보낼 요청 개수
CHECKPOINT_EVERY = 200    # 이만큼 처리할 때마다 중간 저장
PROGRESS_EVERY = 20       # 이만큼 처리할 때마다 진행상황 출력


def log(msg: str) -> None:
    print(msg, flush=True)  # flush=True로 즉시 화면/로그파일에 반영


def _require_key() -> None:
    if not GOV24_SERVICE_KEY:
        raise ValueError(
            ".env 파일에 GOV24_SERVICE_KEY가 비어 있습니다. "
            "발급받은 인증키를 .env에 채워 넣으세요."
        )


def save(items: list[dict], out_path: str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. serviceList — 목록조회 (페이지 순회, 순차 처리로 충분히 빠름)
# ---------------------------------------------------------------------------


def fetch_list_page(page: int, per_page: int = 100) -> dict:
    params = {"serviceKey": GOV24_SERVICE_KEY, "page": page, "perPage": per_page}
    response = requests.get(LIST_URL, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def fetch_list_all() -> list[dict]:
    _require_key()
    all_items: list[dict] = []
    page = 1
    per_page = 100

    while True:
        data = fetch_list_page(page=page, per_page=per_page)
        items = data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        log(f"[list] {page}페이지: {len(items)}건 수집 (누적 {len(all_items)}건)")
        if len(items) < per_page:
            break
        page += 1

    return all_items


# ---------------------------------------------------------------------------
# 2·3. serviceDetail / supportConditions — 서비스ID당 1건, 병렬 처리
# ---------------------------------------------------------------------------


def _fetch_one(kind: str, url: str, service_id: str) -> tuple[str, dict | None, str | None]:
    params = {
        "serviceKey": GOV24_SERVICE_KEY,
        # TODO: 활용가이드에서 실제 파라미터 이름 확인 (servId, serviceId 등일 수 있음)
        "servId": service_id,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return service_id, response.json(), None
    except requests.exceptions.RequestException as e:
        return service_id, None, str(e)


def fetch_many(kind: str, url: str, service_ids: list[str], out_path: str) -> list[dict]:
    """service_ids를 MAX_WORKERS개씩 동시에 호출하면서 결과를 모은다.

    진행 상황을 실시간으로 출력하고, CHECKPOINT_EVERY건마다 중간 저장한다.
    """
    _require_key()

    results: list[dict] = []
    failed_ids: list[str] = []
    total = len(service_ids)
    done = 0
    start = time.time()

    log(f"[{kind}] 시작: 총 {total}건, 동시 요청 {MAX_WORKERS}개")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, kind, url, sid): sid for sid in service_ids}

        for future in concurrent.futures.as_completed(futures):
            service_id, data, err = future.result()
            done += 1

            if err:
                failed_ids.append(service_id)
                log(f"[{kind}] {service_id} 실패: {err}")
            else:
                results.append(data)

            if done % PROGRESS_EVERY == 0 or done == total:
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                remaining = total - done
                eta_min = (remaining / rate / 60) if rate > 0 else 0
                log(
                    f"[{kind}] {done}/{total}건 처리 "
                    f"(성공 {len(results)}, 실패 {len(failed_ids)}) "
                    f"- {rate:.1f}건/초, 예상 남은시간 약 {eta_min:.1f}분"
                )

            if done % CHECKPOINT_EVERY == 0:
                save(results, out_path)
                log(f"[{kind}] 체크포인트 저장 ({len(results)}건) -> {out_path}")

    save(results, out_path)
    log(f"[{kind}] 최종 저장 완료: {out_path} (성공 {len(results)}건, 실패 {len(failed_ids)}건)")

    if failed_ids:
        failed_path = out_path.replace(".json", "_failed_ids.json")
        Path(failed_path).write_text(
            json.dumps(failed_ids, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log(f"[{kind}] 실패한 서비스ID {len(failed_ids)}건 저장: {failed_path}")

    return results


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def _service_ids_from_list_file() -> list[str]:
    items = load(LIST_OUT)
    return [item["서비스ID"] for item in items if item.get("서비스ID")]


def run_list() -> None:
    save(fetch_list_all(), LIST_OUT)


def run_detail(limit: int | None = None) -> None:
    ids = _service_ids_from_list_file()
    if limit:
        ids = ids[:limit]
        log(f"[detail] 테스트 모드: 앞에서 {limit}건만 조회")
    fetch_many("detail", DETAIL_URL, ids, DETAIL_OUT)


def run_conditions(limit: int | None = None) -> None:
    ids = _service_ids_from_list_file()
    if limit:
        ids = ids[:limit]
        log(f"[conditions] 테스트 모드: 앞에서 {limit}건만 조회")
    fetch_many("conditions", CONDITIONS_URL, ids, CONDITIONS_OUT)


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "list"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

    if step == "list":
        run_list()
    elif step == "detail":
        run_detail(limit)
    elif step == "conditions":
        run_conditions(limit)
    elif step == "all":
        run_list()
        run_detail(limit)
        run_conditions(limit)
    else:
        log("사용법: python -m rag_chatbot.collectors.gov24 [list|detail|conditions|all] [테스트건수]")
