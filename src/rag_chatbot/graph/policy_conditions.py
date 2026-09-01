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
_EMPLOYMENT_SLOT_CODES = {
    "employed": frozenset({"JA0326"}),
    "job_seeking": frozenset({"JA0327"}),
}
_DISABILITY_SLOT_CODES = {"registered": frozenset({"JA0328"})}
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
    """raw response wrapper 배열을 exact ``서비스ID`` index로 읽는다.

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

    for wrapper in payload:
        data = wrapper.get("data") if isinstance(wrapper, Mapping) else None
        if not isinstance(data, list):
            dropped += 1
            continue
        counts_are_exact = all(
            not isinstance(wrapper.get(name), bool)
            and isinstance(wrapper.get(name), int)
            and wrapper.get(name) == 1
            for name in ("matchCount", "currentCount")
        )
        if not counts_are_exact or len(data) != 1:
            # 잘못된 응답에 포함된 ID도 이후 정상 wrapper로 덮어쓰지 않는다.
            dropped += max(1, len(data))
            for row in data:
                source_id = _source_id(row)
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


def matches_profile(
    values: SupportConditionValues, filter_plan: FilterPlan
) -> bool:
    """같은 범주는 OR, 서로 다른 범주는 AND로 확정 조건만 대조한다."""

    hard = filter_plan["hard"]
    soft = filter_plan["soft"]

    gender = soft.get("gender", {}).get("equals")
    if not _category_matches(
        values,
        _GENDER_CODES,
        _GENDER_SLOT_CODES.get(gender) if isinstance(gender, str) else None,
    ):
        return False

    income_rank = hard.get("income_bracket", {}).get("max_bracket_rank")
    if not _category_matches(
        values,
        _INCOME_CODES,
        _INCOME_CODES_BY_RANK.get(income_rank)
        if isinstance(income_rank, int) and not isinstance(income_rank, bool)
        else None,
    ):
        return False

    employment = hard.get("employment_status", {}).get("equals")
    unmapped_employment = _active_codes(
        values, _UNMAPPED_EMPLOYMENT_CODES
    )
    # 미매핑 JA03/JA11 조건이 하나라도 active면 그 의미를 역추정하지 않는다.
    # exact code가 비일치하더라도 같은 범주의 다른 OR 조건일 수 있어 keep한다.
    if (
        unmapped_employment == frozenset()
        and not _category_matches(
            values,
            _EMPLOYMENT_CODES,
            _EMPLOYMENT_SLOT_CODES.get(employment)
            if isinstance(employment, str)
            else None,
        )
    ):
        return False

    disability = soft.get("disability_status", {}).get("equals")
    return _category_matches(
        values,
        _DISABILITY_CODES,
        _DISABILITY_SLOT_CODES.get(disability)
        if isinstance(disability, str)
        else None,
    )


def filter_candidates(
    candidates: Sequence[RetrievedChunk],
    conditions: SupportConditionsIndex | None,
    filter_plan: FilterPlan,
) -> tuple[RetrievedChunk, ...]:
    """semantic 후보를 canonical ``metadata.source_id``로 후처리한다."""

    if not conditions:
        return tuple(candidates)

    kept: list[RetrievedChunk] = []
    for candidate in candidates:
        source_id = candidate.chunk.metadata.get("source_id")
        values = conditions.get(source_id) if isinstance(source_id, str) else None
        if values is None or matches_profile(values, filter_plan):
            kept.append(candidate)
    return tuple(kept)
