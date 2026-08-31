"""filter_index.py가 만든 law_filtered.jsonl(목록조회 API 원본 필드 그대로)을
길환님 subsidy_documents.jsonl과 같은 rag_design.contracts.Document 스키마로
변환한다.

본문(lawService.do) API를 안 쓰기로 했기 때문에, content/sections은 조문
원문이 아니라 "목록조회 API로 알 수 있는 정보"(법령명, 소관부처, 시행일자,
공포일자, 법령구분 등)를 정리한 텍스트다. 원본 API가 준 필드는 하나도
버리지 않고 전부 metadata에 담는다.

사용법:
    PYTHONPATH=src python -m rag_chatbot.collectors.law.filtered_to_document \
        data/processed/law_filtered.jsonl \
        data/processed/law_documents.jsonl \
        data/processed/law_manifest.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from rag_design.citation import legal_citation_url
from rag_design.contracts import (
    LEGAL_CONTENT_LEVEL,
    LEGAL_SECTION_HEADING,
    LEGAL_SECTION_TYPE,
    Document,
    SensitiveDataStatus,
    Section,
    SourceType,
    compute_content_hash,
    compute_document_id,
    render_legal_metadata_summary,
)

KST = ZoneInfo("Asia/Seoul")

LICENSE = "국가법령정보센터 공개 법령"
SOURCE_NAME = "국가법령정보센터"
SOURCE_DATASET_URL = "https://open.law.go.kr/LSO/main.do"

# 원본 레코드에서 title/source_id/시행일자 등을 뽑을 때 쓰는 필드 이름
# (target별로 다르다).
FIELD_MAP = {
    "law": {
        "name": "법령명한글",
        "id": "법령ID",
        "seq": "법령일련번호",
        "kind": "법령구분명",
        "organization": "소관부처명",
        "revision": "제개정구분명",
        "issued": "공포일자",
    },
    "admrul": {
        "name": "행정규칙명",
        "id": "행정규칙ID",
        "seq": "행정규칙일련번호",
        "kind": "행정규칙종류",
        "organization": "소관부처명",
        "revision": "제개정구분명",
        "issued": "발령일자",
    },
    "ordin": {
        "name": "자치법규명",
        "id": "자치법규ID",
        "seq": "자치법규일련번호",
        "kind": "자치법규종류",
        "organization": "지자체기관명",
        "revision": "제개정구분명",
        "issued": "공포일자",
    },
}


def _scrub_oc(value):
    """법령상세링크 등에 API 인증키(OC=내값)가 그대로 박혀 나온다.

    저장소에 올릴 데이터에 개인 인증키가 남으면 안 되니(PROJECT_COMPLIANCE.md
    보안 규칙), 문자열 값에서 "OC=..." 쿼리 부분만 지운다.
    """

    if not isinstance(value, str):
        return value
    parts = urlsplit(value)
    if not parts.query:
        return value
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(name.casefold() == "oc" for name, _ in pairs):
        return value
    query = [
        (name, item)
        for name, item in pairs
        if name.casefold() != "oc"
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _iso_date(yyyymmdd: str | None) -> str | None:
    value = str(yyyymmdd) if yyyymmdd is not None else ""
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        return None
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def build_document(record: dict) -> Document | None:
    target = record.get("_target")
    fields = FIELD_MAP.get(target)
    if not fields:
        return None

    title = record.get(fields["name"])
    source_id = record.get(fields["id"])
    source_sequence = record.get(fields["seq"])
    if not title or not source_id or not source_sequence:
        return None

    source_id = str(source_id)
    source_sequence = str(source_sequence)
    effective_from = _iso_date(record.get("시행일자"))
    issued_date = _iso_date(record.get(fields["issued"]))

    # 목록 API가 준 필드를 하나도 안 버리고 전부 metadata에 담는다.
    metadata = {
        k: _scrub_oc(v) for k, v in record.items() if not k.startswith("_")
    }
    metadata.update(
        {
            "content_level": LEGAL_CONTENT_LEVEL,
            "law_type": target,
            "law_name": title,
            "source_sequence": source_sequence,
            "organization": record.get(fields["organization"]),
            "document_kind": record.get(fields["kind"]),
            "issued_date": issued_date,
            "effective_date": effective_from,
            "revision_type": record.get(fields["revision"]),
            "matched_keywords": record.get("_matched_keywords", []),
        }
    )
    content = render_legal_metadata_summary(metadata)
    content_hash = compute_content_hash(content)
    collected_at = datetime.now(KST).isoformat()
    source_url = legal_citation_url(
        law_type=target,
        source_sequence=source_sequence,
        effective_from=effective_from,
    )
    doc_id = compute_document_id(
        source_type=SourceType.LAW,
        source_id=source_id,
        source_updated_at=None,
        effective_from=effective_from,
        content_hash=content_hash,
        law_type=target,
        source_sequence=source_sequence,
    )

    return Document(
        schema_version="1.0",
        doc_id=doc_id,
        source_type=SourceType.LAW,
        source_name=SOURCE_NAME,
        source_id=source_id,
        source_url=source_url,
        title=title,
        content=content,
        sections=(
            Section(
                heading_path=LEGAL_SECTION_HEADING,
                content=content,
                metadata={"section_type": LEGAL_SECTION_TYPE},
            ),
        ),
        collected_at=collected_at,
        source_updated_at=None,
        effective_from=effective_from,
        effective_to=None,
        license=LICENSE,
        content_hash=content_hash,
        metadata=metadata,
        parse_warnings=(),
        sensitive_data_status=SensitiveDataStatus.CLEAR,
    )


def convert_all(src_path: Path) -> tuple[list[Document], list[str]]:
    documents: list[Document] = []
    warnings: list[str] = []

    with src_path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            try:
                doc = build_document(record)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{lineno}번째 줄: 변환 실패 - {exc}")
                continue
            if doc is None:
                warnings.append(f"{lineno}번째 줄: 필수 필드 없음 - 제외")
                continue
            documents.append(doc)

    return documents, warnings


def write_outputs(
    documents: list[Document], warnings: list[str], out_jsonl: Path, out_manifest: Path
) -> None:
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc.to_dict(), ensure_ascii=False) + "\n")

    manifest = {
        "manifest": {
            "schema_version": "1.0",
            "source_type": "law",
            "content_level": LEGAL_CONTENT_LEVEL,
            "document_count": len(documents),
            "collected_at": datetime.now(KST).isoformat(),
            "source_dataset_url": SOURCE_DATASET_URL,
            "license": LICENSE,
            "excluded_count": len(warnings),
            "parse_error_count": sum(1 for w in warnings if "변환 실패" in w),
        },
        "document_card": {
            "source_type": "law",
            "content_level": LEGAL_CONTENT_LEVEL,
            "source_name": SOURCE_NAME,
            "source_dataset_url": SOURCE_DATASET_URL,
            "license": LICENSE,
            "document_count": len(documents),
            "collection_scope": (
                "복지 도메인 키워드로 로컬 필터링한 법령·행정규칙·자치법규 "
                "목록조회 메타데이터 - 본문은 수집·색인하지 않음"
            ),
            "unsupported_scope": [
                "법령·행정규칙·자치법규 조문 본문 검색·인용",
                "법적 정의·자격·배제·금지 판단 및 법률 해석",
            ],
            "official_source_guidance": (
                "조문 본문이나 법률 해석이 필요하면 각 문서 source_url의 "
                "국가법령정보센터 공식 상세 페이지에서 확인"
            ),
            "version_identity": (
                "law_type + source_id + source_sequence + effective_from"
            ),
            "cleaning_method": "목록조회 필드를 공통 법령 메타데이터 계약으로 정규화",
            "chunking_method": (
                "문서당 기본정보 섹션 1개, content_level=metadata_only"
            ),
            "exclusion_criteria": ["필수 기본정보 누락", "직접 공개 상세 URL 없음"],
            "update_policy": (
                "law_type·source_id·source_sequence·effective_from 조합으로 "
                "동일 시행일 개정을 구분해 버전 보존"
            ),
            "rights_reviewed": True,
            "sensitive_data_reviewed": True,
        },
    }
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    with out_manifest.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(
        f"[filtered_to_document] 완료: {len(documents)}건 -> {out_jsonl} "
        f"(제외/경고 {len(warnings)}건)",
        file=sys.stderr,
    )
    for w in warnings[:10]:
        print(f"  - {w}", file=sys.stderr)


def main() -> None:
    if len(sys.argv) != 4:
        print(
            "usage: python -m rag_chatbot.collectors.law.filtered_to_document "
            "<law_filtered.jsonl> <out_jsonl> <out_manifest>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    src_path = Path(sys.argv[1])
    out_jsonl = Path(sys.argv[2])
    out_manifest = Path(sys.argv[3])

    documents, warnings = convert_all(src_path)
    write_outputs(documents, warnings, out_jsonl, out_manifest)


if __name__ == "__main__":
    main()
