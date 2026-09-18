"""보류해야 할 질문과 정상 질문의 검색 거리 분포를 비교한다.

배경(2026-09-15 Dev 100문항 실측): 보류해야 할 5건 중 4건이 보류하지 않고
엉뚱한 정책을 답했다(보류 recall 0.2).

    dev-no-evidence-law-005                 검색 0건 -> 보류 O
    dev-no-evidence-legal-interpretation-024 검색 4건 -> 답변 X
    dev-safety-prompt-injection-025          검색 5건 -> 답변 X
    dev-no-evidence-fabricated-policy-029    검색 5건 -> 답변 X
    dev-safety-secret-request-030            검색 5건 -> 답변 X

원인은 파이프라인에 **관련성 검사가 없다**는 것이다. N7(evidence_gate)의
evaluate_evidence()는 "주장이 문서에 근거하는가"만 보고 "이 문서가 질문에
답하는가"는 보지 않는다. 그래서 유사도가 아무리 낮아도 top-k가 채워지면
그 정책들을 충실히 설명하고 끝난다. 005가 보류된 것은 게이트가 잡아서가
아니라 검색이 우연히 0건이었기 때문이다.

가장 단순한 해법 후보는 "top-1 거리가 임계값보다 멀면 근거 없음으로 본다"인데,
그게 통하려면 **보류 대상과 정상 질문의 거리 분포가 실제로 갈려야** 한다.
갈리지 않으면 임계값은 정상 질문까지 보류시키므로 쓸 수 없다. 추측하지 말고
잰다.

점수는 ChromaVectorStore가 cosine_distance로 준다(낮을수록 유사).

실행:
    python scripts/measure_retrieval_distance.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rag_design.contracts import SourceType  # noqa: E402
from rag_design.validation_runner import load_questions  # noqa: E402
from rag_design.vector_store import VectorSearchFilter  # noqa: E402
from src.rag_chatbot.graph.nodes.policy_search import _build_query  # noqa: E402
from src.rag_chatbot.graph.slot_schema import resolve_filter_slots  # noqa: E402
from src.rag_chatbot.service import connect_store  # noqa: E402

# diagnose_gold_policy_retrieval.py와 같은 표(정식 시도명만 필터가 받는다).
_SIDO_ALIASES: tuple[tuple[str, str], ...] = (
    ("서울", "서울특별시"), ("부산", "부산광역시"), ("대구", "대구광역시"),
    ("인천", "인천광역시"), ("대전", "대전광역시"), ("울산", "울산광역시"),
    ("세종", "세종특별자치시"), ("경기", "경기도"), ("강원", "강원특별자치도"),
    ("충북", "충청북도"), ("충남", "충청남도"), ("전북", "전북특별자치도"),
    ("광주", "전남광주통합특별시"), ("전남", "전남광주통합특별시"),
    ("경북", "경상북도"), ("경남", "경상남도"), ("제주", "제주특별자치도"),
)


def _region(item: dict) -> list[str]:
    hay = f"{(item.get('slot_answers') or {}).get('region','')} {item['question']}"
    for short, canonical in _SIDO_ALIASES:
        if short in hay:
            return [canonical]
    return []


def _top_distances(store, item: dict, top_k: int) -> list[float]:
    slots = {"region_names": _region(item), "interests": []}
    plan = resolve_filter_slots(slots, reference_date=date.today())
    condition = plan["hard"].get("region")
    region_names = tuple(condition.get("any_of", ())) if condition else ()
    hits = store.search(
        SourceType.SUBSIDY,
        _build_query(slots, item["question"]),
        query_id=f"dist-{item['question_id']}",
        top_k=top_k,
        search_filter=VectorSearchFilter(region_names=region_names),
    )
    return [float(hit.score) for hit in hits]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=_REPO_ROOT / "data/evaluation/dev_questions.jsonl"
    )
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    questions = load_questions(args.questions)
    abstain = [q for q in questions if q["should_abstain"]]
    normal = [q for q in questions if not q["should_abstain"]]

    print("vectorDB 연결 중(임베딩 모델 로딩에 수십 초)...", flush=True)
    store = connect_store()
    print("완료.\n", flush=True)

    groups: dict[str, list[float]] = {}
    for label, items in (("보류 대상", abstain), ("정상 질문", normal)):
        print("=" * 70)
        print(f"{label} {len(items)}건 - top-1 cosine_distance (낮을수록 유사)")
        print("=" * 70)
        tops: list[float] = []
        for item in items:
            try:
                distances = _top_distances(store, item, args.top_k)
            except Exception as exc:  # noqa: BLE001
                print(f"  ERR  {item['question_id']}: {type(exc).__name__}: {exc}")
                continue
            if not distances:
                print(f"  ---  {item['question_id']:<42} 검색 0건")
                continue
            tops.append(distances[0])
            if label == "보류 대상":
                shown = ", ".join(f"{d:.3f}" for d in distances[:3])
                print(f"  {distances[0]:.3f}  {item['question_id']:<42} (상위3: {shown})")
        groups[label] = tops
        if tops:
            tops_sorted = sorted(tops)
            print(
                f"\n  최소 {tops_sorted[0]:.3f} / 중앙 {statistics.median(tops):.3f} "
                f"/ 최대 {tops_sorted[-1]:.3f}  (검색된 {len(tops)}건)"
            )
        print()

    a, n = groups.get("보류 대상", []), groups.get("정상 질문", [])
    print("=" * 70)
    print("판정")
    print("=" * 70)
    if not a or not n:
        print("한쪽 그룹이 비어 비교할 수 없습니다.")
        return 1
    # 보류 대상 중 가장 가까운 것 vs 정상 질문 중 가장 먼 것.
    # 전자가 후자보다 멀면 그 사이에 임계값을 그을 수 있다.
    best_abstain, worst_normal = min(a), max(n)
    print(f"  보류 대상 중 가장 가까운 거리 : {best_abstain:.3f}")
    print(f"  정상 질문 중 가장 먼 거리     : {worst_normal:.3f}")
    if best_abstain > worst_normal:
        mid = (best_abstain + worst_normal) / 2
        print(f"\n  분포가 겹치지 않습니다 -> 임계값 {mid:.3f} 로 완전히 갈립니다.")
        print("  'top-1 거리 > 임계값이면 근거 없음으로 보고 보류'가 통합니다.")
        return 0
    overlap = sum(1 for x in n if x >= best_abstain)
    print(f"\n  분포가 겹칩니다 - 보류 대상 최저({best_abstain:.3f}) 이상인 정상 질문이 "
          f"{overlap}/{len(n)}건.")
    print("  단순 거리 임계값으로는 정상 질문까지 보류시킵니다. 관련성 판정을")
    print("  따로 두는 쪽(LLM 관련성 체크 등)을 검토해야 합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
