"""보조금24 API 수집 스크립트 — 목록조회/상세조회/지원조건조회 3종.

흐름:
    1. serviceList (목록조회) — 페이지를 넘기면서 전체 정책 목록을 모은다.
    2. serviceDetail (상세조회) — 1에서 모은 서비스ID마다 상세정보를 하나씩 조회한다.
    3. supportConditions (지원조건조회) — 마찬가지로 서비스ID마다 JA코드 조건을 조회한다.

2, 3번 API도 목록형 응답이다. 전체 수집은 매번 1페이지부터
``page``/``perPage``로 순회하고, 완전성 검증 후에만 기존 snapshot을
교체한다. 단건 재시도는 두 endpoint 모두
``cond[서비스ID::EQ]`` 필터를 사용한다.

재시도·재실행 정책:
    - 요청 하나가 실패하면(RequestException) 지수 백오프로 최대
      MAX_RETRIES번까지 같은 요청을 재시도한 뒤에도 실패해야 최종 실패로
      기록한다.
    - 전체 detail/conditions 재실행은 기존 성공분을 재사용하지
      않는다. 새 ``.partial``에 전체를 받은 뒤 검증이 성공하면
      최종 파일을 원자적으로 교체한다.

사용법:
    python -m rag_chatbot.collectors.gov_24.gov24 list
    python -m rag_chatbot.collectors.gov_24.gov24 detail
    python -m rag_chatbot.collectors.gov_24.gov24 conditions
    python -m rag_chatbot.collectors.gov_24.gov24 conditions-retry-failed
    python -m rag_chatbot.collectors.gov_24.gov24 all
    python -m rag_chatbot.collectors.gov_24.gov24 detail 50

``serviceDetail``과 ``supportConditions`` 단건 조회는 공식 ODcloud
``cond[서비스ID::EQ]=<서비스ID>``를 사용한다.
"""

import concurrent.futures
import json
import os
import random
import re
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote

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
DATASET_PAGE_SIZE = 1000   # 실제 API에서 확인한 안전한 페이지 크기

_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)((?:servicekey|api[_-]?key|token|authorization)\s*(?:=|%3d)\s*)"
    r"[^&\s,;\]\)>'\"]+"
)


class Gov24RequestError(RuntimeError):
    """인증정보를 제거한 뒤 외부로 전달하는 API 요청 오류."""


def log(msg: str) -> None:
    print(msg, flush=True)  # flush=True로 즉시 화면/로그파일에 반영


def _redact_sensitive_text(value: object) -> str:
    text = str(value)
    encoded_secrets = {
        quote(GOV24_SERVICE_KEY, safe=""),
        quote_plus(GOV24_SERVICE_KEY, safe=""),
    }
    secrets = {GOV24_SERVICE_KEY, *encoded_secrets}
    secrets.update(
        re.sub(r"%[0-9A-Fa-f]{2}", lambda match: match.group(0).lower(), encoded)
        for encoded in encoded_secrets
    )
    for secret in sorted((secret for secret in secrets if secret), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", text)


def _safe_request_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {_redact_sensitive_text(error)}"


def _require_key() -> None:
    if not GOV24_SERVICE_KEY:
        raise ValueError(
            ".env 파일에 GOV24_SERVICE_KEY가 비어 있습니다. "
            "발급받은 인증키를 .env에 채워 넣으세요."
        )


def save(items: object, out_path: str) -> None:
    """JSON을 같은 디렉터리의 임시 파일에 쓴 뒤 원자적으로 교체한다."""

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(items, ensure_ascii=False, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


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
            safe_error = _safe_request_error(e)
            log(
                f"[list] 오류 발생(시도 {attempt}/{MAX_RETRIES}) "
                f"- 구간: list/{page}페이지, API: {LIST_URL} - {safe_error}"
            )
            if attempt < MAX_RETRIES:
                sleep_sec = BACKOFF_BASE_SEC * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(sleep_sec)
    raise Gov24RequestError(_safe_request_error(last_err)) from None


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
        "cond[서비스ID::EQ]": service_id,
    }
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
            row = _exact_payload_row(payload, service_id)
            if row is None:
                raise Gov24RequestError(
                    f"{kind} exact one-row response contract mismatch"
                )
            return service_id, row, None
        except (requests.exceptions.RequestException, Gov24RequestError) as e:
            last_err = _safe_request_error(e)
            log(
                f"[{kind}] 오류 발생(시도 {attempt}/{MAX_RETRIES}) "
                f"- 구간: {kind}(서비스ID={service_id}), API: {url} - {last_err}"
            )
            if attempt < MAX_RETRIES:
                sleep_sec = BACKOFF_BASE_SEC * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(sleep_sec)
    return service_id, None, last_err


def _service_id_of(row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    service_id = row.get("서비스ID")
    if (
        not isinstance(service_id, str)
        or not service_id
        or service_id != service_id.strip()
    ):
        return None
    return service_id


def _exact_payload_row(payload: object, requested_id: str) -> dict | None:
    """exact 응답을 검증하고 canonical flat row를 반환한다."""

    if not isinstance(payload, dict):
        return None
    for count_name in ("matchCount", "currentCount"):
        count = payload.get(count_name)
        if isinstance(count, bool) or not isinstance(count, int) or count != 1:
            return None
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return None
    row = data[0]
    if _service_id_of(row) != requested_id:
        return None
    return dict(row)


def _canonical_stored_row(item: object) -> dict | None:
    """flat row와 기존 exact wrapper를 canonical flat row로 읽는다."""

    service_id = _service_id_of(item)
    if service_id is not None:
        return dict(item)
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    if not isinstance(data, list) or len(data) != 1:
        return None
    row_id = _service_id_of(data[0])
    if row_id is None:
        return None
    return _exact_payload_row(item, row_id)


def _validate_service_ids(values: Sequence[object], *, label: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or value in seen
        ):
            raise Gov24RequestError(f"{label} contains an invalid or duplicate service ID")
        seen.add(value)
        result.append(value)
    return tuple(result)


def fetch_dataset_all(
    kind: str,
    url: str,
    out_path: str,
    *,
    expected_service_ids: Sequence[str],
    per_page: int = DATASET_PAGE_SIZE,
) -> list[dict]:
    """1페이지부터 새 partial에 수집하고 완전할 때만 promote한다."""

    _require_key()
    if per_page < 1:
        raise ValueError("per_page must be positive")
    expected_ids = _validate_service_ids(
        expected_service_ids, label="serviceList"
    )
    expected_set = set(expected_ids)
    output_path = Path(out_path)
    partial_path = Path(f"{out_path}.partial")
    partial_path.unlink(missing_ok=True)

    rows_by_id: dict[str, dict] = {}
    page = 1
    total_count: int | None = None
    while total_count is None or len(rows_by_id) < total_count:
        params = {
            "serviceKey": GOV24_SERVICE_KEY,
            "page": page,
            "perPage": per_page,
        }
        last_error: BaseException | None = None
        payload: dict | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                candidate = response.json()
                if not isinstance(candidate, dict):
                    raise ValueError("API response must be an object")
                payload = candidate
                break
            except (requests.exceptions.RequestException, ValueError) as exc:
                last_error = exc
                safe_error = _safe_request_error(exc)
                log(
                    f"[{kind}] {page}페이지 오류(시도 {attempt}/{MAX_RETRIES}) "
                    f"- API: {url} - {safe_error}"
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE_SEC * (2 ** (attempt - 1)))
        if payload is None:
            raise Gov24RequestError(
                _safe_request_error(last_error or ValueError("invalid response"))
            )

        page_rows = payload.get("data")
        if not isinstance(page_rows, list):
            raise Gov24RequestError(f"{kind} response data must be a list")
        raw_total = payload.get("totalCount")
        if isinstance(raw_total, bool) or not isinstance(raw_total, int) or raw_total < 0:
            raise Gov24RequestError(f"{kind} response totalCount is invalid")
        if total_count is None:
            total_count = raw_total
            if total_count != len(expected_set):
                raise Gov24RequestError(
                    f"{kind} totalCount does not match serviceList"
                )
        elif raw_total != total_count:
            raise Gov24RequestError(f"{kind} totalCount changed between pages")
        if not page_rows and len(rows_by_id) < total_count:
            raise Gov24RequestError(f"{kind} ended before totalCount was collected")

        for row in page_rows:
            service_id = _service_id_of(row)
            if service_id is None or service_id in rows_by_id:
                raise Gov24RequestError(
                    f"{kind} contains an invalid or duplicate service ID"
                )
            rows_by_id[service_id] = dict(row)
        if len(rows_by_id) > total_count:
            raise Gov24RequestError(f"{kind} collected more rows than totalCount")
        save(list(rows_by_id.values()), str(partial_path))
        log(
            f"[{kind}] {page}페이지: {len(page_rows)}건 "
            f"(고유 {len(rows_by_id)}/{total_count}건)"
        )
        page += 1

    if total_count is None or len(rows_by_id) != total_count:
        raise Gov24RequestError(f"{kind} row count does not match totalCount")
    if set(rows_by_id) != expected_set:
        raise Gov24RequestError(f"{kind} service IDs do not match serviceList")

    os.replace(partial_path, output_path)
    failed_path = out_path.replace(".json", "_failed_ids.json")
    save([], failed_path)
    result = list(rows_by_id.values())
    return result


def fetch_many(
    kind: str,
    url: str,
    service_ids: list[str],
    out_path: str,
    *,
    preserve_existing: bool = True,
) -> list[dict]:
    """service_ids를 단건 exact 조회하고 flat row snapshot으로 저장한다.

    체크포인트는 ``.partial``에만 쓰고 모든 요청이 성공해야
    최종 snapshot을 교체한다.

    ``preserve_existing=True``는 실패 ID 전용 재시도 경로에서만
    사용한다. 전체 페이지 수집을 이어받는 옵션이 아니다.
    """
    _require_key()

    failed_path = out_path.replace(".json", "_failed_ids.json")
    requested_ids = _validate_service_ids(service_ids, label=kind)

    existing_by_id: dict[str, dict] = {}
    ids_to_fetch = list(requested_ids)
    if preserve_existing and Path(out_path).exists():
        existing = load(out_path)
        if not isinstance(existing, list):
            raise Gov24RequestError(f"{kind} existing snapshot must be a list")
        for item in existing:
            row = _canonical_stored_row(item)
            sid = _service_id_of(row)
            if row is None or sid is None or sid in existing_by_id:
                raise Gov24RequestError(
                    f"{kind} existing snapshot has an invalid or duplicate service ID"
                )
            existing_by_id[sid] = row
        previously_failed = (
            set(
                _validate_service_ids(
                    load(failed_path), label=f"{kind} failed_ids"
                )
            )
            if Path(failed_path).exists()
            else set()
        )
        ids_to_fetch = [
            sid
            for sid in requested_ids
            if sid not in existing_by_id or sid in previously_failed
        ]
        skipped = len(requested_ids) - len(ids_to_fetch)
        if skipped:
            log(f"[{kind}] 이미 성공한 {skipped}건은 재호출하지 않고 재사용합니다.")

    if not ids_to_fetch:
        log(f"[{kind}] 재호출할 서비스ID가 없습니다. 기존 {len(existing_by_id)}건을 그대로 사용합니다.")
        return list(existing_by_id.values())

    new_results: dict[str, dict] = {}
    failed_ids: list[str] = []
    total = len(ids_to_fetch)
    done = 0
    start = time.time()
    partial_path = Path(f"{out_path}.partial")
    partial_path.unlink(missing_ok=True)

    def _flush() -> dict[str, dict]:
        merged = dict(existing_by_id)
        merged.update(new_results)
        save(list(merged.values()), str(partial_path))
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
                returned_id = _service_id_of(data)
                if returned_id != service_id or data is None:
                    failed_ids.append(service_id)
                    log(f"[{kind}] 응답 식별자 계약 불일치로 실패 처리했습니다.")
                else:
                    new_results[service_id] = data

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
                log(
                    f"[{kind}] partial 체크포인트 "
                    f"(누적 {len(merged)}건) -> {partial_path}"
                )

    merged = _flush()
    if failed_ids:
        save(ids_to_fetch, failed_path)
        partial_path.unlink(missing_ok=True)
        raise Gov24RequestError(
            f"{kind} failed for {len(failed_ids)} service IDs; snapshot was preserved"
        )

    expected_result_ids = set(existing_by_id) | set(ids_to_fetch)
    if set(merged) != expected_result_ids:
        partial_path.unlink(missing_ok=True)
        raise Gov24RequestError(f"{kind} result IDs do not match requested IDs")

    os.replace(partial_path, out_path)
    save([], failed_path)
    log(
        f"[{kind}] 최종 저장 완료: {out_path} "
        f"(전체 {len(merged)}건, 이번 실행 성공 {len(new_results)}건)"
    )

    return list(merged.values())


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def _service_ids_from_list_file() -> list[str]:
    items = load(LIST_OUT)
    if not isinstance(items, list):
        raise Gov24RequestError("serviceList snapshot must be a list")
    service_ids = [_service_id_of(item) for item in items]
    if any(service_id is None for service_id in service_ids):
        raise Gov24RequestError("serviceList contains an invalid service ID")
    return list(_validate_service_ids(service_ids, label="serviceList"))


def run_list() -> None:
    save(fetch_list_all(), LIST_OUT)


def run_detail(limit: int | None = None) -> None:
    ids = _service_ids_from_list_file()
    if limit:
        ids = ids[:limit]
        log(f"[detail] 테스트 모드: 앞에서 {limit}건만 조회")
        fetch_many("detail", DETAIL_URL, ids, DETAIL_OUT, preserve_existing=False)
        return
    fetch_dataset_all(
        "detail",
        DETAIL_URL,
        DETAIL_OUT,
        expected_service_ids=ids,
    )


def run_conditions(limit: int | None = None) -> None:
    ids = _service_ids_from_list_file()
    if limit:
        ids = ids[:limit]
        log(f"[conditions] 테스트 모드: 앞에서 {limit}건만 조회")
        fetch_many(
            "conditions",
            CONDITIONS_URL,
            ids,
            CONDITIONS_OUT,
            preserve_existing=False,
        )
        return
    fetch_dataset_all(
        "conditions",
        CONDITIONS_URL,
        CONDITIONS_OUT,
        expected_service_ids=ids,
    )


def _load_failed_ids(out_path: str) -> list[str]:
    """out_path에 대응하는 ``_failed_ids.json``에서 실패 서비스ID 목록을 읽는다.

    파일이 없거나 비어 있으면 빈 리스트를 반환한다.
    """
    failed_path = out_path.replace(".json", "_failed_ids.json")
    if not Path(failed_path).exists():
        return []
    return load(failed_path)


def run_conditions_retry_failed() -> None:
    """전체 목록이 아니라, 이전에 실패한 서비스ID만 다시 조회해서 기존 결과에 병합한다.

    conditions_failed_ids.json에 있는 ID만 대상으로 하기 때문에 전체
    10000여 건을 다시 훑지 않고 실제로 실패했던 몇 건만 빠르게 재시도할 때
    쓴다. fetch_many가 결과를 서비스ID로 병합해서 저장하므로, 기존
    CONDITIONS_OUT 내용은 그대로 유지된 채 성공한 재시도분만 추가된다.
    """
    ids = _load_failed_ids(CONDITIONS_OUT)
    if not ids:
        log("[conditions] 재시도할 실패 ID가 없습니다 (failed_ids 파일이 없거나 비어 있음).")
        return
    log(f"[conditions] failed_ids 기준으로 {len(ids)}건만 재시도합니다.")
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
    elif step == "conditions-retry-failed":
        run_conditions_retry_failed()
    elif step == "all":
        run_list()
        run_detail(limit)
        run_conditions(limit)
    else:
        log(
            "사용법: python -m rag_chatbot.collectors.gov_24.gov24 "
            "[list|detail|conditions|conditions-retry-failed|all] [테스트건수]"
        )
