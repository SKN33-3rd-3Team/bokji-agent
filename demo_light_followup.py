"""정책 상세 문의 경량 채팅(feat/followup-light-chat) 로컬 데모.

벡터 DB(2.9GB, git 미포함)와 N1~N14 추천 파이프라인 없이, 손으로 만든 정책
2건과 **실제 LLM**(.env의 HF_TOKEN + LLM_MODEL_NAME)으로 이 기능만 클릭해
본다. 실제로 도는 코드:
  - streamlit_ui/rendering.py  _render_policy() 의 "이 정책에 대해 물어보기" 버튼
  - streamlit_ui/pages/chat.py _render_detail_chat_banner / _handle_detail_chat_turn
                               / _render_light_answer
  - src/rag_chatbot/light_followup.py  build_policy_context / answer_light_followup
                               / verify_light_answer / respond_to_policy_question

가짜인 것: 정책 추천 결과(run_pipeline), 벡터 DB 존재 체크.

실행:
    cd C:\\Users\\playdata2\\Desktop\\rag_chatbot
    .venv\\Scripts\\python.exe -m streamlit run demo_light_followup.py
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import streamlit as st
import streamlit_ui  # noqa: F401  # rag_design / rag_chatbot import 경로 부트스트랩

from src.rag_chatbot import service as _service
from streamlit_ui.pages import chat
from streamlit_ui.session import init_session
from streamlit_ui.theme import render_header

# ── 1. 벡터 DB 체크 통과시키기 (파일 존재만 보므로 빈 파일이면 충분) ──────
_FAKE_DB = Path(tempfile.gettempdir()) / "bokji_demo_vector_db"
_FAKE_DB.mkdir(exist_ok=True)
(_FAKE_DB / "chroma.sqlite3").touch()
chat.VECTOR_DB_DIR = _FAKE_DB

# ── 2. LLM 클라이언트: get_graph()를 타면 실제 벡터 스토어에 붙으려다 죽는다.
#     graph 없이 HF_TOKEN만으로 만드는 build_llm_client()를 바로 쓴다. ──────
_LLM = _service.build_llm_client()
_service.get_llm_client = lambda: _LLM

# ── 3. 손으로 만든 정책 추천 결과 (run_pipeline 대체) ────────────────────
_POLICIES = [
    {
        "policy_id": "youth-housing-2024",
        "title": "청년 월세 한시 특별지원",
        "eligibility_status": "충족",
        "badge": "자격 충족",
        "amount_label": "월 최대 20만원 (최대 12개월)",
        "duplicate_status": "일부 제한",
        "eligibility_reasons": [
            "만 19~34세 무주택 청년",
            "청년 본인 소득이 기준 중위소득 60% 이하",
        ],
        "detail": {
            "purpose": "코로나19 장기화로 어려움을 겪는 청년층의 주거비 부담을 "
            "완화하기 위해 월세를 한시적으로 지원한다.",
            "support_target": "부모와 별도로 거주하는 만 19세부터 34세까지의 "
            "무주택 청년 중, 청년 본인 가구의 소득이 기준 중위소득 60% 이하이고 "
            "원가구 소득이 기준 중위소득 100% 이하인 사람.",
            "eligibility_criteria": "보증금 5천만원 이하이면서 월세 70만원 이하인 "
            "주택에 거주해야 한다. 월세가 70만원을 초과하더라도 보증금 월세 "
            "환산액과 월세액을 합해 90만원 이하이면 지원 대상에 포함한다.",
            "support_details": "실제 납부하는 임대료를 월 최대 20만원까지 "
            "최대 12개월(회) 동안 나누어 지원한다. 총 지원액은 최대 240만원이다. "
            "방학 등으로 일시적으로 부모 집에 거주해 월세를 내지 않은 달은 "
            "지원 대상에서 제외한다.",
            "application_method": "복지로 누리집(www.bokjiro.go.kr)에서 온라인으로 "
            "신청하거나, 주소지 관할 주민센터를 방문해 신청한다. 신청 시 "
            "임대차계약서와 최근 3개월 월세 이체 증빙을 제출한다.",
            "application_period": "2024년 2월 26일부터 2025년 2월 25일까지 "
            "상시 신청을 받는다. 지원은 신청한 달부터 개시한다.",
            "legal_basis": "청년복지 지원 사업 운영지침(국토교통부 고시)",
            "source_url": "https://www.bokjiro.go.kr",
        },
    },
    {
        "policy_id": "parental-benefit-2024",
        "title": "부모급여(영아수당)",
        "eligibility_status": "미확인",
        "badge": "추가 확인 필요",
        "amount_label": "월 100만원(0세) / 월 50만원(1세)",
        "duplicate_status": "확인 필요",
        "eligibility_reasons": [],
        "detail": {
            "purpose": "출산과 양육으로 소득이 줄어드는 가구의 경제적 부담을 "
            "덜고 영아기 집중 돌봄을 지원한다.",
            "support_target": "2022년 1월 1일 이후 출생한 만 0세와 만 1세 아동을 "
            "양육하는 가구. 소득 재산 기준은 없다.",
            "eligibility_criteria": "아동이 대한민국 국적을 가지고 있고 주민등록 "
            "번호가 정상적으로 부여된 경우 신청할 수 있다. 아동이 90일 이상 "
            "해외에 체류하면 그 기간에는 지급을 정지한다.",
            "support_details": "만 0세 아동은 월 100만원, 만 1세 아동은 월 50만원을 "
            "현금으로 지급한다. 어린이집을 이용하면 보육료 바우처로 지급하며, "
            "바우처 단가가 부모급여액보다 적으면 차액을 현금으로 지급한다.",
            "application_method": "아동 출생일을 포함해 60일 이내에 복지로 "
            "누리집 또는 주민센터에서 신청하면 출생일이 속한 달부터 소급 "
            "지원한다. 60일이 지나 신청하면 신청한 달부터 지원한다.",
            "application_period": "상시 신청.",
            "legal_basis": "아동수당법 제4조, 저출산·고령사회기본법",
            "source_url": "https://www.bokjiro.go.kr",
        },
    },
]


def _fake_run_pipeline(*, user_input, session_id, awaiting_followup, top_k, extra_interests=None):
    # session_id는 매번 달라야 한다 - 정책 캐러셀 위젯 키가 여기서 나오므로,
    # 같은 값이면 두 번째 상담에서 DuplicateWidgetKey가 난다(실제 run_pipeline은
    # 상담마다 conversation_id가 회전해서 자연히 유니크하다).
    return {
        "status": "answered",
        "answer_status": "complete",
        "final_answer": "입력하신 조건으로 아래 2건의 제도를 확인했어요. "
        "각 카드 아래 **‘이 정책에 대해 물어보기’**를 눌러 상세 질문을 이어갈 수 있어요.",
        "final_citations": [],
        "policies": _POLICIES,
        "session_id": f"demo-{uuid.uuid4().hex[:8]}",
        "llm_status": {"enabled": True, "note": "데모: 추천은 고정, 상세 답변만 실제 LLM"},
    }


chat.run_pipeline = _fake_run_pipeline

# 실제 앱에서 로그인/상담을 거치면 사이드바 "파악한 정보"에 채워지는 값.
# 데모에선 손으로 심어서 "우리 지역도 되나요?" 같은 질문을 테스트할 수 있게 한다.
_FAKE_PROFILE = [
    {"key": "region", "label": "지역", "value": "서울특별시"},
    {"key": "age", "label": "나이", "value": "만 27세"},
    {"key": "income_bracket", "label": "소득", "value": "중위소득 60% 이하"},
    {"key": "housing", "label": "주택", "value": "무주택"},
]

# ── 4. 실제 앱과 동일하게 페이지를 띄운다 ───────────────────────────────
st.set_page_config(
    page_title="경량 채팅 데모",
    page_icon="💬",
    layout="centered",
    initial_sidebar_state="expanded",
)
init_session()
# init_session()이 profile을 []로 심으므로 setdefault로는 안 되고 직접 채운다.
if not st.session_state.get("profile"):
    st.session_state["profile"] = list(_FAKE_PROFILE)
render_header()

with st.sidebar:
    st.caption(
        f"데모 모드 · 모델: `{os.environ.get('LLM_MODEL_NAME', '(기본)')}`  \n"
        f"LLM 연결: {'✅' if _LLM is not None else '❌ (HF_TOKEN 확인)'}"
    )
    if st.button("첫 화면으로", width="stretch"):
        st.session_state.messages = []
        st.session_state.pop("detail_chat_policy", None)
        st.session_state.pop("detail_chat_history", None)
        st.rerun()

# 첫 진입 시 추천 결과를 한 번 심어 둔다(질문 입력 없이 바로 카드가 보이도록).
if "messages" in st.session_state and not st.session_state.messages:
    st.session_state.messages = [
        {"role": "user", "content": "지원금 뭐 받을 수 있는지 알려주세요."},
        {"role": "assistant", "result": _fake_run_pipeline(
            user_input="", session_id="", awaiting_followup=False, top_k=5
        )},
    ]

chat.page_chat()
