"""API-09(검색 조건 옵션 조회)의 데이터 소스.

``streamlit_ui/constants.py``의 상수를 그대로 import해서 재노출한다 -
코드값-라벨 매핑을 이 파일에 다시 선언하지 않는다. 이 상수들은 반드시
``src/rag_chatbot/graph/slot_schema.py``의 Enum과 1:1로 맞아야 하며(그
동기화는 ``streamlit_ui/constants.py``가 이미 책임지고 있음), 여기서는
그 값을 그대로 옮기기만 한다(PROJECT_STRUCTURE.md 2.2절 "app/core/options.py"
참고).
"""

from __future__ import annotations

from streamlit_ui.constants import (
    DEFAULT_TOP_K,
    DISABILITY_LABELS_KO,
    GENDER_LABELS_KO,
    HOUSEHOLD_TYPE_LABELS_KO,
    INCOME_BRACKET_LABELS_KO,
    INTEREST_FIELD_OPTIONS,
    INTEREST_OPTIONS,
    SIDO_OPTIONS,
    SIGNUP_INTEREST_OPTIONS,
    VETERAN_LABELS_KO,
)


def _code_label_pairs(labels: dict[str, str]) -> list[dict[str, str]]:
    return [{"code": code, "label": label} for code, label in labels.items()]


def build_search_options() -> dict:
    """API-09 응답 바디를 조립한다."""

    return {
        "sido_options": list(SIDO_OPTIONS),
        "gender_options": _code_label_pairs(GENDER_LABELS_KO),
        "disability_status_options": _code_label_pairs(DISABILITY_LABELS_KO),
        "veteran_status_options": _code_label_pairs(VETERAN_LABELS_KO),
        "income_bracket_options": _code_label_pairs(INCOME_BRACKET_LABELS_KO),
        "household_type_options": _code_label_pairs(HOUSEHOLD_TYPE_LABELS_KO),
        "signup_interest_options": list(SIGNUP_INTEREST_OPTIONS),
        "sidebar_interest_options": list(INTEREST_OPTIONS),
        "interest_field_options": list(INTEREST_FIELD_OPTIONS),
        "default_top_k": DEFAULT_TOP_K,
    }
