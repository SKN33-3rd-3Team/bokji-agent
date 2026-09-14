"""정부24 지원조건 raw sidecar를 N4 정책 후보에 보수적으로 적용한다."""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias

from rag_design.contracts import RetrievedChunk

from .slot_schema import FilterPlan


_GENDER_CODES = frozenset({"JA0101", "JA0102"})
_INCOME_CODES = frozenset({"JA0201", "JA0202", "JA0203", "JA0204", "JA0205"})
_EMPLOYMENT_CODES = frozenset({"JA0326", "JA0327"})
_DISABILITY_CODES = frozenset({"JA0328"})
_UNMAPPED_EMPLOYMENT_CODES = frozenset(
    {
        "JA0313",
        "JA0314",
        "JA0315",
        "JA0316",
        "JA0317",
        "JA0318",
        "JA0319",
        "JA0320",
        "JA0322",
        "JA1101",
        "JA1102",
        "JA1103",
    }
)
_CATEGORY_CODE_SETS = (
    _GENDER_CODES,
    _INCOME_CODES,
    _DISABILITY_CODES,
)

_GENDER_SLOT_CODES = {
    "male": frozenset({"JA0101"}),
    "female": frozenset({"JA0102"}),
}
# 2026-09-11 수정: "self_employed"/"student"/"not_working"이 빠져 있었다.
# 매핑이 없으면 _category_matches가 accepted=None으로 보고 무조건 통과시키는데(즉
# "재직자만" 정책도 걸러지지 않고 다 보임), 이 세 상태는 사용자가 분명히 밝힌
# 값이라 "판단 불가(UNKNOWN)"와 같은 취급을 받으면 안 된다. 이 세 상태는
# JA0326(재직)/JA0327(구직) 어느 쪽도 만족하지 않는다고 보고 빈 집합으로
# 매핑한다 - "재직자만"/"구직자만"처럼 조건이 걸린 정책은 정상적으로 제외되고,
# 조건이 없는 정책은 그대로 통과한다(_category_matches의 not active 분기).
_EMPLOYMENT_SLOT_CODES = {
    "employed": frozenset({"JA0326"}),
    "job_seeking": frozenset({"JA0327"}),
    "self_employed": frozenset(),
    "student": frozenset(),
    "not_working": frozenset(),
}
# 2026-09-11 수정: "not_registered"(비장애인)가 빠져 있었다. 그 결과 사용자가
# 비장애인이라고 분명히 밝혀도 매핑이 없어 accepted=None이 되고, "장애인만"
# (JA0328=Y) 정책까지 전부 통과해버렸다(실사용 중 확인 - 비장애인이라고 답했는데
# 장애인 전용 정책이 검색 결과에 그대로 뜸). "registered"만 있던 이전 버전은
# "장애인에게 장애인 전용 정책을 보여준다"만 보장했지, "비장애인에게 장애인 전용
# 정책을 숨긴다"는 보장하지 않았다 - 빈 집합을 매핑해 후자도 보장한다.
_DISABILITY_SLOT_CODES = {
    "registered": frozenset({"JA0328"}),
    "not_registered": frozenset(),
}

# 2026-09-11: 위 두 dict는 JA 코드가 있을 때만 쓴다. 코드가 전부 결측이면
# (즉 "활성 코드 없음" -> _category_matches가 fail-open) 이 보완
# 규칙이 개입한다. 정부24 정책명은 마케팅 문구가 아니라 공식 서비스명이라
# 키워드 신뢰도가 높다고 보지만, 이미 적격으로 판단된 사람을 새로 끼워넣는 데는
# 절대 쓰지 않고, 이미 적격이 아니라고 확정된 사람을 배제하는 데만 쓴다
# (matches_profile의 해당 분기만을 참고).
_DISABILITY_TITLE_FALLBACK_MARKERS = ("장애인",)
_SELF_EMPLOYED_TITLE_FALLBACK_MARKERS = ("자영업자", "프리랜서")


def _title_signals_restriction(title: object, markers: tuple[str, ...]) -> bool:
    return isinstance(title, str) and any(marker in title for marker in markers)


# 사용자 소득 구간과 정부24 구간의 수치 범위가 겹치는 코드. ``over_150``은
# 151~200%와 200% 초과 양쪽에 걸치므로 두 코드를 OR로 허용한다.
_INCOME_CODES_BY_RANK = {
    0: frozenset({"JA0201"}),
    1: frozenset({"JA0201"}),
    2: frozenset({"JA0201", "JA0202"}),
    3: frozenset({"JA0202", "JA0203"}),
    4: frozenset({"JA0203", "JA0204"}),
    5: frozenset({"JA0204", "JA0205"}),
}

SupportConditionValues: TypeAlias = Mapping[str, str | None]
SupportConditionsIndex: TypeAlias = Mapping[str, SupportConditionValues]
_LOAD_WARNING_EMITTED = False


def _warn_sidecar_unavailable() -> None:
    global _LOAD_WARNING_EMITTED
    if _LOAD_WARNING_EMITTED:
        return
    warnings.warn(
        "정부24 지원조건 sidecar를 읽지 못해 프로필 후처리를 생략합니다.",
        RuntimeWarning,
        stacklevel=2,
    )
    _LOAD_WARNING_EMITTED = True


def _warn_sidecar_quality(
    *, accepted: int, dropped: int, duplicates: int, degraded_categories: int
) -> None:
    global _LOAD_WARNING_EMITTED
    if _LOAD_WARNING_EMITTED:
        return
    warnings.warn(
        "정부24 지원조건 sidecar 일부를 fail-open 처리했습니다: "
        f"accepted={accepted}, dropped={dropped}, duplicates={duplicates}, "
        f"degraded_categories={degraded_categories}",
        RuntimeWarning,
        stacklevel=2,
    )
    _LOAD_WARNING_EMITTED = True


def _source_id(row: object) -> str | None:
    if not isinstance(row, Mapping):
        return None
    value = row.get("서비스ID")
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def load_support_conditions(path: str | Path) -> dict[str, dict[str, str | None]]:
    """canonical flat row와 기존 exact wrapper를 ``서비스ID`` index로 읽는다.

    파일·JSON·wrapper·값 계약이 불명확한 경우 예외를 서비스 시작까지 전파하지
    않고 해당 서비스 조건을 싣지 않는다. 조건이 없는 서비스는 N4에서 그대로
    통과하므로 이 동작은 모두 fail-open이다.
    """

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _warn_sidecar_unavailable()
        return {}
    if not isinstance(payload, list):
        _warn_sidecar_unavailable()
        return {}

    indexed: dict[str, dict[str, str | None]] = {}
    seen: set[str] = set()
    blocked: set[str] = set()
    dropped = 0
    duplicates = 0
    degraded_categories = 0

    for item in payload:
        row = item
        if isinstance(item, Mapping) and "data" in item:
            data = item.get("data")
            if not isinstance(data, list):
                dropped += 1
                continue
            counts_are_exact = all(
                not isinstance(item.get(name), bool)
                and isinstance(item.get(name), int)
                and item.get(name) == 1
                for name in ("matchCount", "currentCount")
            )
            if not counts_are_exact or len(data) != 1:
                # 잘못된 응답에 포함된 ID도 이후 정상 row로 덮어쓰지 않는다.
                dropped += max(1, len(data))
                for candidate in data:
                    source_id = _source_id(candidate)
                    if source_id is not None:
                        blocked.add(source_id)
                        indexed.pop(source_id, None)
                continue
            row = data[0]

        source_id = _source_id(row)
        if source_id is None:
            dropped += 1
            continue
        if source_id in seen or source_id in blocked:
            duplicates += 1
            blocked.add(source_id)
            indexed.pop(source_id, None)
            continue
        seen.add(source_id)

        # 현재 공식 raw 계약은 categorical JA 값이 정확히 "Y" 또는 null이다.
        # 누락·다른 token은 해당 범주만 싣지 않아 범주 단위로 fail-open한다.
        values: dict[str, str | None] = {}
        for codes in _CATEGORY_CODE_SETS:
            if all(
                code in row and row[code] in ("Y", None)
                for code in codes
            ):
                values.update({code: row[code] for code in codes})
            else:
                degraded_categories += 1

        employment_codes = _EMPLOYMENT_CODES | _UNMAPPED_EMPLOYMENT_CODES
        if all(
            code in row and row[code] in ("Y", None)
            for code in employment_codes
        ):
            values.update({code: row[code] for code in employment_codes})
        else:
            degraded_categories += 1

        # 2026-09-11: JA0328/JA0326/JA0327 등이 전부 결측이면 위 코드는
        # "활성 코드 없음 -> fail-open"으로 무조건 통과시킨다. 그런데
        # "남성장애인 출산비용 지원"처럼 정책명 자체가 대상을 못박는데도
        # 코드가 비어 있는 경우가 실사용 중 확인됐다(제목에 "장애인"이 있는
        # 621건 중 98건/16%가 JA0328 전부 None). matches_profile의 보수적
        # 보완 규칙이 참고할 수 있게 title을 얹어둔다 - "JA"로 시작하지
        # 않는 키라 _active_codes/_category_matches가 순회하는 codes
        # frozenset과는 절대 겹치지 않는다.
        title = row.get("서비스명")
        if isinstance(title, str) and title:
            values["_title"] = title
        indexed[source_id] = values

    if dropped or duplicates or degraded_categories or not indexed:
        _warn_sidecar_quality(
            accepted=len(indexed),
            dropped=dropped,
            duplicates=duplicates,
            degraded_categories=degraded_categories,
        )
    return indexed


def _active_codes(
    values: SupportConditionValues, codes: frozenset[str]
) -> frozenset[str] | None:
    if any(code not in values or values[code] not in ("Y", None) for code in codes):
        return None
    return frozenset(code for code in codes if values[code] == "Y")


def _category_matches(
    values: SupportConditionValues,
    codes: frozenset[str],
    accepted: frozenset[str] | None,
) -> bool:
    active = _active_codes(values, codes)
    # 원천 결측/unknown, 사용자 슬롯 미확정, 정책 조건 없음은 모두 fail-open.
    return active is None or accepted is None or not active or bool(active & accepted)


def evaluate_conditions(
    values: SupportConditionValues, filter_plan: FilterPlan
) -> dict[str, dict[str, bool]]:
    """성별/소득수준/취업상태/장애여부 4개 범주를 각각 "실제로 대조했는지"
    (checked)와 "위반했는지"(violated)로 따로 반환한다.

    2026-09-12 추가: matches_profile()은 "전체적으로 통과했는가"만 한 줄로
    답하지만, N9(eligibility_verdict)는 "무엇을 확인했다고 말해도
    되는가"를 정책·범주 단위로 알아야 한다("장애 여부, 성별, 소득 수준,
    취업 상태를 확인하지 못했다"가 실제로는 확인 가능한 경우에도 항상 뜨는
    문제를 고치기 위함). matches_profile은 이 함수의 결과를 모아 "위반이
    하나라도 있는가"로 압축한 것뿐이다 - 판단 기준은 완전히 같다.

    "checked"의 기준은 보수적이다: 코드가 결측이거나(active가 None/빈
    집합) 사용자 값을 모르면(accepted가 None) "확인 안 함"이다. 코드가
    침묵할 때만 개입하는 제목 키워드 보완 규칙이 실제로 배제를 발동시킨
    경우는 "확인함 + 위반"으로 센다 - 그 경우는 사람이 읽을 수 있는 근거
    (제목)가 있어서다.
    """

    hard = filter_plan["hard"]
    soft = filter_plan["soft"]
    result: dict[str, dict[str, bool]] = {}

    gender = soft.get("gender", {}).get("equals")
    gender_active = _active_codes(values, _GENDER_CODES)
    gender_accepted = (
        _GENDER_SLOT_CODES.get(gender) if isinstance(gender, str) else None
    )
    gender_checked = bool(gender_active) and gender_accepted is not None
    result["gender"] = {
        "checked": gender_checked,
        "violated": gender_checked and not bool(gender_active & gender_accepted),
    }

    income_rank = hard.get("income_bracket", {}).get("max_bracket_rank")
    income_active = _active_codes(values, _INCOME_CODES)
    income_accepted = (
        _INCOME_CODES_BY_RANK.get(income_rank)
        if isinstance(income_rank, int) and not isinstance(income_rank, bool)
        else None
    )
    income_checked = bool(income_active) and income_accepted is not None
    result["income_bracket"] = {
        "checked": income_checked,
        "violated": income_checked and not bool(income_active & income_accepted),
    }

    employment = hard.get("employment_status", {}).get("equals")
    unmapped_employment = _active_codes(values, _UNMAPPED_EMPLOYMENT_CODES)
    employment_active = _active_codes(values, _EMPLOYMENT_CODES)
    employment_accepted = (
        _EMPLOYMENT_SLOT_CODES.get(employment) if isinstance(employment, str) else None
    )
    if unmapped_employment != frozenset():
        # 미매핑 JA03/JA11 코드가 하나라도 active면 그 의미를 역추정하지
        # 않는다(matches_profile과 동일 원칙) - 이 범주는 대조했다고
        # 말할 수 없다.
        result["employment_status"] = {"checked": False, "violated": False}
    else:
        employment_checked = (
            bool(employment_active) and employment_accepted is not None
        )
        if employment_checked:
            result["employment_status"] = {
                "checked": True,
                "violated": not bool(employment_active & employment_accepted),
            }
        elif (
            not employment_active
            and employment in ("employed", "job_seeking", "student", "not_working")
            and _title_signals_restriction(
                values.get("_title"), _SELF_EMPLOYED_TITLE_FALLBACK_MARKERS
            )
        ):
            result["employment_status"] = {"checked": True, "violated": True}
        else:
            result["employment_status"] = {"checked": False, "violated": False}

    disability = soft.get("disability_status", {}).get("equals")
    disability_active = _active_codes(values, _DISABILITY_CODES)
    disability_accepted = (
        _DISABILITY_SLOT_CODES.get(disability) if isinstance(disability, str) else None
    )
    disability_checked = bool(disability_active) and disability_accepted is not None
    if disability_checked:
        result["disability_status"] = {
            "checked": True,
            "violated": not bool(disability_active & disability_accepted),
        }
    elif (
        disability == "not_registered"
        and not disability_active
        and _title_signals_restriction(
            values.get("_title"), _DISABILITY_TITLE_FALLBACK_MARKERS
        )
    ):
        result["disability_status"] = {"checked": True, "violated": True}
    else:
        result["disability_status"] = {"checked": False, "violated": False}

    return result


def matches_profile(
    values: SupportConditionValues, filter_plan: FilterPlan
) -> bool:
    """같은 범주는 OR, 서로 다른 범주는 AND로 확정 조건만 대조한다.

    2026-09-12: evaluate_conditions()의 결과를 모아 "위반이 하나라도
    있는가"로 압축하는 것으로 재작성했다 - 판단 기준은 그대로다.
    """

    return not any(
        aspect["violated"]
        for aspect in evaluate_conditions(values, filter_plan).values()
    )


PolicyUserTypeIndex: TypeAlias = Mapping[str, frozenset[str]]

# 이 앱의 사용자는 항상 개인(또는 그 가구)이다. "가구"도 포함하는
# 이유: 가구 단위 지원(예: 다둥이 가구 지원금)이 개인 질문으로 들어오는
# 경우가 있어 "개인"만 인정하면 오히려 오판한다. "법인/시설/단체", "소상공인"
# 등 순수 기업/사업자 전용 정책만 막는다.
_INDIVIDUAL_USER_TYPES = frozenset({"개인", "가구"})


def load_policy_user_types(path: str | Path) -> dict[str, frozenset[str]]:
    """처리된 subsidy 코퍼스(jsonl)에서 source_id -> 사용자구분 집합을 읽는다.

    사용자구분("법인/시설/단체", "소상공인" 등)은 JA 코드
    sidecar(gov24_support_conditions.json)에는 없고 처리된 문서 metadata에만
    있다. 사용자구분이 "개인"도 "가구"도 전혀 포함하지 않는(순수
    법인/시설/단체/소상공인 전용) 정책을 개인 사용자에게는 거르는 데 쓴다
    - 실측 10,968건 중 1,015건이 여기 해당한다(2026-09-11, "장애인고용
    장려금지원"이 개인 질문에 뜨는 것을 보고 발견).

    파일이 없거나 데이터가 깨진 건은 조용히 건너뛴다(fail-open) - 이
    값이 없는 정책은 이 필터를 적용하지 않는다(기존 conditions=None
    fail-open과 같은 원칙).
    """

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}

    indexed: dict[str, frozenset[str]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, Mapping):
            continue
        source_id = doc.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            continue
        metadata = doc.get("metadata")
        raw = metadata.get("user_type") if isinstance(metadata, Mapping) else None
        if isinstance(raw, str) and raw:
            indexed[source_id] = frozenset(part for part in raw.split("||") if part)
    return indexed


def _is_individual_applicable(user_types: frozenset[str] | None) -> bool:
    if not user_types:
        return True
    return bool(user_types & _INDIVIDUAL_USER_TYPES)


def filter_candidates(
    candidates: Sequence[RetrievedChunk],
    conditions: SupportConditionsIndex | None,
    filter_plan: FilterPlan,
    *,
    user_types: PolicyUserTypeIndex | None = None,
) -> tuple[RetrievedChunk, ...]:
    """semantic 후보를 canonical ``metadata.source_id``로 후처리한다."""

    kept: list[RetrievedChunk] = []
    for candidate in candidates:
        source_id = candidate.chunk.metadata.get("source_id")
        # 2026-09-11: 사용자구분은 JA 코드 sidecar와 무관한 구조적 조건이라
        # conditions 유무와 상관없이 먼저 거른다("장애인고용장려금지원"
        # 같은 기업 전용 정책이 conditions=None이라도 개인 사용자에게
        # 그대로 뜨는 일을 막는다).
        if user_types and isinstance(source_id, str):
            if not _is_individual_applicable(user_types.get(source_id)):
                continue
        if not conditions:
            kept.append(candidate)
            continue
        values = conditions.get(source_id) if isinstance(source_id, str) else None
        if values is None or matches_profile(values, filter_plan):
            kept.append(candidate)
    return tuple(kept)
