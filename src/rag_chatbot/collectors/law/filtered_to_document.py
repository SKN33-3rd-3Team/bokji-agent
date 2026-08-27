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
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from rag_design.contracts import (
    Document,
    SensitiveDataStatus,
    Section,
    SourceType,
    compute_content_hash,
    compute_document_id,
)

KST = ZoneInfo("Asia/Seoul")

LICENSE = "국가법령정보센터 공개 법령"
SOURCE_NAME = "국가법령정보센터"
SOURCE_DATASET_URL = "https://open.law.go.kr/LSO/main.do"

# 원본 레코드에서 title/source_id/시행일자 등을 뽑을 때 쓰는 필드 이름
# (target별로 다르다).
FIELD_MAP = {
    "law": {"name": "법령명한글", "id": "법령ID", "seq": "법령일련번호"},
    "admrul": {"name": "행정규칙명", "id": "행정규칙ID", "seq": "행정규칙일련번호"},
    "ordin": {"name": "자치법규명", "id": "자치법규ID", "seq": "자치법규일련번호"},
}


_OC_QUERY_PATTERN = re.compile(r"([?&])OC=[^&]*")


def _scrub_oc(value):
    """법령상세링크 등에 API 인증키(OC=내값)가 그대로 박혀 나온다.

    저장소에 올릴 데이터에 개인 인증키가 남으면 안 되니(PROJECT_COMPLIANCE.md
    보안 규칙), 문자열 값에서 "OC=..." 쿼리 부분만 지운다.
    """

    if isinstance(value, str) and "OC=" in value:
        return _OC_QUERY_PATTERN.sub(r"\1OC=REDACTED", value)
    return value


def _iso_date(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return None
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _build_content(record: dict, target: str, title: str) -> str:
    """조문 원문이 없으니, 목록 API로 알 수 있는 정보를 정리한 텍스트를 만든다."""

    lines = [f"제목: {title}"]
    label_map = {
        "law": [
            ("법령구분명", "법령구분"),
            ("소관부처명", "소관부처"),
            ("제개정구분명", "제개정구분"),
            ("공포번호", "공포번호"),
            ("공포일자", "공포일자"),
            ("시행일자", "시행일자"),
            ("법령약칭명", "약칭"),
        ],
        "admrul": [
            ("행정규칙종류", "행정규칙종류"),
            ("소관부처명", "소관부처"),
            ("담당부서기관명", "담당부서"),
            ("제개정구분명", "제개정구분"),
            ("발령번호", "발령번호"),
            ("발령일자", "발령일자"),
            ("시행일자", "시행일자"),
        ],
        "ordin": [
            ("지자체기관명", "지자체"),
            ("자치법규종류", "자치법규종류"),
            ("제개정정보", "제개정구분"),
            ("공포번호", "공포번호"),
            ("공포일자", "공포일자"),
            ("시행일자", "시행일자"),
        ],
    }
    for key, label in label_map.get(target, []):
        value = record.get(key)
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def build_document(record: dict) -> Document | None:
    target = record.get("_target")
    fields = FIELD_MAP.get(target)
    if not fields:
        return None

    title = record.get(fields["name"])
    source_id = record.get(fields["id"]) or record.get(fields["seq"])
    if not title or not source_id:
        return None

    eff = _iso_date(record.get("시행일자"))
    seq = record.get(fields["seq"]) or source_id

    if target == "law":
        source_url = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={seq}" + (
            f"&efYd={record.get('시행일자')}" if record.get("시행일자") else ""
        )
    else:
        # TODO(RAG설계팀 확인 필요): admrul/ordin 전용 안전 쿼리 파라미터가
        # url_safety.py에 아직 없어서 도메인 루트로 대체.
        source_url = "https://www.law.go.kr/"

    content = _build_content(record, target, title)
    content_hash = compute_content_hash(content)
    collected_at = datetime.now(KST).isoformat()

    doc_id = compute_document_id(
        source_type=SourceType.LAW,
        source_id=str(source_id),
        source_updated_at=None,
        effective_from=eff,
        content_hash=content_hash,
    )

    # 목록 API가 준 필드를 하나도 안 버리고 전부 metadata에 담는다.
    metadata = {
        k: _scrub_oc(v) for k, v in record.items() if not k.startswith("_")
    }
    metadata["law_type"] = target
    metadata["matched_keywords"] = record.get("_matched_keywords", [])

    parse_warnings = (
        "본문(조문) 미포함 - 목록조회 API 정보만으로 구성됨 (본문 API 미사용 정책)",
    )
    if target != "law":
        parse_warnings += (
            "공식 상세페이지 URL 미확보 - law.go.kr 도메인 루트로 대체",
        )

    return Document(
        schema_version="1.0",
        doc_id=doc_id,
        source_type=SourceType.LAW,
        source_name=SOURCE_NAME,
        source_id=str(source_id),
        source_url=source_url,
        title=title,
        content=content,
        sections=(Section(heading_path=("기본정보",), content=content, metadata={}),),
        collected_at=collected_at,
        source_updated_at=None,
        effective_from=eff,
        effective_to=None,
        license=LICENSE,
        content_hash=content_hash,
        metadata=metadata,
        parse_warnings=parse_warnings,
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
            "document_count": len(documents),
            "collected_at": datetime.now(KST).isoformat(),
            "source_dataset_url": SOURCE_DATASET_URL,
            "license": LICENSE,
            "excluded_count": len(warnings),
            "parse_error_count": sum(1 for w in warnings if "변환 실패" in w),
        },
        "document_card": {
            "source_type": "law",
            "source_name": SOURCE_NAME,
            "source_dataset_url": SOURCE_DATASET_URL,
            "license": LICENSE,
            "document_count": len(documents),
            "collection_scope": (
                "복지 도메인 키워드로 로컬 필터링한 법령·행정규칙·자치법규 "
                "목록조회(lawSearch.do) 정보 - 본문(lawService.do) API는 사용하지 않음"
            ),
            "cleaning_method": "목록 API 원본 필드 보존, metadata에 전부 포함",
            "chunking_method": "본문 없음 - 문서당 단일 섹션(기본정보)",
            "exclusion_criteria": ["법령명 또는 ID 누락"],
            "update_policy": "시행일별 버전 보존",
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
