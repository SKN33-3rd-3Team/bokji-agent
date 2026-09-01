"""정책 원문의 "근거법령" 문자열을 active LAW vector index의 canonical
metadata와 이름으로 exact 매칭해서 {law_type, source_id}로 바꾼다.

N5(claim_plan.py)가 law_check_required=True인 claim에 required_law_sources를
채울 때 이걸 쓴다. N5는 정책 데이터만 보고 법령 데이터에 직접 접근하지
않으므로, 이 매칭 로직을 별도 인터페이스로 분리해서 주입받는다
(N7 리뷰 피드백 #3 반영).

주의: 지금 보조금24 원본 데이터의 "근거법령" 필드가 대부분 비어있는 버그가
있어서(수집 코드 파라미터 오류, 확인 후 팀에 보고함), 이 매칭기는 그
데이터가 채워진 정책에 대해서만 실제로 값을 만들어낸다. 데이터가 없으면
그냥 빈 리스트를 돌려준다 - 에러는 아니다.
"""

from __future__ import annotations

import re
from typing import Protocol

from rag_design.contracts import LegalDocumentType, SourceType
from rag_design.vector_store import ChromaVectorStore

from ..state import RequiredLawSource

# 근거법령 섹션 내용 형식: "법령명(제N조)||법령명(제N조)" (filtered_to_document.py,
# 예전에 확인했던 실제 형식 그대로).
_LEGAL_REF_PATTERN = re.compile(r"^(?P<name>.+?)(\(제(?P<article>[^)]+)조\))?$")


def parse_legal_basis_names(section_content: str) -> list[str]:
    """"A(제1조)||B(제2조)" 형태에서 법령명만 뽑는다 (조문번호는 버림)."""

    names: list[str] = []
    for raw in section_content.split("||"):
        raw = raw.strip()
        if not raw:
            continue
        match = _LEGAL_REF_PATTERN.match(raw)
        name = match.group("name").strip() if match else raw
        if name:
            names.append(name)
    return names


class LawSourceResolver(Protocol):
    """법령명 문자열 하나를 {law_type, source_id}로 바꾸는 인터페이스."""

    def resolve(self, law_name: str) -> RequiredLawSource | None:
        """매칭되는 법령을 못 찾으면 None을 돌려준다 (에러 아님)."""
        ...


class VectorStoreLawSourceResolver:
    """Resolve one exact canonical law name from the active LAW collection."""

    def __init__(self, store: ChromaVectorStore) -> None:
        self._store = store

    def resolve(self, law_name: str) -> RequiredLawSource | None:
        if (
            not isinstance(law_name, str)
            or not law_name
            or law_name != law_name.strip()
        ):
            raise ValueError("law_name must be a normalized non-empty string")

        chunks = self._store.get_chunks_by_metadata(
            SourceType.LAW,
            metadata_equals={"law_name": law_name},
        )
        pairs: set[tuple[str, str]] = set()
        for chunk in chunks:
            metadata = chunk.metadata
            if (
                chunk.source_type is not SourceType.LAW
                or metadata.get("law_name") != law_name
                or chunk.text.split("\n", 1)[0] != law_name
            ):
                raise ValueError("exact law lookup returned a mismatched canonical name")
            try:
                law_type = LegalDocumentType(metadata.get("law_type")).value
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "exact law lookup returned an unsupported law_type"
                ) from exc
            source_id = metadata.get("source_id")
            if (
                not isinstance(source_id, str)
                or not source_id.isascii()
                or not source_id.isdigit()
            ):
                raise ValueError("exact law lookup returned an invalid source_id")
            pairs.add((law_type, source_id))

        if not pairs:
            return None
        if len(pairs) != 1:
            raise ValueError("exact law_name maps to multiple source identities")
        law_type, source_id = next(iter(pairs))
        return {"law_type": law_type, "source_id": source_id}


def resolve_required_law_sources(
    legal_basis_content: str | None, resolver: LawSourceResolver
) -> list[RequiredLawSource]:
    """근거법령 섹션 내용을 파싱하고, 매칭되는 것만 골라서 돌려준다."""

    if not legal_basis_content:
        return []
    names = parse_legal_basis_names(legal_basis_content)
    resolved: list[RequiredLawSource] = []
    seen: set[tuple[str, str]] = set()
    for name in names:
        source = resolver.resolve(name)
        if source is not None:
            pair = (source["law_type"], source["source_id"])
            if pair in seen:
                continue
            seen.add(pair)
            resolved.append(source)
    return resolved
