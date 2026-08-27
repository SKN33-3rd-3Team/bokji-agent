"""보조금24 API 수집 스크립트 — 목록조회/상세조회/지원조건조회 3종.

흐름:
    1. serviceList (목록조회) — 페이지를 넘기면서 전체 정책 목록을 모은다.
    2. serviceDetail (상세조회) — 1에서 모은 서비스ID마다 상세정보를 하나씩 조회한다.
    3. supportConditions (지원조건조회) — 마찬가지로 서비스ID마다 JA코드 조건을 조회한다.

2, 3번은 서비스ID 하나당 결과 하나라서, 여러 개를 동시에(병렬로) 요청해서
속도를 낸다. 진행 상황은 실시간으로 출력되고(flush=True), 일정 건수마다
중간 저장(체크포인트)도 하기 때문에 중간에 중단돼도 처음부터 다시 하지
않아도 된다.

재시도·재실행 정책:
    - 요청 하나가 실패하면(RequestException) 지수 백오프로 최대
      MAX_RETRIES번까지 같은 요청을 재시도한 뒤에도 실패해야 최종 실패로
      기록한다.
    - 이 스크립트를 다시 실행하면(예: detail을 다시 돌리는 경우), 이전에
      이미 성공한 서비스ID는 out_path에서 그대로 재사용하고 재호출하지
      않는다. 이전에 실패했던 서비스ID만 다시 시도한다. 서비스ID를 key로
      병합하기 때문에 몇 번을 재실행해도 같은 서비스ID가 중복 레코드로
      쌓이지 않는다.

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
import random
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

load_dotenv()

# data.go.kr는 발급키를 이미 URL-encode된 형태("Encoding" 키, 예: %2F, %3D%3D
# 포함)로도 제공한다. 이걸 그대로 requests의 params에 넘기면 requests가
# 또 한 번 encode해서 %2F -> %252F 처럼 이중 인코딩되어 401 Unauthorized가
# 난다. unquote()로 한 번 미리 풀어두면(raw 키를 넣었을 때는 그대로 아무
# 변화가 없다) requests가 params에서 정확히 한 번만 encode하게 된다.
GOV24_SERVICE_KEY = unquote(os.getenv("GOV24_SERVICE_KEY", ""))

BASE = "https://api.odcloud.kr/api/gov24/v3"
LIST_URL = f"{BASE}/serviceList"
DETAIL_URL = f"{BASE}/serviceDetail"
CONDITIONS_URL = f"{BASE}/supportConditions"

# main_gov24.py를 어느 위치(gov_24/ 안, 리포지토리 루트 등)에서 실행하든
# 항상 같은 data/ 폴더에 저장/조회하도록, 이 파일 위치를 기준으로 프로젝트
# 루트를 고정 경로로 잡는다(현재 작업 디렉터리(cwd)에 의존하지 않음).
PROJECT_ROOT = Path(__file__).resolve().parents[4]
LIST_OUT = str(PROJECT_ROOT / "data" / "raw" / "gov24_service_list.json")
DETAIL_OUT = str(PROJECT_ROOT / "data" / "raw" / "gov24_service_detail.json")
CONDITIONS_OUT = str(PROJECT_ROOT / "data" / "raw" / "gov24_support_conditions.json")

MAX_WORKERS = 6           # 동시에 보낼 요청 개수
CHECKPOINT_EVERY = 200     # 이만큼 처리할 때마다 중간 저장
PROGRESS_EVERY = 20        # 이만큼 처리할 때마다 진행상황 출력
MAX_RETRIES = 3            # 요청 하나당 최대 시도 횟수(최초 시도 포함)
BACKOFF_BASE_SEC = 1.0     # 지수 백오프 기준 시간(초)


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
    """목록조회 한 페이지를 가져온다. 실패하면 재시도하며, 시도마다 오류 구간과
    API 주소를 콘솔에 남긴다."""
    params = {"serviceKey": GOV24_SERVICE_KEY, "page": page, "perPage": per_page}
    last_err: requests.exceptions.RequestException | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(LIST_URL, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            last_err = e
            log(
                f"[list] 오류 발생(시도 {attempt}/{MAX_RETRIES}) "
                f"- 구간: list/{page}페이지, API: {LIST_URL} - {e}"
            )
            if attempt < MAX_RETRIES:
                sleep_sec = BACKOFF_BASE_SEC * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(sleep_sec)
    raise last_err


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
# 2·3. serviceDetail / supportConditions — 서비스ID당 1건, 병렬 처리 + 재시도
# ---------------------------------------------------------------------------


def _fetch_one(kind: str, url: str, service_id: str) -> tuple[str, dict | None, str | None]:
    """요청 하나를 최대 MAX_RETRIES번까지 시도한 뒤 성공/실패를 반환한다.

    재시도는 이 호출 하나에만 국한되며, 다른 서비스ID의 이미 성공한 결과에는
    전혀 영향을 주지 않는다. 실패할 때마다 재시도 횟수, 오류난 구간(kind와
    서비스ID), API 주소를 콘솔에 즉시 남긴다.
    """
    params = {
        "serviceKey": GOV24_SERVICE_KEY,
        # TODO: 활용가이드에서 실제 파라미터 이름 확인 (servId, serviceId 등일 수 있음)
        "servId": service_id,
    }
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return service_id, response.json(), None
        except requests.exceptions.RequestException as e:
            last_err = str(e)
            log(
                f"[{kind}] 오류 발생(시도 {attempt}/{MAX_RETRIES}) "
                f"- 구간: {kind}(servId={service_id}), API: {url} - {last_err}"
            )
            if attempt < MAX_RETRIES:
                sleep_sec = BACKOFF_BASE_SEC * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(sleep_sec)
    return service_id, None, last_err


def _service_id_of(item: dict) -> str | None:
    """serviceDetail/supportConditions 원본 응답에서 서비스ID를 찾는다.

    실제 API 응답은 {"data": [{...}]} 형태로 감싸져 있을 수 있어서
    (merge_gov24.unwrap()과 동일한 가정), 감싸져 있으면 벗겨서 찾는다.
    """
    inner = item.get("data")
    if isinstance(inner, list) and inner:
        inner = inner[0]
    if isinstance(inner, dict) and inner.get("서비스ID"):
        return inner.get("서비스ID")
    return item.get("서비스ID")


def fetch_many(
    kind: str,
    url: str,
    service_ids: list[str],
    out_path: str,
    *,
    resume: bool = True,
) -> list[dict]:
    """service_ids를 MAX_WORKERS개씩 동시에 호출하면서 결과를 모은다.

    진행 상황을 실시간으로 출력하고, CHECKPOINT_EVERY건마다 중간 저장한다.

    resume=True(기본값)이면 out_path에 이미 저장된 성공 결과를 재사용하고,
    실패 목록 파일(``{out_path 이름}_failed_ids.json``)에 있던 서비스ID만
    다시 시도한다. 결과는 항상 서비스ID를 key로 병합하므로, 몇 번을
    재실행해도 같은 서비스ID가 중복 레코드로 쌓이지 않는다.
    """
    _require_key()

    failed_path = out_path.replace(".json", "_failed_ids.json")

    existing_by_id: dict[str, dict] = {}
    ids_to_fetch = list(service_ids)
    if resume and Path(out_path).exists():
        for item in load(out_path):
            sid = _service_id_of(item)
            if sid:
                existing_by_id[sid] = item
        previously_failed = set(load(failed_path)) if Path(failed_path).exists() else set()
        ids_to_fetch = [
            sid for sid in service_ids if sid not in existing_by_id or sid in previously_failed
        ]
        skipped = len(service_ids) - len(ids_to_fetch)
        if skipped:
            log(f"[{kind}] 이미 성공한 {skipped}건은 재호출하지 않고 재사용합니다.")

    if not ids_to_fetch:
        log(f"[{kind}] 재호출할 서비스ID가 없습니다. 기존 {len(existing_by_id)}건을 그대로 사용합니다.")
        return list(existing_by_id.values())

    new_results: list[dict] = []
    failed_ids: list[str] = []
    total = len(ids_to_fetch)
    done = 0
    start = time.time()

    def _flush() -> dict[str, dict]:
        merged = dict(existing_by_id)
        for item in new_results:
            sid = _service_id_of(item)
            if sid:
                merged[sid] = item  # 서비스ID로 병합 -> 재실행해도 중복 불가능
        save(list(merged.values()), out_path)
        return merged

    log(f"[{kind}] 시작: 총 {total}건(재사용 {len(existing_by_id)}건 제외), 동시 요청 {MAX_WORKERS}개")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, kind, url, sid): sid for sid in ids_to_fetch}

        for future in concurrent.futures.as_completed(futures):
            service_id, data, err = future.result()
            done += 1

            if err:
                failed_ids.append(service_id)
                log(f"[{kind}] {service_id} 실패({MAX_RETRIES}회 시도 후): {err}")
            else:
                new_results.append(data)

            if done % PROGRESS_EVERY == 0 or done == total:
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                remaining = total - done
                eta_min = (remaining / rate / 60) if rate > 0 else 0
                log(
                    f"[{kind}] {done}/{total}건 처리 "
                    f"(성공 {len(new_results)}, 실패 {len(failed_ids)}) "
                    f"- {rate:.1f}건/초, 예상 남은시간 약 {eta_min:.1f}분"
                )

            if done % CHECKPOINT_EVERY == 0:
                merged = _flush()
                log(f"[{kind}] 체크포인트 저장 (누적 {len(merged)}건) -> {out_path}")

    merged = _flush()
    log(
        f"[{kind}] 최종 저장 완료: {out_path} "
        f"(전체 {len(merged)}건, 이번 실행 성공 {len(new_results)}건, 실패 {len(failed_ids)}건)"
    )

    if failed_ids:
        Path(failed_path).write_text(
            json.dumps(failed_ids, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log(f"[{kind}] 실패한 서비스ID {len(failed_ids)}건 저장: {failed_path}")
    elif Path(failed_path).exists():
        # 이번 실행에서 전부 성공했다면 이전 실패 목록은 더 이상 유효하지 않으므로 비운다.
        Path(failed_path).write_text("[]", encoding="utf-8")

    return list(merged.values())


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
