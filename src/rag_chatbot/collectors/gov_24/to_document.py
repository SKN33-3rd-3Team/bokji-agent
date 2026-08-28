"""gov24_merged.json을 RAG 설계팀과 합의된 Document 스키마(JSONL)로 변환한다.

rag_design.contracts.Document/Section 규격(schema_version 1.0)에 맞춰
직접 dict를 만든다 (아직 병합 전인 feat/1-rag-design 브랜치 코드를 의존성으로
끌어오지 않기 위해, 검증 규칙만 그대로 재구현했다). 따라서 실제 `Document`
dataclass로의 변환·검증(`rag_design.validation.validate_collection_handoff`는
dict가 아니라 Document 인스턴스를 요구한다)은 두 브랜치가 합쳐진 뒤 별도로
수행해야 한다 — 이 스크립트가 만드는 JSONL은 아직 그 검증을 통과시켜본 적이
없다.

출력:
    data/processed/subsidy_documents.jsonl        전체 (재생성 가능, git 미포함)
    data/processed/subsidy_manifest.json           매니페스트 (재생성 가능, git 미포함)
    data/processed/subsidy_parse_warnings.json     전체 경고 로그 (재생성 가능, git 미포함)
    data/samples/subsidy_documents_sample.jsonl    샘플 5건 (git 포함, 형식 확인용)

사용법:
    python -m rag_chatbot.collectors.gov_24.to_document
"""

import hashlib
import json
import os
import unicodedata
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from rag_design.citation import sanitize_public_url

from .region_utils import extract_region, load_sigungu_code_table

load_dotenv()

# main_gov24.py를 어느 위치(gov_24/ 안, 리포지토리 루트 등)에서 실행하든
# 항상 같은 data/ 폴더에 저장/조회하도록, 이 파일 위치를 기준으로 프로젝트
# 루트를 고정 경로로 잡는다(현재 작업 디렉터리(cwd)에 의존하지 않음).
PROJECT_ROOT = Path(__file__).resolve().parents[4]
MERGED_PATH = str(PROJECT_ROOT / "data" / "raw" / "gov24_merged.json")
DETAIL_FAILED_PATH = str(PROJECT_ROOT / "data" / "raw" / "gov24_service_detail_failed_ids.json")
CONDITIONS_FAILED_PATH = str(PROJECT_ROOT / "data" / "raw" / "gov24_support_conditions_failed_ids.json")
OUT_JSONL = str(PROJECT_ROOT / "data" / "processed" / "subsidy_documents.jsonl")
OUT_MANIFEST = str(PROJECT_ROOT / "data" / "processed" / "subsidy_manifest.json")
OUT_PARSE_WARNINGS = str(PROJECT_ROOT / "data" / "processed" / "subsidy_parse_warnings.json")
SAMPLE_OUT = str(PROJECT_ROOT / "data" / "samples" / "subsidy_documents_sample.jsonl")
SAMPLE_SIZE = 5

# data.go.kr "전국 법정동코드 전체자료" CSV 경로(선택). 지정하면 시군구 5자리
# 코드까지 채워지고, 없으면 시도 코드까지만 채워진다(부정확한 코드를
# 하드코딩하지 않기 위한 설계 — region_utils.py 참고).
SIGUNGU_CODE_CSV = os.getenv("SIGUNGU_CODE_CSV")
GOV24_SERVICE_KEY = os.getenv("GOV24_SERVICE_KEY", "")

SOURCE_DATASET_URL = "https://www.data.go.kr/data/15113968/openapi.do"
LICENSE = "공공데이터포털 이용조건 확인"

# (JSON 필드명, 섹션 제목, section_type 태그) — 전부 serviceDetail 응답 소속.
SECTION_FIELDS = [
    ("서비스목적", "목적", "purpose"),
    ("지원대상", "지원대상", "support_target"),
    ("선정기준", "선정기준", "eligibility_criteria"),
    ("지원내용", "지원내용", "support_details"),
    ("신청방법", "신청방법", "application_method"),
    ("신청기한", "신청기한", "application_period"),
]

LEGAL_BASIS_FIELDS = ("법령", "행정규칙", "자치법규")


class FieldStatus(str, Enum):
    """필드 하나(또는 근거법령처럼 여러 필드를 묶은 그룹)의 상태.

    - PRESENT: 값이 있음 (근거법령 그룹은 법령/행정규칙/자치법규 중 1개만
      있어도 PRESENT로 본다 — 셋 다 있어야 하는 게 정상이 아니기 때문).
    - MISSING_SOURCE: 조회는 성공했는데 원천 자체에 값이 없는 "진짜 결측".
    - FETCH_FAILED: serviceDetail/supportConditions API 호출 자체가 실패해서
      값이 있었는지 없었는지 알 수 없는 경우. MISSING_SOURCE와 반드시
      구분한다 — 재수집하면 채워질 수 있는 값이기 때문이다.
    """

    PRESENT = "present"
    MISSING_SOURCE = "missing_source"
    FETCH_FAILED = "fetch_failed"


def _canonical_public_source_url(url: object) -> str | None:
    """공통 URL 정책을 통과한 공개 canonical URL만 반환한다."""
    if not isinstance(url, str):
        return None
    try:
        return sanitize_public_url(url, secret_values=(GOV24_SERVICE_KEY,))
    except (TypeError, ValueError):
        return None


def _compute_content_hash(content: str) -> str:
    canonical = unicodedata.normalize("NFC", content).replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_updated_at(value: str | None) -> str | None:
    """보조금24 응답의 '수정일시'는 레코드마다 형식이 다르다.

    - "YYYY-MM-DD" 형태는 그대로 둔다.
    - "YYYYMMDDHHMMSS"(14자리 숫자) 형태는 ISO 8601로 바꾼다.
    - 둘 다 아니면 None(형식 불명)으로 처리한다.
    """
    if not value:
        return None
    value = value.strip()
    if len(value) == 14 and value.isdigit():
        try:
            # 시:분:초가 있는 값은 타임존이 꼭 있어야 한다 — 공공데이터포털 기준 KST(+09:00)로 명시
            return datetime.strptime(value, "%Y%m%d%H%M%S").isoformat() + "+09:00"
        except ValueError:
            return None
    try:
        datetime.fromisoformat(value)
        return value
    except ValueError:
        return None


def _clean_text(text: str) -> str:
    """공백/개행 정규화. 빈 줄과 앞뒤 공백만 정리하고 내용은 손대지 않는다."""
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _build_sections(item: dict) -> list[dict]:
    sections = []
    for field, heading, section_type in SECTION_FIELDS:
        value = item.get(field)
        if value:
            sections.append(
                {
                    "heading_path": [heading],
                    "content": _clean_text(value),
                    "metadata": {"section_type": section_type},
                }
            )

    basis = [item.get(k) for k in LEGAL_BASIS_FIELDS if item.get(k)]
    if basis:
        sections.append(
            {
                "heading_path": ["근거법령"],
                "content": " / ".join(basis),
                "metadata": {"section_type": "legal_basis"},
            }
        )
    return sections


def _load_failed_ids(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text(encoding="utf-8")))


def _field_status(has_value: bool, service_id: str, failed_ids: set[str]) -> str:
    if service_id in failed_ids:
        return FieldStatus.FETCH_FAILED.value
    return FieldStatus.PRESENT.value if has_value else FieldStatus.MISSING_SOURCE.value


def build_field_statuses(
    item: dict,
    service_id: str,
    detail_failed_ids: set[str],
    conditions_failed_ids: set[str],
) -> dict[str, str]:
    """섹션 필드·근거법령 그룹·지원조건(JA코드) 필드의 상태를 계산한다.

    근거법령(법령/행정규칙/자치법규)은 셋 중 하나만 있어도 정상이므로 OR
    조건으로 판단하고, 개별 필드 단위로는 결측 경고를 만들지 않는다.
    """
    statuses: dict[str, str] = {}

    for field, _, section_type in SECTION_FIELDS:
        statuses[section_type] = _field_status(bool(item.get(field)), service_id, detail_failed_ids)

    if service_id in detail_failed_ids:
        statuses["legal_basis"] = FieldStatus.FETCH_FAILED.value
    elif any(item.get(k) for k in LEGAL_BASIS_FIELDS):
        statuses["legal_basis"] = FieldStatus.PRESENT.value
    else:
        statuses["legal_basis"] = FieldStatus.MISSING_SOURCE.value

    has_conditions = bool(item.get("JA0110")) or bool(item.get("JA0111"))
    statuses["support_conditions"] = _field_status(has_conditions, service_id, conditions_failed_ids)

    return statuses


def convert_one(
    item: dict,
    collected_at: str,
    warnings_out: list[str],
    detail_failed_ids: set[str],
    conditions_failed_ids: set[str],
    sigungu_code_table: dict,
) -> dict | None:
    service_id = item.get("서비스ID")
    if not service_id:
        warnings_out.append("서비스ID 없음, 제외")
        return None

    raw_source_url = item.get("상세조회URL")
    source_url = _canonical_public_source_url(raw_source_url)
    if source_url is None:
        warnings_out.append(f"{service_id}: 공개 가능한 공식 상세 URL이 없어 제외")
        return None
    if source_url != raw_source_url.strip():
        warnings_out.append(
            f"{service_id}: 상세 URL을 공개 가능한 canonical HTTPS URL로 정규화"
        )

    sections = _build_sections(item)
    if not sections:
        warnings_out.append(f"{service_id}: 본문 없음, 제외")
        return None

    content = "\n".join(f"{s['heading_path'][-1]}\n{s['content']}" for s in sections)
    content_hash = _compute_content_hash(content)
    source_updated_at = _normalize_updated_at(item.get("수정일시"))
    if item.get("수정일시") and source_updated_at is None:
        warnings_out.append(f"{service_id}: 수정일시 형식 인식 불가({item.get('수정일시')!r}), None 처리")
    version = source_updated_at or content_hash[:16]
    doc_id = f"subsidy:{service_id}:{version}"

    field_statuses = build_field_statuses(item, service_id, detail_failed_ids, conditions_failed_ids)

    # 결측이 "원천 결측"인지 "조회 실패로 못 가져온 것"인지 구분해서 문서별로 기록한다.
    doc_parse_warnings: list[str] = []
    if service_id in detail_failed_ids:
        doc_parse_warnings.append(
            "상세조회(serviceDetail) API 호출 실패 — 지원내용/법령 등 상세 필드가 원천 결측이 "
            "아니라 조회 실패로 누락됐을 수 있음"
        )
    if service_id in conditions_failed_ids:
        doc_parse_warnings.append(
            "지원조건조회(supportConditions) API 호출 실패 — JA코드(연령·소득 등 조건)가 원천 "
            "결측이 아니라 조회 실패로 누락됐을 수 있음"
        )

    region = extract_region(item.get("소관기관명"), sigungu_code_table)
    if region["region_scope"] == "unknown":
        doc_parse_warnings.append(
            "소관기관명에서 지역 범위를 확정하지 못해 region_scope=unknown으로 보존"
        )

    return {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "source_type": "subsidy",
        "source_name": "대한민국 공공서비스(혜택) 정보",
        "source_id": service_id,
        "source_url": source_url,
        "title": item.get("서비스명") or "",
        "content": content,
        "sections": sections,
        "collected_at": collected_at,
        "source_updated_at": source_updated_at,
        # effective_from/effective_to는 rag_design.contracts.Document에서 ISO-8601
        # 날짜 문자열만 허용한다(date.fromisoformat으로 검증). 실제 "신청기한" 값은
        # "상시신청", "주택도시기금 주거안정 월세대출 규정에 따름", "공고에 따름"처럼
        # 대부분 날짜가 아닌 서술형 텍스트라서 여기 억지로 넣으면 검증에서 깨진다.
        # 원문 값은 버리지 않고 metadata["application_deadline_raw"]와 "신청기한"
        # 섹션(sections)에 그대로 보존한다.
        "effective_from": None,
        "effective_to": None,
        "license": LICENSE,
        "content_hash": content_hash,
        "metadata": {
            "organization": item.get("소관기관명") or "",
            # serviceDetail API의 "신청기한" 원문 값을 가공 없이 그대로 저장.
            "application_deadline_raw": item.get("신청기한") or None,
            "region_scope": region["region_scope"],
            "region_names": region["region_names"],
            "region_sido": region["sido"],
            "region_sigungu": region["sigungu"],
            "region_sido_code": region["sido_code"],
            "region_sigungu_code": region["sigungu_code"],
            "service_category": item.get("서비스분야") or "",
            "public_detail_url": source_url,
            "age_start": item.get("JA0110") if isinstance(item.get("JA0110"), int) else None,
            "age_end": item.get("JA0111") if isinstance(item.get("JA0111"), int) else None,
            "field_status": field_statuses,
        },
        "parse_warnings": doc_parse_warnings,
        "sensitive_data_status": "clear",
    }


def annotate_cross_service_duplicates(documents: list[dict]) -> int:
    """같은 content_hash를 가진 서로 다른 서비스ID를 찾아 metadata에 표시만 한다.

    삭제·제외는 하지 않는다 — 서비스ID가 다르면 실제로는 서로 다른 제도일 수
    있기 때문이다. 반환값은 중복이 표시된 문서 수(집계용).
    """
    by_hash: dict[str, list[str]] = defaultdict(list)
    for doc in documents:
        by_hash[doc["content_hash"]].append(doc["source_id"])

    flagged = 0
    for doc in documents:
        siblings = sorted({sid for sid in by_hash[doc["content_hash"]] if sid != doc["source_id"]})
        if siblings:
            doc["metadata"]["duplicate_content_of_source_ids"] = siblings
            doc["parse_warnings"] = list(doc["parse_warnings"]) + [
                f"서로 다른 서비스ID({', '.join(siblings)})와 본문 내용 동일 — 삭제하지 않고 보존"
            ]
            flagged += 1
    return flagged


def run() -> None:
    merged_path = Path(MERGED_PATH)
    items = json.loads(merged_path.read_text(encoding="utf-8"))
    collected_at = datetime.fromtimestamp(merged_path.stat().st_mtime).astimezone().isoformat()

    detail_failed_ids = _load_failed_ids(DETAIL_FAILED_PATH)
    conditions_failed_ids = _load_failed_ids(CONDITIONS_FAILED_PATH)
    sigungu_code_table = load_sigungu_code_table(SIGUNGU_CODE_CSV)

    documents = []
    all_warnings: list[str] = []

    for item in items:
        warnings: list[str] = []
        doc = convert_one(
            item,
            collected_at,
            warnings,
            detail_failed_ids,
            conditions_failed_ids,
            sigungu_code_table,
        )
        all_warnings.extend(warnings)
        if doc is not None:
            documents.append(doc)

    duplicate_count = annotate_cross_service_duplicates(documents)

    excluded = len(items) - len(documents)

    Path(OUT_JSONL).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    Path(OUT_PARSE_WARNINGS).write_text(
        json.dumps(all_warnings, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "manifest": {
            "schema_version": "1.0",
            "source_type": "subsidy",
            "document_count": len(documents),
            "collected_at": collected_at,
            "source_dataset_url": SOURCE_DATASET_URL,
            "license": LICENSE,
            "excluded_count": excluded,
            "parse_error_count": len(all_warnings),
        },
        "document_card": {
            "source_type": "subsidy",
            "source_name": "대한민국 공공서비스(혜택) 정보",
            "source_dataset_url": SOURCE_DATASET_URL,
            "license": LICENSE,
            "document_count": len(documents),
            "collection_scope": "보조금24 공개 서비스 전체 목록(serviceList/serviceDetail/supportConditions 병합)",
            "cleaning_method": "공백/개행 정규화, 빈 섹션 제거",
            "chunking_method": "서비스 섹션(지원대상/선정기준/지원내용/신청방법/신청기한/근거법령) 경계 우선",
            "exclusion_criteria": ["서비스ID 없음", "공식 도메인(gov.kr 등) URL 아님", "본문 없음"],
            "update_policy": "source_updated_at 기준 버전 보존",
            "region_extraction": (
                "소관기관명 텍스트에서 시도/시군구를 정규식으로 추출(region_utils.py). "
                "명시적으로 확인한 중앙기관은 national/['전국'], 시도/시군구는 "
                "regional과 계층형 region_names로 기록한다. 확정할 수 없으면 unknown/[]"
            ),
            "missing_vs_failed": (
                "상세조회/지원조건조회 API 호출이 실패한 서비스ID는 실패 목록"
                "(gov24_*_failed_ids.json)으로 별도 기록하고, 해당 문서의 metadata.field_status와 "
                "parse_warnings에 '조회 실패로 누락'임을 명시해 원천 결측(missing_source)과 구분한다"
            ),
            "duplicate_policy": (
                f"서로 다른 서비스ID의 동일 본문 {duplicate_count}건을 "
                "metadata.duplicate_content_of_source_ids로 표시했고, 삭제하지 않고 그대로 보존한다"
            ),
            "parse_warnings_log": OUT_PARSE_WARNINGS,
            "rights_reviewed": True,
            "sensitive_data_reviewed": True,
        },
    }
    Path(OUT_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    Path(SAMPLE_OUT).parent.mkdir(parents=True, exist_ok=True)
    with open(SAMPLE_OUT, "w", encoding="utf-8") as f:
        for doc in documents[:SAMPLE_SIZE]:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    print(f"변환 완료: {len(documents)}건 (제외 {excluded}건, 중복 표시 {duplicate_count}건)", flush=True)
    print(f"전체: {OUT_JSONL}", flush=True)
    print(f"매니페스트: {OUT_MANIFEST}", flush=True)
    print(f"경고 로그: {OUT_PARSE_WARNINGS} ({len(all_warnings)}건)", flush=True)
    print(f"샘플({SAMPLE_SIZE}건): {SAMPLE_OUT}", flush=True)


if __name__ == "__main__":
    run()
