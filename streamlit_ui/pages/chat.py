"""상담(챗) 화면."""

from __future__ import annotations

from datetime import date

import streamlit as st
from rag_design.embeddings import EmbeddingProviderError
from rag_design.vector_store import VectorStoreError

from ..constants import (
    BOT_AVATAR,
    DEFAULT_TOP_K,
    EXAMPLE_PROMPTS,
    INTEREST_FIELD_OPTIONS,
    INTEREST_OPTIONS,
    STATUS_LABELS_KO,
    USER_AVATAR,
)
from ..nav import goto
from ..pipeline import run_pipeline
from ..rendering import profile_summary, render_result
from ..vector_store import get_store

# 임베딩 provider 는 korean(multilingual-e5) 고정. 최초 실행 시 모델을 내려받는다.
_EMBEDDING_CHOICE = "korean"


def _reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.slots = {}
    st.session_state.slot_ask_counts = {}
    st.session_state.pending_prompt = None


def _render_intro() -> None:
    with st.container(border=True):
        st.markdown(
            "#### :material/waving_hand: 안녕하세요, 복지 에이전트입니다\n"
            "거주 지역과 기본 정보를 알려주시면 받을 수 있는 지원 제도를 찾아 "
            "**자격 · 지원금 · 중복수급**을 근거와 함께 확인해 드려요."
        )
        st.caption("아래 예시를 눌러 바로 시작할 수 있어요.")
        for idx, example in enumerate(EXAMPLE_PROMPTS):
            preview = example if len(example) <= 46 else example[:45] + "…"
            if st.button(
                preview, key=f"ex_{idx}", width="stretch",
                icon=":material/bolt:",
            ):
                st.session_state.pending_prompt = example
                st.rerun()


def _render_sidebar() -> dict:
    """사이드바 컨트롤을 그리고 파이프라인에 넘길 설정값을 반환한다."""

    with st.sidebar:
        st.subheader(":material/tune: 설정")
        as_of = st.date_input("검색 기준일", value=date.today())
        if not isinstance(as_of, date):  # 범위 선택 등으로 값이 비면 오늘로
            as_of = date.today()

        selected_interests = st.multiselect(
            "지원조건", INTEREST_OPTIONS,
            default=[], key="interests_pick",
            placeholder="조건 선택 (여러 개 선택 가능)",
            help="해당하는 조건을 골라 주세요. 여러 개 선택할 수 있고, "
                 "정책 검색 쿼리에 더해집니다.",
        )
        selected_fields = st.multiselect(
            "관심 분야", INTEREST_FIELD_OPTIONS,
            default=[], key="fields_pick",
            placeholder="분야 선택 (여러 개 선택 가능)",
            help="관심 있는 지원 분야를 골라 주세요. 여러 개 선택할 수 있고, "
                 "지원조건과 함께 정책 검색 쿼리에 반영됩니다.",
        )
        top_k = st.slider(
            "정책 후보 수", min_value=3, max_value=15,
            value=DEFAULT_TOP_K, key="topk",
            help="검색이 가져오는 후보 정책 청크 수. 늘리면 더 많은 제도를 훑습니다.",
        )

        if st.button("대화 초기화", icon=":material/delete_sweep:",
                     width="stretch", type="secondary"):
            _reset_conversation()
            st.rerun()

        st.markdown(":material/account_circle: **계정**")
        acc = st.container(horizontal=True)
        if acc.button("로그인", icon=":material/login:", width="stretch",
                      key="sb_login"):
            goto("login")
        if acc.button("회원가입", icon=":material/person_add:", width="stretch",
                      key="sb_signup"):
            goto("signup")
        if st.button("마이페이지", icon=":material/person:", width="stretch",
                     key="sb_mypage"):
            goto("mypage")

    return {
        "as_of": as_of,
        "top_k": top_k,
        "extra_interests": [*selected_interests, *selected_fields],
    }


def _render_profile_sidebar() -> None:
    with st.sidebar:
        profile = profile_summary(st.session_state.slots)
        if profile:
            st.markdown(":material/badge: **파악한 정보**")
            for line in profile:
                st.markdown(f"- {line}")


def _announce_ingest(load_info: dict) -> None:
    """샘플 색인이 이번 실행에서 처음 일어났으면 한 번만 알린다."""

    if not load_info.get("ingested") or st.session_state.ingest_toast_shown:
        return
    rep = load_info.get("report", {})
    parts = []
    for key in ("subsidy", "law"):
        info = rep.get(key) or {}
        if "chunks" in info:
            parts.append(f"{key} {info['chunks']}청크")
        elif "error" in info:
            parts.append(f"{key} 색인 실패")
    st.toast("샘플 문서 색인 완료 · " + ", ".join(parts), icon=":material/task_alt:")
    st.session_state.ingest_toast_shown = True


def page_chat() -> None:
    st.caption(
        "거주 지역·기본 정보를 바탕으로 지원 제도를 찾아 자격·지원금·중복수급을 "
        "근거와 함께 확인합니다."
    )

    config = _render_sidebar()

    try:
        store, load_info = get_store(_EMBEDDING_CHOICE)
    except (VectorStoreError, EmbeddingProviderError) as exc:
        st.error(f"Vector store 준비 실패: {type(exc).__name__}: {exc}",
                 icon=":material/error:")
        st.stop()

    _render_profile_sidebar()
    _announce_ingest(load_info)

    # 대화 이력 렌더링
    for message in st.session_state.messages:
        avatar = USER_AVATAR if message["role"] == "user" else BOT_AVATAR
        with st.chat_message(message["role"], avatar=avatar):
            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                render_result(message["result"])

    if not st.session_state.messages and not st.session_state.pending_prompt:
        _render_intro()

    typed = st.chat_input(
        "메시지를 입력하세요 (예: 서울 사는 2021년 3월생 아이 유아학비 지원 되나요?)"
    )
    # pending_prompt 는 조건과 무관하게 항상 소비한다(예시 버튼이 남긴 값이
    # 다음 턴에 유령 실행되지 않도록).
    pending = st.session_state.pop("pending_prompt", None)
    prompt = typed or pending
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar=BOT_AVATAR):
        with st.status("상담을 진행하고 있어요", expanded=True) as status:
            def _on_step(msg: str) -> None:
                status.write(f":material/chevron_right: {msg}")

            result = run_pipeline(
                user_input=prompt,
                slots=st.session_state.slots,
                slot_ask_counts=st.session_state.slot_ask_counts,
                as_of=config["as_of"],
                store=store,
                top_k=config["top_k"],
                extra_interests=config["extra_interests"],
                on_step=_on_step,
            )
            status.update(
                label=STATUS_LABELS_KO.get(result.kind, "처리 완료"),
                state="error" if result.kind == "error" else "complete",
                expanded=False,
            )
        # 누적 슬롯/되묻기 횟수 갱신
        st.session_state.slots = result.state.get("slots", st.session_state.slots)
        st.session_state.slot_ask_counts = result.state.get(
            "slot_ask_counts", st.session_state.slot_ask_counts
        )
        render_result(result)

    st.session_state.messages.append({"role": "assistant", "result": result})
    # 사이드바 "파악한 정보"가 이번 턴 슬롯을 바로 반영하도록 한 번 더 그린다.
    st.rerun()
