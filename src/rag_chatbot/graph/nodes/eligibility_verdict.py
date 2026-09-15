"""N9 자격 판정 노드.

state에서 받은 사용자 정보(slots)와 이전 노드(N4~N7)가 전달한 정책 정보
(claim_plan - N6/N7에서 근거·시행일·충돌·안전까지 검증된 상태)를 바탕으로,
후보 정책별로 사용자가 지원자격에 적합한지 "충족" / "미충족" / "미확인"을 판정한다.

claim_plan은 이미 검증된 근거이지만, 이 노드는 그 근거가 가리키는 정책 문서를
vectorDB에서 한 번 더 검색해 재확인한다 - 재검색으로 얻은 chunk의 구조화
metadata(age_start/age_end 등)를 slots와 직접 대조해서, "근거 문장이 뒷받침된다"는
것과 "이 사용자가 그 조건을 실제로 만족한다"는 것을 구분한다. 새로운 정책을
찾는 게 아니라, 이미 claim_plan이 가리키는 그 정책 문서를 doc_id로 좁혀서
다시 확인하는 것이므로 새로운 근거를 만들어내는 게 아니다.

- 충족: 관련 eligibility claim이 모두 SUPPORTED이고, 재검색한 chunk의 구조화
  조건(age_start/age_end)과 slots 사이에 위반이 없고 지역 UNKNOWN이 아님.
  지역 UNKNOWN은 기존 조건 결과를 유지한 채 지역만 미확인으로 추가한다.

  **중요(2026-08-31 추가, 2026-09-12 갱신): "충족"은 "모든 자격
  조건을 만족한다"는 뜻이 아니다.**
  이 노드가 실제로 대조할 수 있는 조건은 구조화된 데이터가 있는
  것뿐이다. 색인된 문서 metadata에는 ``age_start``/``age_end``가 있어
  연령은 항상 대조한다. 장애 여부·성별·소득·취업 상태는 원래 문서
  본문 텍스트에만 있어서 비교 자체를 하지 못했는데(그래서
  비장애인에게 장애인 정책이 "충족"으로 나오는 일이 실제로
  있었다), 정부24 지원조건 사이드카(``support_conditions``, JA 코드)가
  이 정책을 구조화해 가지고 있을 때만 대조한다
  (policy_conditions.evaluate_conditions() 참고). 코드가 없거나 결측이면
  (정부24 자체가 그 정책에 대해 그 범주를 안 적어둔 경우도 많다 -
  실측 16%) 여전히 비교하지 못한다. 판정 결과에 ``checked``/``unchecked``를
  함께 담아, 무엇을 확인했고 무엇을 확인하지 못했는지 답변과
  화면이 그대로 드러나게 한다
  (docs/PROJECT_COMPLIANCE.md - 확인하지 않은 것을 확인한 것처럼 말하지 않는다).
- 미충족: 재검색한 chunk의 구조화 조건과 slots가 명백히 어긋남
  (예: age_start/age_end 범위를 벗어남). 이 경우만 "위반이 확인됐다"고 본다.
- 미확인: 그 외 전부 - claim 근거가 없거나(UNSUPPORTED/PARTIAL/CONFLICT),
  재검색에서 해당 정책 chunk를 다시 찾지 못했거나(컬렉션이 아예 없는 경우 포함),
  슬롯/문서 어느 한쪽에 구조화 조건이 없어 비교 자체가 불가능한 경우.

근거 문장에 명시되지 않은 조건은 절대 판단하지 않고 미확인을 반환한다
(추측 금지).

LLM 사용 범위(2026-08-31 기준, 프롬프트/모델 아직 확정 전 - 팀에서
skt/A.X-4.0-Light, Qwen/Qwen3.5-9B, Bllossom/llama-3.2-Korean-Bllossom-3B
세 모델을 비교 중이고 RunPod Serverless로 서빙 예정): 충족/미충족/미확인
자체는 여전히 규칙(구조화 metadata 비교)이 결정한다 - LLM이 판정을 내리게
하면 "추론 금지" 원칙이 깨지기 때문이다. LLM은 규칙이 이미 확정한 위반
사유(예: age_start/age_end 비교 결과)를 사람이 읽기 좋은 문장으로 다듬는
데만 쓴다("JA코드 자연어화"). LLM 호출이 없거나 실패해도 규칙이 만든
원래 문장을 그대로 쓰므로, 판정 결과나 노드 동작 자체는 LLM 유무와 무관하게
동일하다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from rag_design.contracts import EvidenceStatus, RegionScope, RetrievedChunk, SourceType
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
)

from ...llm import LLMCallError, LLMClient
from .document_verification import merge_evidence_chunks
from ..policy_conditions import SupportConditionsIndex, evaluate_conditions
from ..slot_schema import FilterPlan, resolve_filter_slots
from ..state import ClaimDraft, EligibilityVerdict, GraphState

_UNCERTAIN_STATUSES = {
    EvidenceStatus.UNSUPPORTED,
    EvidenceStatus.PARTIAL,
    EvidenceStatus.CONFLICT,
}

_RECHECK_TOP_K = 3

# 이 노드가 사용자 슬롯과 대조할 수 있는 조건과, 그 한국어 이름.
# 2026-09-12 갱신: 연령은 색인된 문서 metadata(age_start/age_end)로 항상
# 대조된다. 나머지 네 개(장애·성별·소득·취업)는 이제 정부24
# support_conditions(JA 코드) 사이드카가 그 정책을 구조화해 가지고 있을
# 때만 대조 가능하다(policy_conditions.evaluate_conditions() 참고) - 정책마다
# 다르므로 고정된 목록이 아니라 실행 시에 결정된다(아래 checked 리스트 참고).
_ASPECT_LABELS = {
    "age": "연령",
    "disability_status": "장애 여부",
    "gender": "성별",
    "income_bracket": "소득 수준",
    "employment_status": "취업 상태",
}


def _unknown_region_policy_ids(chunks: Iterable[RetrievedChunk]) -> set[str]:
    return {
        item.chunk.metadata["source_id"]
        for item in chunks
        if item.chunk.source_type is SourceType.SUBSIDY
        and item.chunk.metadata.get("region_scope") == RegionScope.UNKNOWN.value
        and item.chunk.metadata.get("region_names") == []
        and isinstance(item.chunk.metadata.get("source_id"), str)
    }


def determine_eligibility(
    state: GraphState,
    store: ChromaVectorStore,
    llm_client: LLMClient | None = None,
    support_conditions: SupportConditionsIndex | None = None,
) -> dict:
    """state["slots"](사용자 정보)와 state["claim_plan"](이전 노드가 전달한,
    검증 완료된 정책 정보)을 바탕으로 state["eligibility_verdicts"]를 채워
    반환한다 (partial state update).

    store: 재확인용 vectorDB 검색에 쓰는 ChromaVectorStore(또는 동일한
    ``search(...)`` 시그니처를 가진 객체 - 테스트에서는 대체 구현을 넣을 수
    있다). LangGraph 그래프 조립 시 이 store 인스턴스는 체크포인트에 저장되는
    state에 넣지 않고, ``functools.partial(determine_eligibility, store=store)``
    형태로 노드 함수에 주입한다.

    llm_client: 위반 사유 문장을 자연어로 다듬을 때만 쓰는 선택적 LLM
    클라이언트 (``src.rag_chatbot.llm.LLMClient``). None이면(기본값) LLM을
    아예 호출하지 않고 규칙이 만든 문장을 그대로 쓴다 - 판정 로직에는 영향
    없음.

    support_conditions: N4(policy_search)와 같은 정부24 JA 코드 사이드카
    (2026-09-12 추가). 성별·소득·취업·장애 여부를 이것으로 대조해서
    "확인하지 못함"을 줄인다(policy_conditions.evaluate_conditions() 참고).
    None이면(기본값) 이 네 조건은 여전히 모두 미확인으로 남는다 - N4가
    이미 같은 조건으로 후보를 거러냈더라도 이 노드는 자체적으로 다시 대조한다
    (N4 이후 N10a 재질문으로 슬롯이 바뀌었을 수 있어서 N4의 판단을 그대로
    믿지 않는다).
    """
    slots = state.get("slots", {})
    unknown_region_policies = _unknown_region_policy_ids(
        list(state.get("subsidy_chunks") or []) + list(state.get("subsidy_full_chunks") or [])
    )
    # Full slot callers share N4's subject/birth-date guard; age-only callers stay legacy.
    guarded_age = any(key in slots for key in ("age_subject", "birth_date", "age_year_based"))
    full_filter_plan: FilterPlan = resolve_filter_slots(slots, reference_date=state.get("as_of"))
    age_slots = full_filter_plan["hard"].get("birth_date", {}) if guarded_age else slots
    claims_by_policy: dict[str, list[ClaimDraft]] = defaultdict(list)
    retried_policy_ids: set[str] = set()
    for claim in state.get("claim_plan", []):
        if claim.get("doc_retry_count", 0) > 0:
            retried_policy_ids.add(claim["policy_id"])
        if claim.get("claim_type") != "eligibility":
            continue
        claims_by_policy[claim["policy_id"]].append(claim)

    # N4가 실어 보낸 전체 섹션을 쓴다(duplicate_benefit.py의 같은 최적화를
    # 그대로 따른다). 없으면(예전 계약·단위 테스트) 정책마다 재검색하는
    # 기존 경로로 떨어진다.
    #
    # 이게 필요한 이유(2026-09-11 추가): N10a(request_calc_info)가 혼인
    # 상태·임신 상태 같은 계산용 소프트 슬롯만 되물은 뒤에도 E18b가 이
    # 노드로 돌아온다(builder.py의 route_after_benefit_calculator/E18b
    # 참고 - result_assembly의 조인 조건 때문에 N9 전체를 다시 태워야
    # 한다). 그런데 이 노드가 대조하는 조건(_VERIFIABLE_ASPECTS = age뿐)은
    # 그 슬롯들과 무관해서 재검색해도 결과가 똑같다 - 매번 정책 수만큼
    # vectorDB를 다시 때리는 건 순수 낭비다(관찰된 실행에서 이 노드
    # 하나가 30초 걸렸다). full_chunks는 이미 그 정책의 전체 문서라
    # age_start/age_end 같은 문서 단위 metadata는 어느 청크로 봐도
    # 동일하므로, top_k=3 재검색 대신 써도 정보 손실이 없다.
    full_by_policy: dict[str, list] = defaultdict(list)
    for retrieved in state.get("subsidy_full_chunks") or []:
        full_by_policy[retrieved.chunk.metadata.get("source_id")].append(retrieved)

    verdicts: list[EligibilityVerdict] = []
    for policy_id, claims in claims_by_policy.items():
        relevant = [
            claim
            for claim in claims
            if EvidenceStatus(claim["status"]) is not EvidenceStatus.NOT_APPLICABLE
        ]
        if not relevant:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "verdict": "미확인",
                    "reasons": ["판정 가능한 자격 조건 근거가 없음"],
                }
            )
            continue

        statuses = {EvidenceStatus(claim["status"]) for claim in relevant}

        if statuses & _UNCERTAIN_STATUSES:
            reasons = [
                reason
                for claim in relevant
                if EvidenceStatus(claim["status"]) in _UNCERTAIN_STATUSES
                for reason in claim.get("reasons", [])
            ]
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "verdict": "미확인",
                    "reasons": reasons or ["근거가 불충분하거나 상충함"],
                }
            )
            continue

        # 여기까지 왔으면 관련 claim이 전부 SUPPORTED. 같은 정책 문서를
        # 한 번 더 확인해 구조화 자격 조건을 재확인한다 - full_chunks가
        # 있으면 그걸 쓰고(재검색 없음), 없거나 재시도된 정책이면 vectorDB를
        # 다시 검색한다(위 full_by_policy 설명 참고).
        recheck_chunks = full_by_policy.get(policy_id) or ()
        if not recheck_chunks or policy_id in retried_policy_ids:
            try:
                found = store.search(
                    SourceType.SUBSIDY,
                    f"{policy_id} 지원자격",
                    query_id=f"{state.get('query_id', 'n9')}-{policy_id}-recheck",
                    top_k=_RECHECK_TOP_K,
                    search_filter=VectorSearchFilter(metadata_equals={"source_id": policy_id}),
                )
            except CollectionNotFoundError:
                # 아직 어떤 정책도 색인되지 않은 상태 (컬렉션 자체가 없음) - 근거를
                # 찾지 못한 것과 동일하게 취급한다. 여기서 예외를 그대로 흘려보내면
                # 그래프 전체가 죽는다.
                found = ()
            recheck_chunks = merge_evidence_chunks(list(recheck_chunks), list(found))
        if not recheck_chunks:
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "verdict": "미확인",
                    "reasons": ["재검색에서 해당 정책 근거를 다시 찾지 못함"],
                }
            )
            continue

        if policy_id in _unknown_region_policy_ids(recheck_chunks):
            unknown_region_policies.add(policy_id)
        violations, checked = _find_structured_violations(recheck_chunks, age_slots)
        sc_violations, sc_checked = _find_support_condition_violations(
            policy_id, full_filter_plan, support_conditions
        )
        violations = violations + sc_violations
        checked = checked + [aspect for aspect in sc_checked if aspect not in checked]
        scope = _verification_scope(checked)
        if guarded_age and not checked and any(
            chunk.chunk.metadata.get(key) is not None
            for chunk in recheck_chunks for key in ("age_start", "age_end")
        ):
            verdicts.append({
                "policy_id": policy_id,
                "verdict": "미확인",
                "reasons": ["대상자의 나이가 확인되지 않아 연령 조건을 판정할 수 없음"],
                **scope,
            })
            continue
        if violations:
            reasons = violations if policy_id in unknown_region_policies else _naturalize_reasons(violations, llm_client)
            verdicts.append(
                {
                    "policy_id": policy_id,
                    "verdict": "미충족",
                    "reasons": reasons,
                    **scope,
                }
            )
            continue

        reasons = [reason for claim in relevant for reason in claim.get("reasons", [])]
        verdicts.append(
            {"policy_id": policy_id, "verdict": "충족", "reasons": reasons, **scope}
        )

    # Add only the unresolved region; never erase other checks or a proven violation.
    for verdict in verdicts:
        if verdict["policy_id"] not in unknown_region_policies:
            continue
        if verdict["verdict"] == "충족":
            verdict["verdict"] = "미확인"
        verdict.setdefault("unchecked", []).append("지역")
        verdict["reasons"].append("지역 조건 추가 확인 필요")
    return {"eligibility_verdicts": verdicts}


def _verification_scope(checked: list[str]) -> dict:
    """무엇을 확인했고 무엇을 확인하지 못했는지 사람이 읽을 이름으로 만든다."""

    checked_labels = [_ASPECT_LABELS[aspect] for aspect in checked]
    unchecked_labels = [
        _ASPECT_LABELS[aspect]
        for aspect in _ASPECT_LABELS
        if aspect not in checked
    ]
    return {"checked": checked_labels, "unchecked": unchecked_labels}


def _find_structured_violations(
    chunks: Iterable[RetrievedChunk], slots: dict
) -> tuple[list[str], list[str]]:
    """age_start/age_end 등 구조화 metadata와 사용자 slots를 대조해, 문서에
    명시적으로 적힌 조건과 명백히 어긋나는 경우만 위반으로 판단한다. 슬롯이나
    metadata 어느 한쪽이 없으면 비교하지 않는다 (애매한 경우 위반으로 단정하지
    않음 - 그런 경우는 호출부에서 이미 "충족"으로 이어지므로, 이후 미확인
    처리가 필요하면 이 함수가 아니라 상위 판정 규칙을 조정한다).
    """
    age_filter = VectorSearchFilter(age=slots.get("age"), year_age=slots.get("age_year_based"))
    violations: list[str] = []
    checked: list[str] = []
    if age_filter.age is None:
        # 사용자 나이를 모르면 연령 조건도 "확인했다"고 말할 수 없다.
        return violations, checked
    for retrieved in chunks:
        metadata = retrieved.chunk.metadata
        age = age_filter.age_for_basis(metadata.get("age_basis"))
        age_start = metadata.get("age_start")
        age_end = metadata.get("age_end")
        if age_start is None and age_end is None:
            # 문서에 연령 기준 자체가 없으면 비교한 것이 아니다. 이걸 "확인함"
            # 으로 세면 아무 조건도 대조하지 않고 "충족"이라고 말하게 된다.
            continue
        if "age" not in checked:
            checked.append("age")
        if age_start is not None and age < age_start:
            violations.append(
                f"연령 조건 미충족: 최소 {age_start}세부터 지원 (사용자 age={age})"
            )
        if age_end is not None and age > age_end:
            violations.append(
                f"연령 조건 미충족: 최대 {age_end}세까지 지원 (사용자 age={age})"
            )
    return violations, checked


def _find_support_condition_violations(
    policy_id: str,
    filter_plan: FilterPlan,
    support_conditions: SupportConditionsIndex | None,
) -> tuple[list[str], list[str]]:
    """policy_conditions.evaluate_conditions()로 성별·소득·취업·장애 4개
    범주를 대조한다(2026-09-12 추가).

    support_conditions가 없거나 이 정책 항목이 사이드카에 없으면(정부24
    자체가 그 범주를 안 적어둔 경우 포함) 아무것도 대조하지 못한 것으로
    본다 - _find_structured_violations와 같은 fail-open 원칙(비교 불가 = 미확인,
    위반 단정 금지).
    """
    if not support_conditions:
        return [], []
    values = support_conditions.get(policy_id)
    if values is None:
        return [], []

    evaluation = evaluate_conditions(values, filter_plan)
    violations: list[str] = []
    checked: list[str] = []
    for aspect, outcome in evaluation.items():
        if not outcome["checked"]:
            continue
        checked.append(aspect)
        if outcome["violated"]:
            violations.append(
                f"{_ASPECT_LABELS[aspect]} 조건 미충족: 정부24 지원조건 기준과 일치하지 않음"
            )
    return violations, checked


def _naturalize_reasons(rule_reasons: list[str], llm_client: LLMClient | None) -> list[str]:
    """규칙이 만든 위반 사유(예: "연령 조건 미충족: 최소 65세부터 지원
    (사용자 age=4)")를 LLM으로 자연스러운 한국어 문장으로 다듬는다.

    DRAFT(팀 확인 필요, 확정 전): 프롬프트/출력 스키마가 아직 설계 중이라
    아래 프롬프트는 임시다. 실제 서빙 모델(RunPod에 올라갈 fine-tuned
    checkpoint)이 정해지고 프롬프트 설계가 끝나면 이 함수만 교체하면 된다.

    llm_client가 없거나(None) 호출이 실패하면 규칙이 만든 원문 그대로
    돌려준다 - 판정 결과 자체는 이 함수와 무관하게 이미 확정돼 있으므로,
    LLM은 "있으면 문장이 더 자연스러워지는" 보조 역할일 뿐이다.
    """
    if llm_client is None or not rule_reasons:
        return rule_reasons

    prompt = (
        "다음은 복지 정책 자격 조건 위반 사유를 규칙 기반으로 기계적으로 "
        "생성한 문장이다. 사실 관계(숫자, 조건)는 하나도 바꾸지 말고, 표현만 "
        "자연스러운 한국어 안내 문장으로 다듬어라. 새로운 조건을 추가하거나 "
        "추측하지 마라. 문장마다 한 줄씩 출력해라.\n\n"
        + "\n".join(f"- {reason}" for reason in rule_reasons)
    )
    try:
        response = llm_client.complete(
            prompt,
            system="너는 복지 정책 안내 문장을 다듬는 보조 도구다. 사실을 절대 바꾸지 않는다.",
        )
    except LLMCallError:
        return rule_reasons

    naturalized = [line.strip("- ").strip() for line in response.splitlines() if line.strip()]
    if not naturalized:
        return rule_reasons
    return naturalized
