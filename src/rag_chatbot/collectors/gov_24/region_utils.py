"""소관기관명 텍스트에서 지역(시도/시군구)을 추출한다.

보조금24 API에는 구조화된 지역코드 필드가 없어서, 소관기관명 텍스트를
정규식으로 파싱한다. 완전한 정확도는 아니며, 시도 이름으로 시작하지 않는
경우(중앙부처 등)는 "전국" 사업으로 간주한다.

시군구 5자리 법정동코드는 개수가 많고(약 250개) 정확성이 중요해서 이 파일에
직접 하드코딩하지 않는다. data.go.kr의 "전국 법정동코드 전체자료" CSV를
`load_sigungu_code_table()`로 불러와서 쓰고, CSV가 없으면 시도 2자리 코드까지만
채워진다.
"""

import csv
import re
from pathlib import Path

SIDO_LIST = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시",
    "경기도", "강원특별자치도", "강원도",
    "충청북도", "충청남도",
    "전북특별자치도", "전라북도", "전라남도",
    "경상북도", "경상남도", "제주특별자치도", "제주도",
]

# 행정표준코드관리시스템 기준 시도 2자리 코드(법정동코드 앞 2자리). 시도는
# 개수가 적고(17개) 코드가 안정적이라 여기 직접 넣지만, 반영 전에
# data.go.kr "전국 법정동코드 전체자료"와 한 번 더 대조할 것을 권장한다.
SIDO_CODE = {
    "서울특별시": "11", "부산광역시": "26", "대구광역시": "27", "인천광역시": "28",
    "광주광역시": "29", "대전광역시": "30", "울산광역시": "31", "세종특별자치시": "36",
    "경기도": "41", "강원특별자치도": "42", "강원도": "42",
    "충청북도": "43", "충청남도": "44",
    "전북특별자치도": "45", "전라북도": "45", "전라남도": "46",
    "경상북도": "47", "경상남도": "48", "제주특별자치도": "50", "제주도": "50",
}

# 시도 이름 뒤에 오는 시/군/구 단위 토큰
_SIGUNGU_PATTERN = re.compile(r"([가-힣]+(?:시|군|구))")


def load_sigungu_code_table(csv_path: str | Path | None) -> dict[tuple[str, str], str]:
    """(시도명, 시군구명) -> 5자리 법정동코드 매핑을 외부 CSV에서 불러온다.

    CSV는 최소한 "시도명", "시군구명", "법정동코드" 헤더를 가져야 한다
    (data.go.kr "전국 법정동코드 전체자료" 기준). csv_path가 없거나 파일이
    없으면 빈 dict를 반환하고, 이 경우 시군구 코드는 None으로 남는다 —
    부정확한 코드를 이 파일 안에 직접 하드코딩하지 않기 위해서다.
    """
    if not csv_path or not Path(csv_path).exists():
        return {}
    table: dict[tuple[str, str], str] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sido = row.get("시도명")
            sigungu = row.get("시군구명")
            code = row.get("법정동코드")
            if sido and sigungu and code:
                table[(sido, sigungu)] = code[:5]
    return table


def extract_region(org_name: str | None, sigungu_code_table: dict | None = None) -> dict:
    """소관기관명에서 지역 정보를 뽑아 dict로 반환한다.

    반환값: {"sido", "sigungu", "region_label", "sido_code", "sigungu_code"}
    매칭되는 시도가 없으면 중앙부처 사업으로 보고 region_label="전국"을 준다.
    sigungu_code_table을 넘기지 않으면(또는 매칭이 없으면) sigungu_code는
    None이다 — 부정확한 코드를 만들어내지 않기 위해서다.
    """
    empty = {
        "sido": None, "sigungu": None, "region_label": "전국",
        "sido_code": None, "sigungu_code": None,
    }
    if not org_name:
        return dict(empty)

    for sido in SIDO_LIST:
        if org_name.startswith(sido):
            rest = org_name[len(sido):].strip()
            match = _SIGUNGU_PATTERN.search(rest)
            sigungu = match.group(1) if match else None
            label = f"{sido} {sigungu}" if sigungu else sido
            sigungu_code = None
            if sigungu and sigungu_code_table:
                sigungu_code = sigungu_code_table.get((sido, sigungu))
            return {
                "sido": sido,
                "sigungu": sigungu,
                "region_label": label,
                "sido_code": SIDO_CODE.get(sido),
                "sigungu_code": sigungu_code,
            }

    return dict(empty)
