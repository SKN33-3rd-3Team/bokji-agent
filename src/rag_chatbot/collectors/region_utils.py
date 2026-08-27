"""소관기관명 텍스트에서 지역(시도/시군구)을 추출한다.

보조금24 API에는 구조화된 지역코드 필드가 없어서, 소관기관명 텍스트를
정규식으로 파싱한다. 완전한 정확도는 아니며, 시도 이름으로 시작하지 않는
경우(중앙부처 등)는 "전국" 사업으로 간주한다.
"""

import re

SIDO_LIST = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시",
    "경기도", "강원특별자치도", "강원도",
    "충청북도", "충청남도",
    "전북특별자치도", "전라북도", "전라남도",
    "경상북도", "경상남도", "제주특별자치도", "제주도",
]

# 시도 이름 뒤에 오는 시/군/구 단위 토큰
_SIGUNGU_PATTERN = re.compile(r"([가-힣]+(?:시|군|구))")


def extract_region(org_name: str | None) -> dict:
    """소관기관명에서 지역 정보를 뽑아 dict로 반환한다.

    반환값: {"sido": str|None, "sigungu": str|None, "region_label": str}
    매칭되는 시도가 없으면 중앙부처 사업으로 보고 region_label="전국"을 준다.
    """
    if not org_name:
        return {"sido": None, "sigungu": None, "region_label": "전국"}

    for sido in SIDO_LIST:
        if org_name.startswith(sido):
            rest = org_name[len(sido):].strip()
            match = _SIGUNGU_PATTERN.search(rest)
            sigungu = match.group(1) if match else None
            label = f"{sido} {sigungu}" if sigungu else sido
            return {"sido": sido, "sigungu": sigungu, "region_label": label}

    return {"sido": None, "sigungu": None, "region_label": "전국"}
