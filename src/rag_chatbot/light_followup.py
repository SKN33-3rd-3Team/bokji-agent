"""정책 상세 문의 경량 응답 - 무거운 N1~N14 파이프라인을 다시 돌리지 않고,
직전 상담에서 이미 세션에 저장된 대상 정책 정보(``policies[i]["detail"]`` 7개
섹션 + ``eligibility_reasons`` + ``final_answer``)만으로 답한다.

이 모듈은 그래프 노드가 아니라 서비스 계층 유틸이다. Streamlit(``chat.py``)이
직접 호출하며, 여기서 답을 못 만들면 에스컬레이션하지 않고
"이 채팅은 이 정책 전용" 안내로 넘긴다(스코프: 추천받은 정책 하나에 대한
상세 문의).

핵심 원칙(``README.md``): "검색된 공적 근거로 명확히 입증된 내용만 답한다."
- LLM에게 대상 정책 텍스트만 컨텍스트로 주고, 그 밖의 내용은 만들어내지 말라고
  강제한다.
- LLM이 근거로 제시한 원문 발췌(``evidence_quotes``)가 컨텍스트에 실제로 있는지
  코드가 문자열 대조로 다시 확인한다(``verify_light_answer``). N6
  document_verification과 같은 원리 - 발췌를 원문 그대로 내게 하고 포함 여부만
  본다.

참고:
- ``service.answer_followup()``은 N3 interrupt(되묻기) 재개용이라 이 모듈과 다르다.
- ``service._fetch_policy_detail()``은 이미 있는 함수 - 우리는 그 결과를 세션에서
  재사용만 하고 새로 호출하지 않는다.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping

from .llm import LLMCallError, LLMClient, loads_json_object

# 재시도 사이 짧은 대기. 무료/공용 엔드포인트가 짧은 순간 과호출(버스트)에
# 429/타임아웃을 내는 걸 봤다(2026-09-11 평가 스크립트로 29건 연속 호출 시
# 초반 몇 건 이후 전부 즉시 실패 -> 안내로 폴백, 단일 호출은 정상). 바로
# 재시도하면 같은 버스트에 또 걸리므로 한 박자 쉬고 재시도한다.
_RETRY_BACKOFF_SECONDS = 1.5

_WS_RE = re.compile(r"\s+")

# policies[i]["detail"]의 섹션 키 -> 사람이 읽는 라벨. service._DETAIL_SECTION_TYPES와
# 같은 순서·라벨을 유지한다.
_DETAIL_SECTIONS: tuple[tuple[str, str], ...] = (
    ("purpose", "목적"),
    ("support_target", "지원대상"),
    ("eligibility_criteria", "선정기준"),
    ("support_details", "지원내용"),
    ("application_method", "신청방법"),
    ("application_period", "신청기한"),
    ("legal_basis", "근거법령"),
    ("required_documents", "구비서류"),
    ("required_documents_official", "공무원 확인 구비서류"),
    ("required_documents_self", "본인확인 필요 구비서류"),
)

_SYSTEM_PROMPT = (
    "너는 복지 정책 상세 안내 도구다. 주어진 정책 정보 안에 있는 내용만으로 "
    "답하고, 정보에 없는 내용은 절대 만들거나 추측하지 않는다. "
    "답변은 '~합니다', '~입니다' 같은 정중한 격식체로 끝맺는다."
)


def _normalize(text: object) -> str:
    """공백·줄바꿈을 한 칸으로 접어 비교하기 좋게 만든다."""

    return _WS_RE.sub(" ", str(text if text is not None else "")).strip()


def build_policy_context(policy: Mapping, *, extra_facts: Iterable = ()) -> str:
    """대상 정책의 이미 검증된 정보를 LLM 컨텍스트(및 검증 haystack)로 조립한다.

    ``policy``는 ChatResponse의 ``policies`` 항목(PolicyView) 하나다. 첫 상담 때
    ``service._fetch_policy_detail()``이 Vector DB에서 뽑아 ``detail``에 담아둔
    7개 섹션 전문을 그대로 재사용한다 - 여기서 새로 검색하지 않는다.

    ``extra_facts``에는 자격 판정 근거 문장 등 그 정책에 대한 다른 검증된
    문장을 넣을 수 있다.
    """

    lines: list[str] = []
    title = policy.get("title")
    if isinstance(title, str) and title.strip():
        lines.append(f"[{title.strip()}]")

    detail = policy.get("detail")
    if isinstance(detail, Mapping):
        for key, label in _DETAIL_SECTIONS:
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{label}: {value.strip()}")

    for fact in extra_facts:
        if isinstance(fact, str) and fact.strip():
            lines.append(fact.strip())

    return "\n".join(lines)


def _coerce_light_answer(data: Mapping) -> dict | None:
    """파싱된 dict를 ``{answerable, answer, evidence_quotes}`` 계약으로 좁힌다.

    형식이 어긋나거나(answerable가 bool이 아님 등) answerable=true인데 답
    문장이 비어 있으면 ``None``을 반환한다 - 호출부가 안내 메시지로 폴백한다.
    """

    answerable = data.get("answerable")
    if not isinstance(answerable, bool):
        return None

    answer = data.get("answer")
    answer = answer.strip() if isinstance(answer, str) else ""

    raw_quotes = data.get("evidence_quotes")
    quotes = (
        [q.strip() for q in raw_quotes if isinstance(q, str) and q.strip()]
        if isinstance(raw_quotes, list)
        else []
    )

    if not answerable:
        return {"answerable": False, "answer": "", "evidence_quotes": []}
    if not answer:
        return None
    return {"answerable": True, "answer": answer, "evidence_quotes": quotes}


def answer_light_followup(
    context_text: str, question: str, *, llm_client: LLMClient, attempts: int = 2
) -> dict | None:
    """대상 정책 컨텍스트만으로 후속 질문에 답을 시도한다.

    반환: ``{"answerable": bool, "answer": str, "evidence_quotes": list[str]}``.
    LLM 호출 실패, JSON 파싱 실패, 형식 위반이면 ``None`` - 이 경우와
    ``answerable=False``, 그리고 이후 ``verify_light_answer`` 실패는 모두
    호출부(``chat.py``)에서 "이 채팅은 이 정책 전용" 안내로 처리한다.

    ``attempts``만큼 다시 시도한다(기본 2회). 무료/공용 LLM 엔드포인트는
    간헐적으로 타임아웃이 나거나(예: HuggingFace 서버리스) 작은 모델이 JSON
    대신 산문을 뱉는 일이 잦은데, 한 번 실패했다고 바로 안내로 물러나면
    답할 수 있는 질문도 못 답하게 된다. 재시도는 같은 프롬프트를 그대로
    다시 보낼 뿐이라 "지어내지 않는다" 원칙에는 영향이 없다.

    검증(``verify_light_answer``)은 여기서 하지 않는다 - 같은 ``context_text``로
    호출부가 별도로 돌린다.
    """

    context_text = (context_text or "").strip()
    question = (question or "").strip()
    if not context_text or not question:
        return None

    prompt = (
        "다음은 이미 검증된 복지 정책 정보다. 사용자 질문에 이 정보 안에 있는 "
        "내용만으로 답하라. **질문과 관련된 내용이 정보 안에 조금이라도 있으면 "
        "answerable을 true로 하고 그 범위 안에서 답하라.** 정보가 전혀 다루지 "
        "않는 별개의 주제(예: 다른 정책, 이 정보에 없는 절차·중복수급 여부)일 "
        "때만 answerable을 false로 하라 - '자세히는 안 나와 있어서 애매하다'는 "
        "이유로 false로 두지 마라. '사용자 정보 -'로 시작하는 줄은 질문자 본인의 "
        "상황이니 자격·지역 관련 질문에 활용해도 된다. answer는 '~합니다.', "
        "'~입니다.'처럼 정중한 격식체 종결어미와 마침표로 끝맺어라. "
        "evidence_quotes에는 답의 근거가 된 문장을 정보 원문에서 그대로 복사해 "
        "담아라(의역·요약 금지, 짧아도 된다).\n\n"
        "예시 1 (관련 내용이 있으면 답한다):\n"
        "[정책 정보]\n지원내용: 월 최대 20만원을 최대 12개월 지원한다.\n"
        "[질문]\n한 달에 얼마씩 받아요?\n"
        '출력: {"answerable": true, "answer": "월 최대 20만원을 최대 12개월 '
        '지원합니다.", "evidence_quotes": ["월 최대 20만원을 최대 12개월 지원한다."]}\n\n'
        "예시 2 (정보에 전혀 없는 별개 주제만 거절한다):\n"
        "[정책 정보]\n지원내용: 월 최대 20만원을 최대 12개월 지원한다.\n"
        "[질문]\n다른 지역 청년 정책도 알려줘\n"
        '출력: {"answerable": false, "answer": "", "evidence_quotes": []}\n\n'
        "출력 형식(다른 텍스트 없이 이 JSON 하나만): "
        '{"answerable": <true 또는 false>, "answer": "<한국어 답변 또는 빈 '
        '문자열>", "evidence_quotes": ["<정보에서 그대로 발췌한 문장>", ...]}\n\n'
        f"[정책 정보]\n{context_text}\n\n[질문]\n{question}"
    )

    for attempt in range(max(attempts, 1)):
        if attempt > 0:
            time.sleep(_RETRY_BACKOFF_SECONDS)
        try:
            response = llm_client.complete(prompt, system=_SYSTEM_PROMPT)
            data = loads_json_object(response)
        except (LLMCallError, ValueError, TypeError):
            continue
        coerced = _coerce_light_answer(data)
        if coerced is not None:
            return coerced
    return None


_GUIDANCE_TEMPLATE = (
    "이 채팅은 '{title}' 정책에 대한 질문만 답할 수 있어요. "
    "다른 정책이나 새로운 검색은 메인 화면에서 다시 물어봐 주세요."
)


def _profile_facts(user_profile: Iterable | None) -> list[str]:
    """사이드바 "파악한 정보"(``st.session_state["profile"]``)를 컨텍스트 문장으로.

    각 항목은 ``{"key","label","value"}`` (service._build_profile 산출물). "우리
    지역도 되나요?", "제 소득이면 대상인가요?" 같은 질문에 답하려면 정책
    본문만으로는 부족하고 질문자 본인 상황이 필요하다.

    개인정보 주의: 여기 값은 이미 파생·라벨링된 형태다(생년월일 원문 없음,
    만 나이만 - service._build_profile 참고). N1/N5/N9가 무거운 실행에서
    슬롯을 LLM에 넘기는 것과 같은 수준의 노출이다.
    """

    facts: list[str] = []
    for item in user_profile or []:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if label and value:
            facts.append(f"사용자 정보 - {label}: {value}")
    return facts


def respond_to_policy_question(
    policy: Mapping,
    question: str,
    *,
    llm_client: LLMClient | None,
    user_profile: Iterable | None = None,
) -> dict:
    """정책 상세 채팅의 한 턴 - 컨텍스트 조립(B) → 생성(B) → 검증(C)을 묶어
    화면에 그대로 쓸 결과를 돌려준다.

    반환: ``{"kind": "answer" | "guidance", "text": str, "evidence_quotes": list[str]}``
    - ``"answer"``: 검증까지 통과한 경량 답변
    - ``"guidance"``: 답할 수 없거나(``answerable=false``), LLM이 없거나 실패,
      또는 근거 검증 실패 - 지어내지 않고 안내 문구로 대체

    ``policy``는 ChatResponse의 ``policies`` 항목(PolicyView) 하나다.
    ``user_profile``은 세션의 "파악한 정보"(지역·나이·소득 등) - 자격 관련
    질문에 쓰려고 컨텍스트에 함께 넣는다.
    """

    title = str(policy.get("title") or policy.get("policy_id") or "이 정책")
    guidance = {
        "kind": "guidance",
        "text": _GUIDANCE_TEMPLATE.format(title=title),
        "evidence_quotes": [],
    }
    if llm_client is None:
        return guidance

    reasons = [
        f"자격 판정 근거: {reason}"
        for reason in (policy.get("eligibility_reasons") or [])
        if isinstance(reason, str) and reason.strip()
    ]
    context = build_policy_context(
        policy, extra_facts=[*_profile_facts(user_profile), *reasons]
    )
    if not context:
        return guidance

    light = answer_light_followup(context, question, llm_client=llm_client)
    if light is None or not light["answerable"]:
        return guidance
    if not verify_light_answer(light["evidence_quotes"], context):
        return guidance
    return {
        "kind": "answer",
        "text": light["answer"],
        "evidence_quotes": light["evidence_quotes"],
    }


def verify_light_answer(evidence_quotes: Iterable, context_text: object) -> bool:
    """LLM이 근거로 제시한 발췌(``evidence_quotes``)가 컨텍스트 텍스트 안에
    문자열로 실제 존재하는지 확인한다 (N6 document_verification과 같은 원리).

    - 발췌가 하나라도 컨텍스트에 없으면 실패로 본다(모델이 지어냈다고 가정).
    - 발췌가 아예 비어 있으면 실패로 본다(근거 없이 답한 것).

    공백·줄바꿈은 정규화 후 비교한다. 원문에는 청킹 prefix나 줄바꿈이 섞여
    있어 완전 일치가 어렵기 때문이다. (발췌 표현이 약간 달라 자주 실패하면
    토큰 단위 허용치를 추가하는 것을 검토 - 지금은 엄격 매칭.)

    통과(``True``) 시에만 경량 답변을 사용자에게 노출한다.
    """

    haystack = _normalize(context_text)
    if not haystack:
        return False
    quotes = [
        _normalize(quote)
        for quote in (evidence_quotes or [])
        if isinstance(quote, str) and quote.strip()
    ]
    if not quotes:
        return False
    return all(quote in haystack for quote in quotes)
