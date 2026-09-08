"""이번 브랜치(fix/41-fix-node11-logic) 변경 사항을 재색인 없이 확인한다.

왜 필요한가
-----------
변경이 세 갈래(수집기 / N4 / N11)로 나뉘어 있는데, 전부 확인하려면 재색인
(수십 분~1시간)을 기다려야 하는 것으로 오해하기 쉽다. 실제로는 **재색인이
필요한 건 새로 추가한 섹션(구비서류·문의처 등)뿐**이고, N4의 정책 단위
top-N과 N11의 조항 분류는 기존 색인으로도 그대로 확인된다.

이 스크립트는 재색인 없이 확인 가능한 것만 모아서 한 번에 보여준다.

실행 (레포 루트에서)::

    python scripts/verify_changes.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from src.rag_chatbot.collectors.gov_24.to_document import (  # noqa: E402
    collect_support_conditions,
    convert_one,
)
from src.rag_chatbot.graph.nodes.duplicate_benefit import (  # noqa: E402
    _RESTRICTION_PATTERN,
    classify_clause,
    find_named_conflicts,
)

_MERGED = _REPO_ROOT / "data" / "raw" / "gov24_merged.json"
_DOCS = _REPO_ROOT / "data" / "processed" / "subsidy_documents.jsonl"
_SAMPLE_SERVICE_ID = "000000465790"  # 유아학비 (누리과정) 지원


def _rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def check_collector() -> bool:
    """수집기: 새 섹션 · JA 코드 전체 · 시크릿 미포함."""

    _rule("1. 수집기 — 정부24 전 필드 저장 (재색인 전이라도 확인 가능)")
    if not _MERGED.exists():
        print(f"  [건너뜀] {_MERGED} 가 없습니다. 수집을 먼저 돌려야 합니다.")
        return True

    rows = json.loads(_MERGED.read_text(encoding="utf-8"))
    target = next((r for r in rows if r.get("서비스ID") == _SAMPLE_SERVICE_ID), rows[0])
    document = convert_one(target, "2026-09-07T00:00:00+09:00", [], set(), set(), {})
    if document is None:
        print("  [실패] 표본 문서를 변환하지 못했습니다.")
        return False

    metadata = document["metadata"]
    section_types = [s["metadata"]["section_type"] for s in document["sections"]]
    print(f"  표본: {document['title']}")
    print(f"  섹션 {len(section_types)}개: {', '.join(section_types)}")

    ok = True
    for expected in ("purpose_summary", "required_documents", "contact"):
        mark = "OK  " if expected in section_types else "없음"
        if expected not in section_types:
            ok = False
        print(f"    [{mark}] 새 섹션 {expected}")

    conditions = metadata.get("support_conditions") or {}
    active = metadata.get("support_condition_codes") or []
    print(f"  JA 코드: 전체 {len(conditions)}개 저장 / 켜진 코드 {len(active)}개")
    print(f"    {', '.join(active[:12])}{' ...' if len(active) > 12 else ''}")
    if len(conditions) < 40:
        print("    [실패] JA 코드가 충분히 저장되지 않았습니다.")
        ok = False

    # 시크릿이 문서에 실리면 안 된다(상세조회URL의 serviceKey).
    rendered = json.dumps(document, ensure_ascii=False)
    for marker in ("serviceKey", "ServiceKey"):
        if marker in rendered:
            print(f"    [실패] 문서에 {marker} 가 남아 있습니다.")
            ok = False
    if ok:
        print("    [OK  ] 문서에 API 키가 실리지 않음")

    # "해당없음"만 든 섹션은 만들지 않는다.
    placeholder = [
        s for s in document["sections"] if s["content"].strip() in ("해당없음", "해당 없음")
    ]
    if placeholder:
        print(f"    [실패] 빈 값 섹션이 {len(placeholder)}개 남아 있습니다.")
        ok = False
    else:
        print("    [OK  ] '해당없음'만 든 섹션 없음")
    return ok


def check_corpus_scale() -> bool:
    """전체 코퍼스에서 섹션 수가 어떻게 바뀌는지(재색인 규모 예측)."""

    _rule("2. 재색인 규모 — 섹션이 얼마나 늘어나는지")
    if not _MERGED.exists():
        print("  [건너뜀] raw 데이터가 없습니다.")
        return True

    rows = json.loads(_MERGED.read_text(encoding="utf-8"))
    counts = Counter()
    for row in rows:
        document = convert_one(row, "2026-09-07T00:00:00+09:00", [], set(), set(), {})
        if document is None:
            continue
        for section in document["sections"]:
            counts[section["metadata"]["section_type"]] += 1

    total = sum(counts.values())
    print(f"  변경 후 총 섹션 {total:,}개 (문서 {len(rows):,}건)")
    for name, count in counts.most_common():
        print(f"    {name:30} {count:6,}")
    print("\n  참고: 현재 색인된 subsidy 청크는 60,497개다.")
    print("  섹션이 늘어난 만큼 재색인 시간도 늘어난다(CPU면 1시간 이상 예상).")
    return True


def check_clause_classification() -> bool:
    """N11: 실제 원문 조항이 other/household/header 로 갈리는지."""

    _rule("3. N11 — 중복 조항 분류 (재색인 불필요)")
    if not _DOCS.exists():
        print(f"  [건너뜀] {_DOCS} 가 없습니다.")
        return True

    import re

    counts = Counter()
    examples: dict[str, str] = {}
    with _DOCS.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            document = json.loads(line)
            for section in document.get("sections") or []:
                for sentence in re.split(r"(?<=[.。!?])\s+|\n", section.get("content") or ""):
                    sentence = sentence.strip()
                    if not _RESTRICTION_PATTERN.search(sentence):
                        continue
                    kind = classify_clause(sentence)
                    counts[kind] += 1
                    examples.setdefault(kind, sentence[:70])

    total = sum(counts.values()) or 1
    print(f"  중복 관련 조항 {total}개를 분류했다.")
    for kind, count in counts.most_common():
        print(f"    {kind:10} {count:4}건 ({count / total * 100:4.1f}%)  예: {examples[kind]}")
    if not counts:
        print("    [실패] 조항을 하나도 찾지 못했습니다.")
        return False
    return True


def check_named_conflict() -> bool:
    """N11: 같은 답변 안의 상대 정책을 실제로 찾아내는지."""

    _rule("4. N11 — 상대 정책 매칭 (재색인 불필요)")
    clause = "○ 중복불가서비스 : 유아학비(누리과정) 지원, 영유아보육료 지원"
    titles = {
        "p-a": "영유아보육료 지원",
        "p-b": "가정양육수당 지원",
        "p-c": "청년 월세 지원",
    }
    found = find_named_conflicts([clause], titles, self_policy_id="p-b")
    print(f"  조항: {clause}")
    print(f"  같은 답변의 정책: {list(titles.values())}")
    print(f"  찾아낸 상대: {[titles[pid] for pid in found]}")
    ok = set(found) == {"p-a"}
    print(f"    [{'OK  ' if ok else '실패'}] 영유아보육료만 충돌로 잡히고 무관한 정책은 안 잡힘")
    return ok


def main() -> int:
    results = [
        check_collector(),
        check_corpus_scale(),
        check_clause_classification(),
        check_named_conflict(),
    ]
    _rule("결과")
    if all(results):
        print("  모두 통과했습니다.")
        print("\n  아직 확인되지 않은 것 (재색인이 필요합니다):")
        print("   - 새 섹션(구비서류·문의처)이 실제로 검색되는지")
        print("   - JA 코드가 chunk metadata까지 넘어가는지")
        print("   - N4 정책 단위 top-N 이 실제 검색 결과에서 N건을 돌려주는지")
        return 0
    print("  실패한 항목이 있습니다. 위 로그를 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
