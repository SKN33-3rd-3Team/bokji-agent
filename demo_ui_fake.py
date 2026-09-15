"""UI만 빠르게 확인하는 데모 진입점 - 실제 LLM/파이프라인을 안 돌린다.

    streamlit run demo_ui_fake.py

``run_pipeline``을 가짜로 바꿔치기해서, 무슨 질문을 치든 미리 준비된
정책 2건이 즉시 나온다(대기 시간 0초). 로그인·사이드바·카드·모달 등
화면 쪽 코드를 고칠 때, 매번 진짜 파이프라인(수십 초~분)을 기다리지
않고 바로바로 확인하기 위한 용도다. app.py는 건드리지 않는다.
"""

from __future__ import annotations

import streamlit as st
import streamlit_ui  # noqa: F401  # import 경로 부트스트랩

from streamlit_ui.pages import chat as chat_module
from streamlit_ui.pages.auth import page_login, page_signup
from streamlit_ui.pages.chat import page_chat
from streamlit_ui.pages.mypage import page_mypage
from streamlit_ui.session import init_session
from streamlit_ui.theme import localize_menu, render_header

_SAMPLE_POLICIES = [
    {
        "policy_id": "demo-1",
        "title": "청년월세지원",
        "badge": "확인 필요",
        "eligibility_status": "미확인",
        "eligibility_reasons": ["만 19~39세 무주택 청년 요건에 해당합니다."],
        "verification_checked": ["연령"],
        "verification_unchecked": ["장애 여부", "성별", "소득 수준", "취업 상태", "지역"],
        "verification_note": (
            "연령 조건만 확인했습니다. 장애 여부, 성별, 소득 수준, 취업 상태, "
            "지역은(는) 확인하지 못했으니 원문을 직접 확인해주세요."
        ),
        "amount_label": "월 최대 200,000원 (최대 12개월)",
        "duplicate_status": "가능",
        "duplicate_note": "다른 주거 지원과 중복 가능합니다.",
        "needs_confirmation": ["소득 기준 확인 필요"],
        "related_law": [{"law_name": "청년월세 한시 특별지원 사업 공고"}],
        "detail": {
            "support_details": "만 19~39세 무주택 청년에게 월 최대 20만원의 월세를 최대 12개월간 지원합니다.",
            "application_method": "복지로 홈페이지에서 온라인 신청합니다.",
            "required_documents": "임대차계약서\n주민등록등본\n소득금액증명원",
            "source_url": "https://example.gov.kr",
        },
    },
    {
        "policy_id": "demo-2",
        "title": "근로장려금",
        "badge": "자격 충족",
        "eligibility_status": "충족",
        "eligibility_reasons": ["가구 소득 기준을 충족합니다."],
        "verification_checked": ["소득 수준", "가구 유형"],
        "verification_unchecked": [],
        "verification_note": "소득 수준, 가구 유형 조건을 확인했습니다.",
        "amount_label": "최대 3,300,000원 (연 1회)",
        "duplicate_status": "확인 필요",
        "related_law": [],
        "detail": {
            "support_details": "저소득 근로자 가구에 소득 수준에 따라 최대 330만원을 연 1회 지급합니다.",
            "application_method": "홈택스 또는 세무서 방문 신청합니다.",
            "source_url": "https://example.gov.kr",
        },
    },
]


def _fake_run_pipeline(*, user_input, session_id, awaiting_followup, top_k,
                        extra_interests=None, known_region=None):
    if not awaiting_followup:
        # 첫 턴은 되묻기로 - 위젯 폼을 바로 확인할 수 있게 한다.
        return {
            "status": "needs_input",
            "session_id": session_id,
            "question": (
                "맞춤 제도를 찾으려면 아래 정보가 필요해요. 한 번에 이어서 답해주셔도 됩니다."
            ),
            "missing_slots": [
                "region", "birth_date", "gender", "income_bracket",
                "disability_status", "employment_status",
            ],
            "llm_status": {"enabled": False},
        }
    return {
        "status": "answered",
        "session_id": session_id,
        "answer_status": "complete",
        "final_answer": f"'{user_input}' 질문으로 정책 {len(_SAMPLE_POLICIES)}건을 확인했습니다. (데모 가짜 응답)",
        "final_citations": [],
        "policies": _SAMPLE_POLICIES,
        "llm_status": {"enabled": False},
    }


chat_module.run_pipeline = _fake_run_pipeline

_PAGES = {
    "login": page_login,
    "signup": page_signup,
    "mypage": page_mypage,
    "chat": page_chat,
}


def main() -> None:
    st.set_page_config(
        page_title="복지 에이전트 (UI 데모)",
        page_icon="🧪",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    init_session()
    localize_menu()
    render_header()
    st.caption(":material/science: UI 데모 모드 - 실제 파이프라인 대신 가짜 응답을 씁니다.")

    view = st.session_state.get("view", "chat")
    _PAGES.get(view, page_chat)()


main()
