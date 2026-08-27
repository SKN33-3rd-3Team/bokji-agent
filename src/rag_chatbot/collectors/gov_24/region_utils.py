"""소관기관명에서 이름 기반 지역 범위를 보수적으로 추출한다.

보조금24 API에는 구조화된 지역 범위 필드가 없으므로 현재 시도 정규 명칭으로
시작하는 기관만 지역 사업으로, 명시적으로 확인한 전국 기관만 전국 사업으로
분류한다. 그 밖의 값은 전국으로 확대 해석하지 않고 ``unknown``으로 둔다.
"""

import csv
import re
from pathlib import Path


SIDO_CODE = {
    "서울특별시": "11",
    "부산광역시": "26",
    "대구광역시": "27",
    "인천광역시": "28",
    "전남광주통합특별시": "12",
    "대전광역시": "30",
    "울산광역시": "31",
    "세종특별자치시": "36",
    "경기도": "41",
    "강원특별자치도": "51",
    "충청북도": "43",
    "충청남도": "44",
    "전북특별자치도": "52",
    "경상북도": "47",
    "경상남도": "48",
    "제주특별자치도": "50",
}
SIDO_NAMES = tuple(sorted(SIDO_CODE, key=len, reverse=True))

# 기관명만으로 전국 범위를 확정할 수 있는 최소 목록이다. 공사·재단처럼
# 서비스 범위가 기관명만으로 불명확한 조직은 unknown으로 남긴다.
NATIONAL_ORGANIZATIONS = frozenset(
    {
        "교육부",
        "고용노동부",
        "국세청",
        "보건복지부",
        "해양수산부",
    }
)

_SIGUNGU_PATTERN = re.compile(
    r"(?:^|\s)([가-힣]+(?:시|군|구))(?=\s|청|$)"
)


def load_sigungu_code_table(csv_path: str | Path | None) -> dict[tuple[str, str], str]:
    """현재 시도 prefix와 일치하는 시군구 5자리 코드만 읽는다."""
    if not csv_path or not Path(csv_path).exists():
        return {}
    table: dict[tuple[str, str], str] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            sido = row.get("시도명")
            sigungu = row.get("시군구명")
            code = str(row.get("법정동코드") or "").strip()[:5]
            current_prefix = SIDO_CODE.get(sido or "")
            if (
                sido
                and sigungu
                and current_prefix
                and code.isdigit()
                and code.startswith(current_prefix)
            ):
                table[(sido, sigungu)] = code
    return table


def _unknown_region() -> dict:
    return {
        "region_scope": "unknown",
        "region_names": [],
        "sido": None,
        "sigungu": None,
        "sido_code": None,
        "sigungu_code": None,
    }


def extract_region(org_name: str | None, sigungu_code_table: dict | None = None) -> dict:
    """기관명에서 이름 기반 지역 계약과 선택적 보조 코드를 만든다."""
    if not org_name or not org_name.strip():
        return _unknown_region()

    normalized_org = " ".join(org_name.split())
    if normalized_org == "전국" or normalized_org in NATIONAL_ORGANIZATIONS:
        return {
            "region_scope": "national",
            "region_names": ["전국"],
            "sido": None,
            "sigungu": None,
            "sido_code": None,
            "sigungu_code": None,
        }

    for sido in SIDO_NAMES:
        if not normalized_org.startswith(sido):
            continue

        rest = normalized_org[len(sido) :].strip()
        sigungu_parts = _SIGUNGU_PATTERN.findall(rest)
        if len(sigungu_parts) > 2 or (
            len(sigungu_parts) == 2
            and not (sigungu_parts[0].endswith("시") and sigungu_parts[1].endswith("구"))
        ):
            return _unknown_region()

        region_names = [sido]
        for depth in range(1, len(sigungu_parts) + 1):
            region_names.append(f"{sido} {' '.join(sigungu_parts[:depth])}")
        sigungu = " ".join(sigungu_parts) if sigungu_parts else None
        sigungu_code = None
        if sigungu and sigungu_code_table:
            candidate = sigungu_code_table.get((sido, sigungu))
            if isinstance(candidate, str) and candidate.startswith(SIDO_CODE[sido]):
                sigungu_code = candidate

        return {
            "region_scope": "regional",
            "region_names": region_names,
            "sido": sido,
            "sigungu": sigungu,
            "sido_code": SIDO_CODE[sido],
            "sigungu_code": sigungu_code,
        }

    return _unknown_region()
