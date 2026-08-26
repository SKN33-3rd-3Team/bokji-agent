"""gov24_merged.json을 RAG 설계팀과 합의된 Document 스키마(JSONL)로 변환한다.

rag_design.contracts.Document/Section 규격(schema_version 1.0)에 맞춰
직접 dict를 만든다 (아직 병합 전인 feat/1-rag-design 브랜치 코드를 의존성으로
끌어오지 않기 위해, 검증 규칙만 그대로 재구현했다).

출력:
    data/processed/subsidy_documents.jsonl        전체 (재생성 가능, git 미포함)
    data/processed/subsidy_manifest.json           매니페스트 (재생성 가능, git 미포함)
    data/samples/subsidy_documents_sample.jsonl    샘플 5건 (git 포함, 형식 확인용)

사용법:
    python -m rag_chatbot.collectors.to_document
"""

import hashlib
import json
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

MERGED_PATH = "data/raw/gov24_merged.json"
OUT_JSONL = "data/processed/subsidy_documents.jsonl"
OUT_MANIFEST = "data/processed/subsidy_manifest.json"
SAMPLE_OUT = "data/samples/subsidy_documents_sample.jsonl"
SAMPLE_SIZE = 5

SOURCE_DATASET_URL = "https://www.data.go.kr/data/15113968/openapi.do"
LICENSE = "공공데이터포털 이용조건 확인"

# rag_design.contracts.url_safety.OFFICIAL_DOMAINS 와 동일해야 한다.
OFFICIAL_DOMAINS = ("data.go.kr", "gov.kr", "law.go.kr")

# (JSON 필드명, 섹션 제목, section_type 태그)
SECTION_FIELDS = [
    ("서비스목적", "목적", "purpose"),
    ("지원대상", "지원대상", "support_target"),
    ("선정기준", "선정기준", "eligibility_criteria"),
    ("지원내용", "지원내용", "support_details"),
    ("신청방법", "신청방법", "application_method"),
    ("신청기한", "신청기한", "application_period"),
]


def _is_official_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_DOMAINS)


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

    basis = [item.get(k) for k in ("법령", "행정규칙", "자치법규") if item.get(k)]
    if basis:
        sections.append(
            {
                "heading_path": ["근거법령"],
                "content": " / ".join(basis),
                "metadata": {"section_type": "legal_basis"},
            }
        )
    return sections


def convert_one(item: dict, collected_at: str, warnings_out: list[str]) -> dict | None:
    service_id = item.get("서비스ID")
    if not service_id:
        warnings_out.append("서비스ID 없음, 제외")
        return None

    source_url = item.get("상세조회URL") or ""
    if not source_url or not _is_official_url(source_url):
        warnings_out.append(f"{service_id}: 공식 도메인 URL 아님({source_url!r}), 제외")
        return None

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
        "effective_from": None,
        "effective_to": None,
        "license": LICENSE,
        "content_hash": content_hash,
        "metadata": {
            "organization": item.get("소관기관명") or "",
            "region_codes": ["ALL"],
            "service_category": item.get("서비스분야") or "",
            "public_detail_url": source_url,
            "age_start": item.get("JA0110") if isinstance(item.get("JA0110"), int) else None,
            "age_end": item.get("JA0111") if isinstance(item.get("JA0111"), int) else None,
        },
        "parse_warnings": [],
        "sensitive_data_status": "clear",
    }


def run() -> None:
    merged_path = Path(MERGED_PATH)
    items = json.loads(merged_path.read_text(encoding="utf-8"))
    collected_at = datetime.fromtimestamp(merged_path.stat().st_mtime).astimezone().isoformat()

    documents = []
    all_warnings: list[str] = []

    for item in items:
        warnings: list[str] = []
        doc = convert_one(item, collected_at, warnings)
        all_warnings.extend(warnings)
        if doc is not None:
            documents.append(doc)

    excluded = len(items) - len(documents)

    Path(OUT_JSONL).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    manifest = {
        "manifest": {
            "schema_version": "1.0",
            "source_type": "subsidy",
            "document_count": len(documents),
            "collected_at": collected_at,
            "source_dataset_url": SOURCE_DATASET_URL,
            "license": LICENSE,
            "excluded_count": excluded,
            "parse_error_count": 0,
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

    print(f"변환 완료: {len(documents)}건 (제외 {excluded}건)", flush=True)
    print(f"전체: {OUT_JSONL}", flush=True)
    print(f"매니페스트: {OUT_MANIFEST}", flush=True)
    print(f"샘플({SAMPLE_SIZE}건): {SAMPLE_OUT}", flush=True)
    if all_warnings:
        print(f"경고 {len(all_warnings)}건, 앞 10개만 표시:", flush=True)
        for w in all_warnings[:10]:
            print(" -", w, flush=True)


if __name__ == "__main__":
    run()
