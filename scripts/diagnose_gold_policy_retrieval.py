"""정답 정책이 왜 검색되지 않는지 판정한다 (색인 누락 / 필터 탈락 / 순위 밀림).

2026-09-14 Dev 검증 100문항 실측에서 정책별 Recall이 극단적으로 갈렸다:

    000000465790 유아학비        17/19 성공
    119200000007 해양사고 심판변론 14/19
    119200000001 어선 친환경장비   10/19
    116010000001 월세자금보증       1/19   <- 거의 전멸
    105100000001 근로장려금         0/19   <- 완전 전멸

그리고 "정답 ID가 Top-5에 들어갔는데 순위만 밀린" 경우는 0건이었다. 즉 순위
튜닝 문제가 아니라 후보군에 아예 안 뜨는 문제다. 원인은 셋 중 하나이고 고치는
방법이 서로 완전히 다르므로, 추측하지 말고 여기서 판정한다:

  A. 색인 누락    - 그 정책 청크가 vectorDB에 아예 없음        -> 재색인
  B. 필터 탈락    - index_policy.subsidy_regions_match()에서 제외 -> 메타데이터 수정
                    (region 메타데이터가 validate_region_metadata를 통과 못 하면
                     except ValueError -> return False 로 조용히 빠진다)
  C. 순위 밀림    - 후보엔 있는데 지자체 정책에 밀려 Top-k 밖   -> 질의/랭킹 수정
  D. 2차 필터 탈락 - semantic 검색엔 떴는데 policy_search가 그 뒤에 거는
                    filter_candidates()에서 제외 -> 사용자구분/지원조건 데이터 수정
                    (semantic 1위여도 여기서 떨어지면 답변에 안 나온다)

판정 방법은 단순하다. 파이프라인이 실제로 쓰는 것과 **같은** store·같은 질의·
같은 필터로 검색하되 top_k만 크게 잡는다. 정답 정책이

  - top_k를 키워도 안 나오고 metadata_equals 조회에도 없으면    -> A
  - metadata_equals 조회엔 있는데 region 필터를 못 통과하면      -> B
  - 큰 top_k에서는 나오는데 5 밖이면                            -> C
  - semantic 순위는 멀쩡한데 filter_candidates()가 떨어뜨리면    -> D

실행:
    python scripts/diagnose_gold_policy_retrieval.py
    python scripts/diagnose_gold_policy_retrieval.py --deep-top-k 100
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rag_design.contracts import SourceType, validate_region_metadata  # noqa: E402
from rag_design.index_policy import subsidy_regions_match  # noqa: E402
from rag_design.validation_runner import load_questions  # noqa: E402
from rag_design.vector_store import VectorSearchFilter  # noqa: E402
from src.rag_chatbot.graph.nodes.policy_search import _build_query  # noqa: E402
from src.rag_chatbot.graph.policy_conditions import (  # noqa: E402
    _is_individual_applicable,
    evaluate_conditions,
    load_policy_user_types,
    load_support_conditions,
)
from src.rag_chatbot.graph.slot_schema import resolve_filter_slots  # noqa: E402
from src.rag_chatbot.service import (  # noqa: E402
    _REAL_SUBSIDY_DOCUMENTS_PATH,
    _REAL_SUPPORT_CONDITIONS_PATH,
    connect_store,
)


def _chunks_for_policy(store, policy_id: str, limit: int):
    """region 필터 없이 source_id로만 조회한다 - 색인 존재 여부 확인용."""

    return store.search(
        SourceType.SUBSIDY,
        policy_id,
        query_id=f"diag-exists-{policy_id}",
        top_k=limit,
        search_filter=VectorSearchFilter(metadata_equals={"source_id": policy_id}),
    )


# 질문 본문의 짧은 지역명 -> contracts.CANONICAL_SIDO_NAMES의 정식 시도명.
# VectorSearchFilter가 validate_region_name()으로 정식명만 받는다("서울"은
# ValueError). 광주·전남은 이 프로젝트 기준으로 '전남광주통합특별시' 하나다.
_SIDO_ALIASES: tuple[tuple[str, str], ...] = (
    ("서울", "서울특별시"),
    ("부산", "부산광역시"),
    ("대구", "대구광역시"),
    ("인천", "인천광역시"),
    ("대전", "대전광역시"),
    ("울산", "울산광역시"),
    ("세종", "세종특별자치시"),
    ("경기", "경기도"),
    ("강원", "강원특별자치도"),
    ("충북", "충청북도"),
    ("충남", "충청남도"),
    ("전북", "전북특별자치도"),
    ("광주", "전남광주통합특별시"),
    ("전남", "전남광주통합특별시"),
    ("경북", "경상북도"),
    ("경남", "경상남도"),
    ("제주", "제주특별자치도"),
)


def _income_bracket(text: str) -> str | None:
    """'기준중위소득 50%' -> IncomeBracket 값."""

    match = re.search(r"중위소득\s*(\d+)\s*%", text)
    if not match:
        return None
    pct = int(match.group(1))
    if pct < 30:
        return "under_30"
    if pct < 50:
        return "pct_30_50"
    if pct < 75:
        return "pct_50_75"
    if pct < 100:
        return "pct_75_100"
    if pct < 150:
        return "pct_100_150"
    return "over_150"


def _slots_from_question(item: dict) -> dict:
    """fixture의 slot_answers를 N1이 뽑았을 슬롯 값으로 되돌린다.

    fixture 문장은 템플릿이라 정규식으로 충분하다(LLM을 다시 태우면 진단
    한 번에 수십 분이 걸린다). 여기서 만든 슬롯은 region 필터뿐 아니라
    filter_candidates()의 소득/취업/성별/장애 조건 대조에도 쓰이므로,
    실제 파이프라인이 보는 값과 같아야 한다.
    """

    answers = item.get("slot_answers") or {}
    question = item["question"]
    slots: dict = {"interests": []}

    haystack = f"{answers.get('region', '')} {question}"
    for short, canonical in _SIDO_ALIASES:
        if short in haystack:
            slots["region_names"] = [canonical]
            break
    else:
        slots["region_names"] = []

    birth = re.search(r"(\d{4})-(\d{2})-(\d{2})", answers.get("birth_date", ""))
    if birth:
        slots["birth_date"] = birth.group(0)

    bracket = _income_bracket(answers.get("income_bracket", "") or question)
    if bracket:
        slots["income_bracket"] = bracket

    gender_text = answers.get("gender", "")
    if "남성" in gender_text:
        slots["gender"] = "male"
    elif "여성" in gender_text:
        slots["gender"] = "female"

    disability = answers.get("disability_status", "")
    if "모름" in disability:
        slots["disability_status"] = "unknown"
    elif "없" in disability:
        slots["disability_status"] = "not_registered"
    elif disability:
        slots["disability_status"] = "registered"

    employment = answers.get("employment_status", "")
    for needle, value in (
        ("재직", "employed"),
        ("자영업", "self_employed"),
        ("구직", "job_seeking"),
        ("학생", "student"),
        ("무직", "not_working"),
        ("모름", "unknown"),
    ):
        if needle in employment:
            slots["employment_status"] = value
            break

    return slots


def _rank_of(policy_id: str, results) -> int | None:
    seen: list[str] = []
    for hit in results:
        source_id = str(hit.chunk.metadata.get("source_id", ""))
        if source_id and source_id not in seen:
            seen.append(source_id)
        if source_id == policy_id:
            return len(seen)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=_REPO_ROOT / "data/evaluation/dev_questions.jsonl"
    )
    parser.add_argument(
        "--deep-top-k",
        type=int,
        default=50,
        help="순위 밀림(C)을 판정하려고 크게 잡는 top_k. 서비스 기본은 5다.",
    )
    parser.add_argument(
        "--per-policy",
        type=int,
        default=4,
        help="정책마다 실제 검색을 재현해볼 질문 수",
    )
    args = parser.parse_args()

    questions = load_questions(args.questions)
    by_policy: dict[str, list[dict]] = defaultdict(list)
    for item in questions:
        for policy_id in item["expected_policy_ids"]:
            by_policy[policy_id].append(item)

    print(f"질문 {len(questions)}건, 정답 정책 {len(by_policy)}종")
    print("vectorDB 연결 중(임베딩 모델 로딩에 수십 초 걸릴 수 있습니다)...", flush=True)
    store = connect_store()
    support_conditions = load_support_conditions(_REAL_SUPPORT_CONDITIONS_PATH)
    user_types = load_policy_user_types(_REAL_SUBSIDY_DOCUMENTS_PATH)
    print(f"연결 완료. 지원조건 {len(support_conditions)}건, "
          f"사용자구분 {len(user_types)}건 로드.\n", flush=True)

    verdicts: dict[str, str] = {}

    for policy_id in sorted(by_policy, key=lambda p: -len(by_policy[p])):
        items = by_policy[policy_id]
        print("=" * 72)
        print(f"[{policy_id}] 이 정책을 정답으로 하는 질문 {len(items)}건")

        # --- A. 색인 존재 ------------------------------------------------
        chunks = _chunks_for_policy(store, policy_id, args.deep_top_k)
        if not chunks:
            print("  색인   : 청크 0개 - vectorDB에 이 정책이 없습니다")
            verdicts[policy_id] = "A 색인 누락"
            print()
            continue
        sections = Counter(
            str(hit.chunk.metadata.get("section_type", "?")) for hit in chunks
        )
        print(f"  색인   : 청크 {len(chunks)}개 {dict(sections)}")

        # --- B. region 메타데이터 / 필터 통과 -----------------------------
        meta = chunks[0].chunk.metadata
        scope = meta.get("region_scope")
        names = meta.get("region_names")
        print(f"  메타   : region_scope={scope!r} region_names={names!r}")
        try:
            validate_region_metadata(scope, names)
            print("  검증   : validate_region_metadata 통과")
            metadata_ok = True
        except ValueError as exc:
            print(f"  검증   : *** 실패 *** {exc}")
            print("           -> subsidy_regions_match()가 except ValueError로")
            print("              이 정책을 모든 지역 검색에서 제외합니다")
            metadata_ok = False

        regions_used = sorted(
            {
                tuple(_slots_from_question(item)["region_names"])
                for item in items
            }
        )
        blocked = [
            r[0]
            for r in regions_used
            if r and not subsidy_regions_match(meta, (r[0],))
        ]
        if blocked:
            print(f"  필터   : *** 이 지역들에서 탈락 *** {blocked}")
        else:
            print("  필터   : 질문에 쓰인 모든 지역에서 통과")

        if not metadata_ok or blocked:
            verdicts[policy_id] = "B region 필터 탈락"
            print()
            continue

        # --- D. 검색 후 2차 필터(filter_candidates) -----------------------
        # policy_search는 semantic 2000건을 받아 filter_candidates()로 거른
        # **뒤에** top_k를 자른다. 여기서 떨어지면 semantic 1위여도 사라진다.
        if not _is_individual_applicable(user_types.get(policy_id)):
            kinds = sorted(user_types.get(policy_id) or ())
            print(f"  사용자 : *** 개인 대상 아님 *** 사용자구분={kinds}")
            print("           -> filter_candidates()가 모든 질문에서 제외합니다")
            verdicts[policy_id] = "D 사용자구분 탈락(개인 대상 아님)"
            print()
            continue
        print(f"  사용자 : 개인 적용 가능 (사용자구분={sorted(user_types.get(policy_id) or ()) or '정보없음'})")

        values = support_conditions.get(policy_id)
        if values is None:
            print("  지원조건: sidecar에 조건 없음 - 조건 대조 없이 통과(fail-open)")
        else:
            violated_for: list[str] = []
            for item in items:
                plan = resolve_filter_slots(
                    _slots_from_question(item), reference_date=date.today()
                )
                aspects = evaluate_conditions(values, plan)
                bad = sorted(k for k, v in aspects.items() if v["violated"])
                if bad:
                    violated_for.append(f"{item['question_id']}({','.join(bad)})")
            if violated_for:
                print(f"  지원조건: *** {len(violated_for)}/{len(items)}건에서 조건 위반으로 탈락 ***")
                for line in violated_for[:5]:
                    print(f"           - {line}")
                if len(violated_for) > 5:
                    print(f"           ... 외 {len(violated_for) - 5}건")
                verdicts[policy_id] = (
                    f"D 지원조건 탈락 {len(violated_for)}/{len(items)}건"
                )
                print()
                continue
            print(f"  지원조건: {len(items)}건 모두 통과")

        # --- C. 실제 질의 재현 -------------------------------------------
        print(f"  재현   : 서비스와 같은 질의·필터, top_k={args.deep_top_k}")
        ranks: list[int | None] = []
        for item in items[: args.per_policy]:
            slots = _slots_from_question(item)
            plan = resolve_filter_slots(slots, reference_date=date.today())
            condition = plan["hard"].get("region")
            region_names = tuple(condition.get("any_of", ())) if condition else ()
            query = _build_query(slots, item["question"])
            try:
                results = store.search(
                    SourceType.SUBSIDY,
                    query,
                    query_id=f"diag-{item['question_id']}",
                    top_k=args.deep_top_k,
                    search_filter=VectorSearchFilter(region_names=region_names),
                )
            except Exception as exc:  # 한 건이 죽어도 나머지 판정은 계속한다
                print(f"    ERR  {item['question_id']:<38} {type(exc).__name__}: {exc}")
                continue
            rank = _rank_of(policy_id, results)
            ranks.append(rank)
            shown = f"{rank}위" if rank else f"top-{args.deep_top_k} 밖"
            mark = "OK " if rank and rank <= 5 else "MISS"
            print(f"    {mark} {item['question_id']:<38} {shown}")

        found = [r for r in ranks if r]
        if not found:
            verdicts[policy_id] = f"A/B 후보에 없음(top-{args.deep_top_k}에도 미등장)"
        elif all(r > 5 for r in found):
            verdicts[policy_id] = f"C 순위 밀림(최선 {min(found)}위)"
        else:
            verdicts[policy_id] = "정상"
        print()

    print("=" * 72)
    print("판정")
    for policy_id, verdict in verdicts.items():
        print(f"  {policy_id}: {verdict}")
    print()
    print("A 색인 누락 -> 그 정책을 다시 색인. B 필터 탈락 -> region 메타데이터 수정.")
    print("C 순위 밀림 -> _build_query()/랭킹 수정(지자체 정책이 상위를 채우는 문제).")


if __name__ == "__main__":
    main()
