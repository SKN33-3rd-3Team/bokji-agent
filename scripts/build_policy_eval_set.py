"""정답 정책이 100종인 검증 세트를 색인 전체에서 생성한다.

왜 필요한가
-----------
기존 ``data/evaluation/dev_questions.jsonl``은 150문항이지만 **정답 정책이
5개뿐**이다. 색인에는 10,968개가 있다. 그래서 거기서 나오는 Recall@5는
"10,968개 중에 맞는 걸 고르는가"가 아니라 사실상 5지선다 점수다. 어떤 수정을
하든 그 5개에 유리하게 맞춰질 수 있고, 실제 난이도를 전혀 반영하지 않는다.
이 스크립트는 그 문제 하나를 고친다: **정책 100종, 질문 100건.**

무엇을 보장하는가
-----------------
자동 생성한 질문의 정답이 "검색만 잘하면 뜨는" 상태여야 검색을 잰 것이 된다.
그래서 정책을 고를 때 파이프라인이 검색 **뒤에** 거는 관문을 전부 미리
통과시킨다 - 하나라도 못 넘으면 그 정책은 버리고 다음 후보로 넘어간다:

    1. ``_is_individual_applicable``      개인/가구 대상인가
    2. ``validate_region_metadata``       region 메타데이터가 유효한가
    3. ``subsidy_regions_match``          그 사용자 지역에서 살아남는가
    4. 연령 메타데이터                     age_start~age_end 안에 드는가
    5. ``evaluate_conditions``            지원조건(JA코드) 위반이 없는가

프로필(지역·생년·소득·장애·취업)은 후보 사다리를 돌려가며 5번을 실제로
통과하는 조합을 찾는다. 못 찾으면 그 정책은 제외한다 - 지어내지 않는다.

질문 형식
---------
한 가지 틀로 100건을 찍으면 그 틀에 맞춘 수정이 또 과적합한다. 그래서
dev-form 세트와 같은 축으로 형식을 6가지로 돌린다(단문 / 상황서술 / 반말 /
불릿 / 다중질문 / 마지막 문장이 일반 문형). 인적사항은 질문 본문에 넣지
않는다 - 되묻기로 받는다.

제도명 노출
-----------
질문에 제도명을 그대로 쓰면 제목과의 어휘 일치만으로 검색이 쉬워진다. 반대로
늘 지우면 실제보다 어려워진다 - 사용자는 제도명을 알고 오기도 한다. 그래서
형식 6종 중 3종은 제도명을 쓰고, 3종은 문서에서 **제목 어휘가 안 섞인
문장**을 골라 쓴다(목적 요약 -> 지원내용 -> 지원대상 -> 선정기준 순).
초기 구현은 요약문에서 제목 단어를 삭제했는데 "를 입은 사회적 약자에게 및
법률자문 지원" 같은 비문이 나와 폐기했다.

고른 문장은 두 관문을 더 넘어야 한다. (1) 딴 데를 가리키기만 하는 줄이
아닐 것("아래 선정기준을 동시에 충족하여야 함"), (2) 자격 나열만이 아닐 것
("중위소득 60% 이하, 국민기초생활 수급권자"). 둘 다 주제어가 없어서 어떤
검색기도 맞힐 수 없고, 그런 항목을 넣으면 검색기가 아니라 벤치마크를 재게
된다. 쓸 만한 문장이 하나도 없으면 제도명 쪽으로 되돌리고 그 사실을
``names_program`` 플래그에 남긴다. 결과를 이 플래그로 갈라 보면 "제도명을
알 때"와 "모를 때"의 성능을 따로 읽을 수 있다.

한계(정직하게)
--------------
- 질문이 정책 문서의 요약 문장에서 파생되므로 어휘가 겹친다. ``names_program
  =false`` 쪽도 완전히 독립적인 표현은 아니다. 이 세트의 절대 점수를
  "실사용자 성능"으로 읽으면 안 된다. 서로 다른 설정을 **비교**하는 데 쓴다.
- 정답을 1개로 둔다. 실제로는 같은 필요에 맞는 정책이 여럿일 수 있어
  Recall@5가 과소평가될 수 있다.
- 보류(should_abstain) 항목은 만들지 않는다. 그건 별도 설계가 필요하다.

실행:
    python scripts/build_policy_eval_set.py
    python scripts/build_policy_eval_set.py --count 100 --seed 20260915
    python scripts/build_policy_eval_set.py --show 10
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rag_design.contracts import validate_region_metadata  # noqa: E402
from rag_design.index_policy import subsidy_regions_match  # noqa: E402
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
)

# 정식 시도명 -> 사용자가 말하는 짧은 이름. slot_answers 문장은 N1 규칙
# 파서가 읽으므로 짧은 이름을 써야 한다.
_SIDO_SHORT: tuple[tuple[str, str], ...] = (
    ("서울특별시", "서울"),
    ("부산광역시", "부산"),
    ("대구광역시", "대구"),
    ("인천광역시", "인천"),
    ("대전광역시", "대전"),
    ("울산광역시", "울산"),
    ("세종특별자치시", "세종"),
    ("경기도", "경기"),
    ("강원특별자치도", "강원"),
    ("충청북도", "충북"),
    ("충청남도", "충남"),
    ("전북특별자치도", "전북"),
    ("전남광주통합특별시", "전남"),
    ("경상북도", "경북"),
    ("경상남도", "경남"),
    ("제주특별자치도", "제주"),
)
_CANONICAL_BY_SHORT = {short: canon for canon, short in _SIDO_SHORT}

# 제도명에서 걷어내도 의미가 남는 일반어. 이것만 남으면 제목을 지운 효과가
# 없으므로 need 문장 생성에 쓰지 않는다.
_GENERIC_TITLE_WORDS = {
    "지원", "사업", "제도", "서비스", "보급", "운영", "관리", "지원사업",
    "및", "등", "대상", "신청", "추진", "센터", "지원금", "보조", "융자",
    "교육", "프로그램", "지원센터", "사업비",
}

_BULLET_PREFIX = re.compile(r"^\s*(?:[○●◦·∙■□▶▷◇◆*\-–—]|[0-9]+[.)]|[가-힣][.)])\s*")
# "지원대상 : ..." 같은 라벨은 사용자가 쓰는 말이 아니라 문서의 목차다.
_LABEL_PREFIX = re.compile(
    r"^(?:지원\s*대상(?:자)?|대상(?:자)?|선정\s*기준|신청\s*자격|자격\s*요건"
    r"|개인|가구|세대|단체|기타|공통|구분|유형)\s*[:：]\s*"
    r"|^(?:지원\s*대상(?:자)?|대상(?:자)?|선정\s*기준|신청\s*자격|자격\s*요건)\s+"
)
# "본문은 딴 데 있다"고 가리키기만 하는 줄. 이걸 질문으로 만들면 주제어가
# 하나도 없어서 어떤 검색기도 맞힐 수 없고, 검색 성능과 무관한 이유로 점수만
# 깎인다(실측: "아래 선정기준을 동시에 충족하여야 함(2026년 기준)").
_POINTER_MARKERS = (
    "아래", "상기", "별첨", "붙임", "참조", "다음 각", "위와 같", "하단",
    "별도 공고", "공고문", "자세한 사항", "세부 사항", "해당 지침", "별표",
)
# 날짜·금액·조문번호만 늘어놓은 줄도 사용자의 상황 설명이 아니다
# ("2026. 3. 1~2027.2.28. 까지 적용").
_NON_WORD_CHARS = re.compile(r"[\d\s.~\-,()%:;/·]")
# 어느 제도에나 붙는 자격·행정 어휘. 한 줄이 **이것들로만** 이뤄져 있으면
# 무엇에 관한 제도인지 알 수 없어 검색 질의가 되지 못한다
# (실측: "중위소득(4인가구 기준) 60%이하, 국민기초생활 수급권자" -> 이 문장만
#  보고 '해양사고 국선 심판변론인'을 찾아낼 방법이 없다).
#
# 정확히 일치가 아니라 **부분 문자열**로 본다. "국민기초생활수급자",
# "인가구", "보호대상아동"처럼 붙여 쓴 변형이 끝없이 나와서, 낱말 목록으로는
# 두더지잡기가 된다. 조금 과하게 걸러도 손해가 없다 - 걸러지면 다른 섹션이나
# 제도명 쪽으로 넘어갈 뿐이다.
_GENERIC_ELIGIBILITY_STEMS = (
    "수급", "중위소득", "가구", "차상위", "저소득", "장애", "유공자", "이민자",
    "한부모", "다문화", "노인", "아동", "청소년", "청년", "여성", "남성",
    "주민", "시민", "구민", "군민", "도민", "세대", "계층", "등급", "기준",
    "이하", "이상", "미만", "초과", "해당", "경우", "대상", "거주", "등록",
    "신청", "본인", "가족", "이내", "우선", "선정", "충족", "요건", "조건",
    "관내", "지역", "전원", "모두", "학생", "근로자", "어르신",
    "기초생활", "생활보장", "취약",
)
# "OO방법 : ..." 라벨은 지원대상/지원내용이 아니라 처리 절차만 말한다.
# 제목 고유어(예: "PC")를 뺀 나머지 줄 중 이런 라벨이 걸리면, 라벨을 떼도
# 남는 말("방문설치" 등)이 그 자체로 무엇에 관한 제도인지 알려주지 못한다
# (실측: "사랑의 PC 지원" 정책의 지원내용 섹션은 "PC"가 들어간 줄이 전부
# 걸러지고 나면 "보급방법 : 방문설치"만 남아, PC라는 말은 한 글자도 없이
# "방문설치인데 지원이 있나요?"라는 질문이 만들어졌다 - 검색기가 맞힐 수
# 없는 질문이 벤치마크에 들어간 것). 라벨이 이거면 줄 자체를 후보에서 뺀다 -
# _situation()이 쓸 줄을 하나도 못 찾으면 제도명을 쓰는 쪽으로 되돌아가는
# 기존 fallback(names_program)이 대신 처리한다.
_PROCEDURAL_LABEL_PREFIX = re.compile(
    r"^(?:(?:보급|지급|배부|공급|전달|접수|처리|선정|등록|관리)\s*방법|사후\s*관리)\s*[:：]"
)
# 제목 끝의 "~ 지원", "~ 사업"은 템플릿의 "지원"과 겹쳐 "지원 지원"이 된다.
_TITLE_TAIL = re.compile(r"\s*(?:지원\s*사업|지원|사업|제도|서비스)$")
# 제목의 괄호 안 부연("(누리과정)")은 고유 어휘 판정에서 뺀다.
_PAREN_NOISE = re.compile(r"\((?:[^()]{0,40})\)")
_SPACES = re.compile(r"\s+")


def _clean_line(text: str) -> str:
    line = _BULLET_PREFIX.sub("", text.strip())
    line = line.replace("※", " ").replace("☞", " ")
    line = _LABEL_PREFIX.sub("", line)
    line = _SPACES.sub(" ", line).strip(" :·-–—")
    return line


def _is_contentful(line: str) -> bool:
    """이 줄이 '상황'으로 쓸 만한 내용을 담고 있는가.

    검색 질의가 될 문장이므로 주제어가 있어야 한다. 딴 데를 가리키기만 하는
    줄과 숫자만 남는 줄은 걸러낸다 - 이런 항목을 벤치마크에 넣으면 검색기가
    아니라 벤치마크를 재게 된다.
    """

    if any(marker in line for marker in _POINTER_MARKERS):
        return False
    letters = _NON_WORD_CHARS.sub("", line)
    if len(letters) < 8:
        return False
    # 한글 단어가 최소 두 덩어리는 있어야 상황 설명이라고 볼 수 있다.
    words = [w for w in re.findall(r"[가-힣]{2,}", line) if w not in _GENERIC_TITLE_WORDS]
    if len(words) < 2:
        return False
    # 그리고 그중 최소 하나는 "무엇에 관한 제도인가"를 말해 주는 어휘여야 한다.
    return any(
        not any(stem in word for stem in _GENERIC_ELIGIBILITY_STEMS) for word in words
    )


def _usable_lines(text: str, *, max_chars: int) -> list[str]:
    """섹션에서 사람이 읽을 만한 한 줄들을 골라 길이를 맞춘다."""

    out: list[str] = []
    for raw in text.splitlines():
        line = _clean_line(raw)
        if len(line) < 10:
            continue
        if _PROCEDURAL_LABEL_PREFIX.match(line):
            continue
        if len(line) > max_chars:
            # 아무 데서나 자르면 "…설치 희망하는 연근해인데"처럼 말이 깨진다.
            # 절 경계가 있을 때만 자르고, 없으면 이 줄은 버리고 다음 줄을 본다.
            cut = line[:max_chars]
            best = max(cut.rfind(mark) for mark in (". ", ", ", "; "))
            if best < max_chars // 2:
                continue
            line = cut[:best].rstrip(" ,.;")
        if len(line) >= 10 and _is_contentful(line):
            out.append(line)
    return out


def _sections(doc: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for section in doc.get("sections") or []:
        meta = section.get("metadata") or {}
        kind = meta.get("section_type")
        if isinstance(kind, str) and kind and kind not in out:
            out[kind] = str(section.get("content") or "")
    return out


def _title_tokens(title: str) -> list[str]:
    raw = _PAREN_NOISE.sub(" ", title)
    tokens = [t for t in re.split(r"[\s·/,()\[\]]+", raw) if len(t) >= 2]
    return [t for t in tokens if t not in _GENERIC_TITLE_WORDS]


def _program_name(title: str) -> str:
    """질문에 쓸 제도명. 끝의 '지원/사업'을 떼어 템플릿과 겹치지 않게 한다."""

    return _TITLE_TAIL.sub("", title.strip()).strip() or title.strip()


def _situation(sections: dict[str, str], title: str) -> str:
    """제도명을 쓰지 않고 '무엇이 필요한지'를 말하는 한 구절을 뽑는다.

    ``purpose_summary``(목적 요약)를 먼저 본다 - "무료로 공구대여 서비스 제공"
    처럼 **그 제도가 해 주는 일**이 적혀 있어서, 검색하는 사용자가 실제로
    묘사할 내용에 가장 가깝다. 없거나 제도명이 그대로 들어 있으면
    ``support_target``(지원대상)으로 내려간다 - 거기엔 사람의 상태가 적혀
    있다("국공립 및 사립유치원에 다니는 3~5세 유아").

    지원대상을 먼저 보지 않는 이유는, 그쪽이 "국민기초생활수급자, 장애인,
    결혼이민자, 국가유공자 등" 같은 **어느 제도에나 붙는 자격 나열**인 경우가
    많아서다. 그런 문장은 주제어가 없어 무엇을 물어보는지 알 수 없다.

    제목의 고유 어휘가 들어 있는 줄은 건너뛴다 - 그런 줄을 쓰면 "제도명을
    모르는 사용자"라는 전제가 깨진다. 끝까지 못 찾으면 빈 문자열을 돌려주고,
    호출한 쪽이 제도명 쓰는 쪽으로 되돌린다.
    """

    tokens = _title_tokens(title)
    for kind in ("purpose_summary", "support_details", "support_target", "eligibility_criteria"):
        text = sections.get(kind)
        if not text:
            continue
        for line in _usable_lines(text, max_chars=70):
            if not any(token in line for token in tokens):
                return line
    return ""


# 형식 6종. 앞 3개는 제도명을 아는 사용자, 뒤 3개는 상황만 말하는 사용자다.
# 인적사항은 본문에 넣지 않는다 - 되묻기로 받는다.
_FORMS: tuple[tuple[str, bool, str], ...] = (
    ("단문", True, "{topic} 받을 수 있나요?"),
    ("반말", True, "{topic} 어떻게 신청해?"),
    ("다중질문", True, "{topic} 대상이 되는지, 얼마나 받는지 같이 알려주세요."),
    ("상황서술", False, "{topic}인데, 받을 수 있는 지원이 있을까요?"),
    ("불릿", False, "- 상황: {topic}\n- 궁금한 것: 지원 대상하고 금액"),
    ("일반문형마무리", False, "{topic}에 해당합니다. 관련해서 받을 수 있는 게 있는지 궁금합니다."),
)


def _birth_date_for(age_start, age_end, today: date) -> str:
    """연령 조건 한가운데에 떨어지는 생년월일을 만든다."""

    lo = age_start if isinstance(age_start, int) else None
    hi = age_end if isinstance(age_end, int) else None
    if lo is None and hi is None:
        age = 35
    elif lo is None:
        age = max(0, hi - 1) if hi > 1 else hi
    elif hi is None:
        age = lo + 5
    else:
        age = (lo + hi) // 2
    age = max(0, min(age, 100))
    # 생일이 아직 안 지난 경우까지 감안해 만 나이가 확실히 age가 되도록
    # 반년 더 뺀다.
    born = today - timedelta(days=int(age * 365.25) + 200)
    return born.isoformat()


def _age_at(birth_iso: str, today: date) -> int:
    born = date.fromisoformat(birth_iso)
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# 프로필 사다리. 지원조건을 통과할 때까지 위에서부터 시도한다. 넓은
# (덜 제약적인) 조합을 앞에 둔다.
_PROFILE_LADDER: tuple[tuple[str, str, str], ...] = (
    ("pct_30_50", "not_registered", "employed"),
    ("pct_30_50", "not_registered", "not_working"),
    ("under_30", "not_registered", "not_working"),
    ("pct_75_100", "not_registered", "employed"),
    ("pct_30_50", "registered", "not_working"),
    ("pct_50_75", "not_registered", "self_employed"),
    ("pct_100_150", "not_registered", "employed"),
    ("under_30", "registered", "not_working"),
)
_INCOME_TEXT = {
    "under_30": "기준중위소득 30% 미만",
    "pct_30_50": "기준중위소득 45%",
    "pct_50_75": "기준중위소득 60%",
    "pct_75_100": "기준중위소득 85%",
    "pct_100_150": "기준중위소득 120%",
}
_EMPLOYMENT_TEXT = {
    "employed": "재직",
    "not_working": "무직",
    "self_employed": "자영업",
}
_DISABILITY_TEXT = {
    "not_registered": "장애가 없습니다.",
    "registered": "등록장애인입니다.",
}


def _user_region(meta: dict) -> str | None:
    """이 정책을 받을 수 있는 사용자의 시도(짧은 이름)를 정한다."""

    if meta.get("region_scope") == "national":
        return None  # 호출한 쪽에서 아무 시도나 돌려 쓴다
    candidates: list[str] = []
    sido = meta.get("region_sido")
    if isinstance(sido, str) and sido:
        candidates.append(sido)
    for name in meta.get("region_names") or []:
        if isinstance(name, str):
            candidates.append(name)
    for text in candidates:
        for canon, short in _SIDO_SHORT:
            if canon in text or short in text:
                return short
    return None


def _build_record(
    doc: dict,
    *,
    index: int,
    today: date,
    support_conditions,
    rotation_regions: list[str],
) -> tuple[dict, str] | tuple[None, str]:
    """한 정책에서 검증 항목 하나를 만든다. 실패하면 (None, 사유)."""

    policy_id = str(doc.get("source_id") or "")
    title = str(doc.get("title") or "").strip()
    meta = doc.get("metadata") or {}
    if not policy_id or not title:
        return None, "id/title 없음"

    try:
        validate_region_metadata(meta.get("region_scope"), meta.get("region_names"))
    except ValueError:
        return None, "region 메타데이터 무효"

    short_region = _user_region(meta)
    if short_region is None:
        if meta.get("region_scope") != "national":
            return None, "시도를 특정할 수 없음"
        short_region = rotation_regions[index % len(rotation_regions)]
    canonical = _CANONICAL_BY_SHORT.get(short_region)
    if not canonical:
        return None, f"알 수 없는 시도 {short_region!r}"
    if not subsidy_regions_match(meta, (canonical,)):
        return None, "region 필터 탈락"

    birth = _birth_date_for(meta.get("age_start"), meta.get("age_end"), today)
    age = _age_at(birth, today)
    lo, hi = meta.get("age_start"), meta.get("age_end")
    if isinstance(lo, int) and age < lo:
        return None, f"연령 하한 미달({age}<{lo})"
    if isinstance(hi, int) and age > hi:
        return None, f"연령 상한 초과({age}>{hi})"

    values = support_conditions.get(policy_id)
    gender = "female" if index % 2 else "male"
    chosen = None
    for income, disability, employment in _PROFILE_LADDER:
        slots = {
            "interests": [],
            "region_names": [canonical],
            "birth_date": birth,
            "income_bracket": income,
            "gender": gender,
            "disability_status": disability,
            "employment_status": employment,
        }
        if values is None:
            chosen = (income, disability, employment)
            break
        plan = resolve_filter_slots(slots, reference_date=today)
        aspects = evaluate_conditions(values, plan)
        if not any(aspect["violated"] for aspect in aspects.values()):
            chosen = (income, disability, employment)
            break
    if chosen is None:
        return None, "어떤 프로필로도 지원조건 통과 실패"
    income, disability, employment = chosen

    sections = _sections(doc)
    form_name, wants_name, template = _FORMS[index % len(_FORMS)]
    situation = _situation(sections, title)
    # _situation은 제도명이 안 섞인 줄만 돌려준다. 그런 줄이 없으면 빈 문자열이
    # 오고, 그때는 솔직하게 제도명을 쓰는 쪽으로 되돌린다(플래그에 남는다).
    names_program = wants_name or not situation
    topic = _program_name(title) if names_program else situation
    topic = topic.strip(" .,")
    if len(topic) < 6:
        return None, "쓸 만한 주제 표현 없음"

    question = template.format(topic=topic)

    subject = "상담 대상자"
    record = {
        "question_id": f"pol-{policy_id}",
        "question": question,
        "expected_policy_ids": [policy_id],
        "should_abstain": False,
        "category": str(meta.get("service_category") or "미분류"),
        "slot_answers": {
            "region": f"{subject}의 거주지는 {short_region}입니다.",
            "birth_date": f"{subject}의 생년월일은 {birth}입니다.",
            "gender": f"{subject}의 성별은 {'여성' if gender == 'female' else '남성'}입니다.",
            "income_bracket": f"{subject} 가구의 소득은 {_INCOME_TEXT[income]}입니다.",
            "disability_status": f"{subject}은(는) {_DISABILITY_TEXT[disability]}",
            "employment_status": f"{subject}의 취업 상태는 {_EMPLOYMENT_TEXT[employment]}입니다.",
        },
        # 분석용 부가 필드. load_questions는 여분 키를 허용한다.
        "policy_title": title,
        "names_program": names_program,
        "question_form": form_name,
        "generated_by": "scripts/build_policy_eval_set.py",
    }
    return record, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=Path(_REAL_SUBSIDY_DOCUMENTS_PATH))
    parser.add_argument(
        "--support-conditions", type=Path, default=Path(_REAL_SUPPORT_CONDITIONS_PATH)
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT / "data/evaluation/policy_eval_questions.jsonl",
    )
    parser.add_argument("--count", type=int, default=100, help="만들 질문 수(정책 1개당 1건)")
    parser.add_argument(
        "--seed", type=int, default=20260915, help="후보 섞기 시드. 같은 시드면 같은 세트가 나온다."
    )
    parser.add_argument("--show", type=int, default=5, help="생성 후 눈으로 볼 예시 개수")
    parser.add_argument(
        "--exclude-ids-file",
        type=Path,
        default=None,
        help="한 줄에 하나씩 적힌 source_id 목록 - 이미 다른 평가 세트가 쓴 정책을 "
        "제외하고 겹치지 않는 새 세트를 만들 때 쓴다(예: 준-Holdout 생성).",
    )
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    excluded_ids: set[str] = set()
    if args.exclude_ids_file is not None:
        excluded_ids = {
            line.strip()
            for line in args.exclude_ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    today = date.today()
    print("지원조건·사용자구분 로드 중...", flush=True)
    support_conditions = load_support_conditions(args.support_conditions)
    user_types = load_policy_user_types(args.documents)
    print(f"  지원조건 {len(support_conditions)}건, 사용자구분 {len(user_types)}건", flush=True)

    print(f"{args.documents} 훑는 중...", flush=True)
    candidates: list[dict] = []
    total = 0
    with args.documents.open(encoding="utf-8") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            total += 1
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                continue
            policy_id = str(doc.get("source_id") or "")
            if not policy_id:
                continue
            if policy_id in excluded_ids:
                continue
            if not _is_individual_applicable(user_types.get(policy_id)):
                continue
            sections = _sections(doc)
            if not any(
                sections.get(kind)
                for kind in ("purpose_summary", "support_details", "support_target")
            ):
                continue
            candidates.append(
                {
                    "source_id": policy_id,
                    "title": doc.get("title"),
                    "metadata": doc.get("metadata"),
                    "sections": doc.get("sections"),
                }
            )
    print(f"  문서 {total}건 중 개인 대상·본문 있는 후보 {len(candidates)}건", flush=True)
    if not candidates:
        print("후보가 없습니다.", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    rng.shuffle(candidates)

    rotation_regions = [short for _, short in _SIDO_SHORT]
    records: list[dict] = []
    seen_questions: set[str] = set()
    reasons: dict[str, int] = {}
    for candidate in candidates:
        if len(records) >= args.count:
            break
        record, reason = _build_record(
            candidate,
            index=len(records),
            today=today,
            support_conditions=support_conditions,
            rotation_regions=rotation_regions,
        )
        if record is None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        if record["question"] in seen_questions:
            reasons["질문 중복"] = reasons.get("질문 중복", 0) + 1
            continue
        seen_questions.add(record["question"])
        records.append(record)

    if len(records) < args.count:
        print(
            f"경고: {args.count}건을 요청했지만 {len(records)}건만 만들었습니다.",
            file=sys.stderr,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print()
    print("=" * 72)
    print(f"생성 완료: {args.output} ({len(records)}건, 정책 {len({r['expected_policy_ids'][0] for r in records})}종)")
    print("=" * 72)
    by_category: dict[str, int] = {}
    by_form: dict[str, int] = {}
    named = 0
    for record in records:
        by_category[record["category"]] = by_category.get(record["category"], 0) + 1
        by_form[record["question_form"]] = by_form.get(record["question_form"], 0) + 1
        named += bool(record["names_program"])
    print(f"  분야       : {dict(sorted(by_category.items(), key=lambda kv: -kv[1]))}")
    print(f"  질문 형식  : {dict(sorted(by_form.items()))}")
    print(f"  제도명 노출: {named}건 / 미노출 {len(records) - named}건")
    if reasons:
        print(f"  제외 사유  : {dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}")
    print()
    print("-- 예시 --")
    for record in records[: max(0, args.show)]:
        flag = "제도명O" if record["names_program"] else "제도명X"
        print(f"[{record['question_form']}/{flag}] {record['policy_title']}")
        print(f"  Q: {record['question']}")
    print()
    print("이 세트는 절대 점수를 '실사용자 성능'으로 읽으면 안 됩니다. 질문이")
    print("정책 문서에서 파생되어 어휘가 겹칩니다. 설정 A와 B를 비교하는 데 씁니다.")
    print()
    print("실행:")
    print(f"  python scripts/run_model_evaluation.py --workers 5 --questions {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
