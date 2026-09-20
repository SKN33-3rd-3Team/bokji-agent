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
from src.rag_chatbot.graph.llm_gateway import (  # noqa: E402
    _bracket_for_percent,
    _INCOME_PERCENT_PATTERN,
)
from src.rag_chatbot.graph.nodes.policy_search import _build_query  # noqa: E402
from src.rag_chatbot.graph.nodes.slot_parser import (  # noqa: E402
    _SIDO_ALIASES as _SLOT_PARSER_SIDO_ALIASES,
)
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
# slot_parser.py의 정식 표를 그대로 쓴다(measure_retrieval_distance.py도
# 동일) - 별도 사본을 두면 정식 표가 바뀔 때 진단 스크립트만 옛 별칭으로
# 조용히 남는다.
_SIDO_ALIASES: tuple[tuple[str, str], ...] = tuple(_SLOT_PARSER_SIDO_ALIASES.items())


def _income_bracket(text: str) -> str | None:
    """'기준중위소득 50%' -> IncomeBracket 값.

    llm_gateway._extract_income_bracket과 같은 정규식·경계값·방향("초과"/
    "이상") 판정을 그대로 쓴다 - 여기서 경계값을 따로 하드코딩해 재구현하면
    실제 서비스 쪽 구간 경계가 바뀔 때 이 진단 스크립트만 옛 기준으로 남아
    회귀 진단 결과가 실제 동작과 어긋난다.
    """

    match = _INCOME_PERCENT_PATTERN.search(text)
    if match is None:
        return None
    return _bracket_for_percent(
        int(match.group(1)), text[match.end() : match.end() + 6]
    )


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


# 2026-09-15: 여기 있던 ``_intent_tail``("마지막 문장만 남긴다")은 삭제했다.
#
# 질의 희석 효과 자체는 실재했다(Top-5 62/95 -> 85/95). 하지만 그 구현은 이
# 질문 세트가 전부 "인적사항 문장 + 요구 문장" 한 가지 틀로 쓰였다는 사실에
# 기대고 있었다 - 즉 fixture 과적합이다. 마지막 문장이 "관련 지원이 있나요?"
# 같은 일반 문형이면 주제어는 앞 문장에 남는데 그걸 통째로 버려서,
# dev-energy-led-019(2위)와 dev-marine-defense-minor-022(1위)가 top-50 밖으로
# 회귀했다.
#
# 대신 ``llm_gateway.strip_profile_phrases``가 "이미 필터가 처리하는 표현만
# 걷어낸다"는 규칙으로 같은 효과를 내고 문장 순서에 의존하지 않는다. 그게
# 이제 서비스 기본 동작이므로 **이 스크립트의 기본 실행이 곧 수정 후**이고,
# ``--raw-query``가 수정 전(인적사항이 섞인 질의)을 재현한다.


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
        "--raw-query",
        action="store_true",
        help="인적사항을 걷어내기 전(2026-09-15 이전)의 질의로 검색한다."
        " 기본 실행과 두 번 돌려 총계를 비교하면 이 수정이 실제로 순위를"
        " 올렸는지 확인할 수 있다.",
    )
    parser.add_argument(
        "--per-policy",
        type=int,
        default=0,
        help="정책마다 재현할 질문 수. 0(기본)이면 전부 - 일부만 보면 '9/19가 왜"
        " 실패하는가'를 판단할 수 없다. 빠르게 훑을 때만 4 같은 값을 준다.",
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
    totals: dict[str, int] = {}
    top5_counts: dict[str, int] = {}

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
        sample = items if args.per_policy <= 0 else items[: args.per_policy]
        mode = "수정 전 질의(인적사항 포함)" if args.raw_query else "서비스와 같은 질의"
        print(
            f"  재현   : {mode}·필터, top_k={args.deep_top_k}"
            f" ({len(sample)}/{len(items)}건)"
        )
        ranks: list[int | None] = []
        for item in sample:
            slots = _slots_from_question(item)
            plan = resolve_filter_slots(slots, reference_date=date.today())
            condition = plan["hard"].get("region")
            region_names = tuple(condition.get("any_of", ())) if condition else ()
            query = _build_query(
                slots, item["question"], strip_profile=not args.raw_query
            )
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

        # 순위 분포. "9/19 실패"가 '5위 턱걸이로 밀린 것'인지 '아예 후보에도
        # 없는 것'인지에 따라 고치는 방법이 완전히 달라서, 실패 건을 구간으로
        # 쪼개 보여준다.
        found = [r for r in ranks if r]
        absent = len(ranks) - len(found)
        in_top5 = sum(1 for r in found if r <= 5)
        near = sorted(r for r in found if 5 < r <= 10)
        far = sorted(r for r in found if r > 10)
        print(
            f"    └ 순위 분포: Top-5 {in_top5}건 / 6~10위 {len(near)}건"
            f" / 11위~ {len(far)}건 / top-{args.deep_top_k} 밖 {absent}건"
        )
        if near:
            print(f"      6~10위(아깝게 밀림): {', '.join(f'{r}위' for r in near)}")
        if far:
            print(f"      11위~(크게 밀림)   : {', '.join(f'{r}위' for r in far)}")

        # 판정은 통과 **비율**로 낸다. 예전에는 한 건이라도 Top-5에 들면
        # "정상"이라 9/19 실패하는 정책까지 정상으로 찍혀 쓸모가 없었다.
        total = len(ranks)
        totals[policy_id] = total
        top5_counts[policy_id] = in_top5
        if not total:
            verdicts[policy_id] = "재현 실패(검색 에러)"
        elif in_top5 == total:
            verdicts[policy_id] = f"정상 ({in_top5}/{total} Top-5)"
        elif not found:
            verdicts[policy_id] = (
                f"A/B 후보에 없음 ({total}건 전부 top-{args.deep_top_k} 밖)"
            )
        else:
            detail = []
            if near:
                detail.append(f"6~10위 {len(near)}")
            if far:
                detail.append(f"11위~ {len(far)}")
            if absent:
                detail.append(f"미등장 {absent}")
            verdicts[policy_id] = (
                f"C 순위 밀림 ({in_top5}/{total} Top-5"
                + (f", {', '.join(detail)}" if detail else "")
                + ")"
            )
        print()

    print("=" * 72)
    print("판정")
    for policy_id, verdict in verdicts.items():
        print(f"  {policy_id}: {verdict}")

    # 두 모드(기본 / --raw-query)를 번갈아 돌려 비교할 수 있게 총계를 찍는다.
    # 정책별 숫자만 보면 "좋아졌나?"를 눈으로 더해야 한다.
    total_all = sum(totals.values())
    top5_all = sum(top5_counts.values())
    mode = "수정 전 질의(인적사항 포함)" if args.raw_query else "현재 서비스 질의(기본)"
    print()
    print("=" * 72)
    print(f"총계 [{mode}]: Top-5 {top5_all}/{total_all}"
          f" ({top5_all / total_all:.1%})" if total_all else "총계: 없음")
    print("=" * 72)
    print("--raw-query 로 한 번 더 돌려 이 숫자를 비교하세요. 기본 실행이 더")
    print("높아야 인적사항 제거가 실제로 효과가 있다는 뜻입니다.")
    print()
    print("A 색인 누락 -> 그 정책을 다시 색인. B 필터 탈락 -> region 메타데이터 수정.")
    print("C 순위 밀림 -> _build_query()/랭킹 수정.")


if __name__ == "__main__":
    main()
