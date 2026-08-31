"""정책 원문의 "근거법령" 문자열을, 유나님이 수집한 법령 데이터
(law_documents.jsonl)와 이름으로 매칭해서 {law_type, source_id}로 바꾼다.

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

import json
import re
from pathlib import Path
from typing import Protocol

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


class LawDocumentIndexResolver:
    """law_documents.jsonl(Document 형식)을 읽어서 이름 -> {law_type, source_id}
    조회 테이블을 만들고, 그걸로 매칭하는 실제 구현체.

    법령명이 정확히 일치하는 것 우선, 없으면 "OO법 시행령"처럼 접미어가
    붙은 이름이 기준 법령명으로 시작하는 경우도 매칭한다.
    """

    def __init__(self, law_documents_path: Path) -> None:
        self._by_exact_name: dict[str, RequiredLawSource] = {}
        with law_documents_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                doc = json.loads(line)
                name = doc.get("metadata", {}).get("law_name") or doc.get("title")
                law_type = doc.get("metadata", {}).get("law_type")
                source_id = doc.get("source_id")
                if not name or not law_type or not source_id:
                    continue
                self._by_exact_name[name] = {
                    "law_type": law_type,
                    "source_id": source_id,
                }

    def resolve(self, law_name: str) -> RequiredLawSource | None:
        if law_name in self._by_exact_name:
            return self._by_exact_name[law_name]
        # 정확히 안 맞으면, 기준 법령명으로 시작하는 걸 느슨하게 매칭
        # (예: 정책 원문은 "OO법"만 언급했는데 실제 수집본은 "OO법 시행령"뿐인 경우 등).
        for name, source in self._by_exact_name.items():
            if name.startswith(law_name) or law_name.startswith(name):
                return source
        return None


def resolve_required_law_sources(
    legal_basis_content: str | None, resolver: LawSourceResolver
) -> list[RequiredLawSource]:
    """근거법령 섹션 내용을 파싱하고, 매칭되는 것만 골라서 돌려준다."""

    if not legal_basis_content:
        return []
    names = parse_legal_basis_names(legal_basis_content)
    resolved: list[RequiredLawSource] = []
    for name in names:
        source = resolver.resolve(name)
        if source is not None:
            resolved.append(source)
    return resolved
