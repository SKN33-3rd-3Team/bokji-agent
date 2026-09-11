"""N1~N14 공식 서비스에 연결된 상담 화면."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from datetime import date

import streamlit as st

from src.rag_chatbot.timing import EXPECTED_NODE_COUNT, TIMER

from ..constants import (
    BOT_AVATAR,
    DEFAULT_TOP_K,
    EXAMPLE_PROMPTS,
    INTEREST_FIELD_OPTIONS,
    INTEREST_OPTIONS,
    SIDO_OPTIONS,
    SLOT_LABELS_KO,
    USER_AVATAR,
    VECTOR_DB_DIR,
)
from ..nav import goto
from ..pipeline import run_pipeline
from ..rendering import render_result
from ..session import (
    clear_conversation_state,
    escape_md,
    logout,
    md_text,
    new_conversation,
)

_GENERIC_ERROR_MESSAGE = (
    "상담 처리 중 오류가 발생했습니다. 잠시 후 다시 시도하거나 대화를 초기화해 주세요."
)
_SETUP_ERROR_MESSAGE = "서비스 실행 설정을 확인할 수 없습니다. 관리자에게 문의해 주세요."

# 화면에는 일반 문구만 보여주되(내부 정보·비밀값 노출 금지), 원인은 터미널에
# 남긴다. 이렇게 하지 않으면 렌더링이 깨져도 "오류가 발생했습니다"만 뜨고
# 무엇이 잘못됐는지 아무도 알 수 없다.
_LOG = logging.getLogger("bokji.streamlit")

# 진행 상황을 다시 그리는 주기. 너무 짧으면 브라우저로 보내는 메시지만 늘고,
# 너무 길면 노드가 바뀐 걸 늦게 안다. 노드 하나가 수십 초 걸리는 파이프라인
# 이라 0.4초면 충분하다.
_PROGRESS_POLL_SECONDS = 0.4


def _reset_conversation() -> None:
    clear_conversation_state()


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
                preview,
                key=f"ex_{idx}",
                width="stretch",
                icon=":material/bolt:",
            ):
                st.session_state.pending_prompt = example
                st.rerun()


def _render_sidebar() -> tuple[int, list[str]]:
    """설정 사이드바. ``(top_k, extra_interests)``를 돌려준다.

    지원조건·관심 분야는 **검색 질의를 넓히는 힌트**이지 자격 판정 조건이
    아니다. interests는 소프트 슬롯이라 하드 게이트나 검색 필터에 쓰이지
    않는다 - 고른다고 해서 "그 조건에 해당한다"고 판정되지 않는다.
    """

    auth_user = st.session_state.get("auth_user")
    selected_conditions: list[str] = []
    selected_fields: list[str] = []
    top_k = DEFAULT_TOP_K

    with st.sidebar:
        st.markdown(":material/account_circle: **계정**")
        if auth_user:
            name = auth_user.get("display_name") or auth_user.get("username", "")
            st.caption(f":material/check_circle: {escape_md(name)} 님")
            if st.button("마이페이지", icon=":material/person:", width="stretch", key="sb_mypage"):
                goto("mypage")
            if st.button(
                "로그아웃", icon=":material/logout:", width="stretch",
                key="sb_logout", type="secondary",
            ):
                logout()
                st.rerun()
        else:
            account = st.container(horizontal=True)
            if account.button("로그인", icon=":material/login:", width="stretch", key="sb_login"):
                goto("login")
            if account.button("회원가입", icon=":material/person_add:", width="stretch", key="sb_signup"):
                goto("signup")

        # 로그인한 사용자에게만: 상담 관리 + (접어둔) 검색 범위 조정.
        if auth_user:
            st.divider()
            if st.button(
                "새 상담 시작", icon=":material/delete_sweep:",
                width="stretch", type="secondary",
            ):
                _reset_conversation()
                st.rerun()

            with st.expander(":material/tune: 검색 범위 조정 (선택)", expanded=False):
                selected_conditions = st.multiselect(
                    "지원조건", INTEREST_OPTIONS, default=[], key="interests_pick",
                    placeholder="해당하는 조건",
                    help="검색 질의를 넓히는 힌트입니다. 자격 판정 조건은 아닙니다.",
                )
                selected_fields = st.multiselect(
                    "관심 분야", INTEREST_FIELD_OPTIONS, default=[], key="fields_pick",
                    placeholder="관심 분야",
                )
                top_k = st.slider(
                    "정책 후보 수", min_value=3, max_value=15,
                    value=DEFAULT_TOP_K, key="topk",
                )

    # 같은 값을 두 번 고른 경우(예: 두 목록에 다 있는 "장애인")를 합치되
    # 사용자가 고른 순서는 유지한다.
    extra_interests = list(dict.fromkeys([*selected_conditions, *selected_fields]))
    return top_k, extra_interests


def _render_profile_sidebar() -> None:
    """서비스가 파악한 슬롯을 사이드바에 보여준다.

    값은 응답의 ``output_json["profile"]``을 그대로 쓴다 - 화면에서 슬롯
    코드값을 한글로 바꾸지 않는다(서비스가 이미 라벨까지 만들어 준다).
    생년월일 원문은 여기 오지 않는다(service._build_profile 참고).
    """

    profile = st.session_state.get("profile") or []
    if not profile:
        return

    with st.sidebar:
        st.markdown(":material/badge: **파악한 정보**")
        for item in profile:
            if not isinstance(item, Mapping):
                continue
            # 소득 라벨에 "중위소득 30~50%" 같은 범위 표기가 들어온다.
            # Markdown 취소선(~~)으로 읽히지 않게 - 로 바꾼 뒤 이스케이프한다.
            label = escape_md(md_text(item.get("label")))
            value = escape_md(md_text(item.get("value")))
            if label and value:
                st.markdown(f"- {label}: {value}")


def _remember_profile(result: Mapping[str, object]) -> None:
    """이번 응답이 파악한 정보를 사이드바용으로 보관한다.

    되묻는 중(needs_input)에도 값이 오므로 매 턴 갱신한다. 응답에 profile이
    없으면(예전 계약) 직전 값을 그대로 둔다 - 있던 정보가 갑자기 사라지는
    것보다 낫다.
    """

    output_json = result.get("output_json")
    if isinstance(output_json, Mapping) and "profile" in output_json:
        profile = output_json.get("profile")
        st.session_state.profile = list(profile) if isinstance(profile, list) else []


def _run_with_progress(**kwargs):
    """파이프라인을 워커 스레드에서 돌리고, 메인 스레드는 진행률만 그린다.

    ``run_pipeline``은 한 번 부르면 수 분간 돌아오지 않는다. 멈춘 건지 도는
    건지 알 수 있게 진행 막대와 경과 시간만 보여준다 - 노드 이름·개수 같은
    내부 정보는 화면에 내지 않는다(사용자에게 필요한 것만). 워커 스레드는
    Streamlit API를 전혀 건드리지 않는다.

    진행률은 **어림값**이다 - 조건부 분기 때문에 전체 노드 수는 끝나봐야
    알므로, 끝나기 전에는 95%를 넘기지 않는다.
    """

    bar = st.progress(0.0, text="지원 제도를 찾고 있어요…")

    # 이전 요청의 기록이 남아 있으면 시작하자마자 진행률이 100%로 보인다.
    TIMER.reset()

    outcome: dict = {}

    def _work() -> None:
        try:
            outcome["result"] = run_pipeline(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - 메인 스레드에서 다시 던진다
            outcome["error"] = exc

    worker = threading.Thread(target=_work, name="bokji-pipeline", daemon=True)
    started = time.perf_counter()
    worker.start()

    while True:
        alive = worker.is_alive()
        fraction = (
            min(len(TIMER.path()) / EXPECTED_NODE_COUNT, 0.95) if alive else 1.0
        )
        bar.progress(
            fraction,
            text=f"지원 제도를 찾고 있어요…  ({time.perf_counter() - started:.0f}초)",
        )
        if not alive:
            break
        worker.join(timeout=_PROGRESS_POLL_SECONDS)

    bar.empty()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")


def _log_elapsed(result: Mapping[str, object], wall_seconds: float) -> None:
    """이번 요청에 걸린 시간을 콘솔에 한 줄로 남긴다.

    ``BOKJI_TRACE=1``은 노드별로 찍지만 총합은 안 찍는다. 총 소요를 매번
    직접 더해 보게 만들 이유가 없다. 화면을 안 보고 터미널만 볼 때도 이
    한 줄이면 이번 요청이 얼마나 걸렸는지 알 수 있다.
    """

    timing = result.get("timing")
    total = timing.get("phases") if isinstance(timing, Mapping) else None
    measured = None
    for phase in total or []:
        if isinstance(phase, Mapping) and phase.get("name") == "request_total":
            measured = phase.get("total_s")
            break

    node_path = (timing or {}).get("node_path") if isinstance(timing, Mapping) else []
    parts = [f"총 소요 {wall_seconds:.2f}초"]
    if isinstance(measured, (int, float)):
        # wall time 은 UI 폴링/렌더까지 포함하고, request_total 은 서비스
        # 내부만 잰다. 둘이 크게 벌어지면 화면 쪽에서 시간을 쓴 것이다.
        parts.append(f"서비스 내부 {float(measured):.2f}초")
    parts.append(f"노드 {len(node_path or [])}개")
    print("[bokji] " + " · ".join(parts), flush=True)


def _render_result_safely(result: Mapping[str, object]) -> None:
    try:
        render_result(result)
    except Exception:  # 내부 예외나 비밀값은 화면에 노출하지 않는다.
        _LOG.exception("결과 렌더링 실패 (status=%r)", result.get("status"))
        st.error(_GENERIC_ERROR_MESSAGE, icon=":material/error:")


def _render_light_answer(out: Mapping[str, object]) -> None:
    """정책 상세 채팅의 경량 응답(``light_followup.respond_to_policy_question``
    결과)을 그린다."""

    text = str(out.get("text") or "")
    if out.get("kind") == "guidance":
        st.info(text, icon=":material/info:")
        return
    st.markdown(text)
    quotes = [str(q) for q in (out.get("evidence_quotes") or []) if q]
    if quotes:
        with st.expander("보충자료", icon=":material/source:"):
            for quote in quotes:
                st.markdown(f"- {md_text(quote)}")


def _render_history() -> None:
    for message in st.session_state.messages:
        avatar = USER_AVATAR if message["role"] == "user" else BOT_AVATAR
        with st.chat_message(message["role"], avatar=avatar):
            if message["role"] == "user":
                st.markdown(message["content"])
            elif message.get("error"):
                st.error(message["error"], icon=":material/error:")
            else:
                _render_result_safely(message["result"])


def _clear_detail_chat() -> None:
    """상세 문의 채팅방 상태를 비운다(닫기 버튼·모달 X 공통)."""

    st.session_state.pop("detail_chat_policy", None)
    st.session_state.pop("detail_chat_history", None)
    st.session_state.pop("_detail_chat_opened", None)


@st.dialog("정책 문의 채팅방", width="large", on_dismiss=_clear_detail_chat)
def _detail_chat_dialog(policy: Mapping[str, object]) -> None:
    """정책 상세 문의를 화면 한가운데 뜨는 채팅방(모달)으로 띄운다.

    메인 대화(``messages``)와 분리해 ``detail_chat_history``에 쌓는다 - 추천
    결과 화면은 그대로 두고, 특정 정책 하나만 이어서 물어본다. 모달이라
    "어디 열렸는지" 헷갈릴 일이 없다. X 또는 "닫기"로 나간다.
    """

    history = st.session_state.setdefault("detail_chat_history", [])
    title = md_text(policy.get("title") or policy.get("policy_id") or "이 정책")

    st.markdown(f"##### :material/chat: {title}")
    st.caption("이 정책 전용 채팅방이에요. 신청·자격·서류 등을 물어보세요.")

    box = st.container(height=360)
    if not history:
        box.caption("예: “신청은 어디서 하나요?”, “제가 서울 사는데 대상인가요?”")
    for message in history:
        avatar = USER_AVATAR if message["role"] == "user" else BOT_AVATAR
        with box.chat_message(message["role"], avatar=avatar):
            if "light_answer" in message:
                _render_light_answer(message["light_answer"])
            else:
                st.markdown(message["content"])

    typed = st.chat_input(f"{title}에 대해 물어보세요", key="detail_chat_input")
    if st.button("닫기", key="detail_chat_exit", icon=":material/close:"):
        _clear_detail_chat()
        st.rerun()

    if typed:
        _handle_detail_chat_turn(policy, typed)


def _handle_detail_chat_turn(policy: Mapping[str, object], prompt: str) -> None:
    """정책 상세 문의 한 턴 - 무거운 파이프라인 없이 경량 응답으로 답한다.

    결과는 ``detail_chat_history``에만 쌓고 rerun한다. 사이드바 패널이 다음
    실행에서 그 기록을 다시 그린다.
    """

    from src.rag_chatbot.light_followup import respond_to_policy_question
    from src.rag_chatbot.service import get_llm_client

    history = st.session_state.setdefault("detail_chat_history", [])
    history.append({"role": "user", "content": prompt})

    # 사이드바 "파악한 정보"(지역·나이·소득 등)를 함께 넘겨 "우리 지역도
    # 되나요?" 같은 질문에 답할 수 있게 한다. 답변 완료 후에도 세션에 남는다
    # (_remember_profile).
    profile = st.session_state.get("profile") or []

    try:
        client = get_llm_client()
        with st.spinner("정책 내용을 확인하고 있어요"):
            out = respond_to_policy_question(
                policy, prompt, llm_client=client, user_profile=profile
            )
    except Exception:  # 내부 정보·비밀값은 화면에 노출하지 않는다.
        _LOG.exception("정책 상세 채팅 응답 실패")
        out = {
            "kind": "guidance",
            "text": _GENERIC_ERROR_MESSAGE,
            "evidence_quotes": [],
        }

    history.append({"role": "assistant", "light_answer": out})
    st.rerun()


# ── 로그인 게이트 ─────────────────────────────────────────────────────


def _render_login_required() -> None:
    """로그인 전에는 상담 화면 대신 안내만 보여준다."""

    with st.container(border=True):
        st.markdown(
            "#### :material/lock: 로그인이 필요해요\n"
            "상담을 시작하려면 먼저 로그인해 주세요. 회원가입 때 입력한 지역·"
            "관심사가 상담에 자동으로 반영됩니다."
        )
        row = st.container(horizontal=True)
        if row.button("로그인", icon=":material/login:", type="primary", key="gate_login"):
            goto("login")
        if row.button("회원가입", icon=":material/person_add:", key="gate_signup"):
            goto("signup")


# ── 추가 정보 위젯 폼 (needs_input) ───────────────────────────────────

_INCOME_CHOICES = (
    "기초생활수급 수준(중위소득 30% 이하)",
    "차상위 수준(중위소득 30-50%)",
    "중위소득 50-75%",
    "중위소득 75-100%",
    "중위소득 100-150%",
    "중위소득 150% 초과",
)
_EMPLOYMENT_CHOICES = ("재직", "구직", "자영업", "학생", "무직")


def _slot_widget(slot: str, label: str):
    """missing_slot 하나를 알맞은 위젯으로 그린다(자유 입력 대신)."""

    if slot == "region":
        return st.selectbox(label, ("선택하세요", *SIDO_OPTIONS), key=f"slotw_{slot}")
    if slot == "birth_date":
        return st.date_input(
            label, value=None, min_value=date(1920, 1, 1), max_value=date.today(),
            format="YYYY-MM-DD", key=f"slotw_{slot}",
        )
    if slot == "gender":
        return st.radio(label, ("남성", "여성"), index=None, horizontal=True, key=f"slotw_{slot}")
    if slot == "income_bracket":
        return st.selectbox(label, ("선택하세요", *_INCOME_CHOICES), key=f"slotw_{slot}")
    if slot == "disability_status":
        return st.radio(
            label, ("장애 없음", "장애 등록", "모름"), index=None, horizontal=True,
            key=f"slotw_{slot}",
        )
    if slot == "employment_status":
        return st.radio(label, _EMPLOYMENT_CHOICES, index=None, horizontal=True, key=f"slotw_{slot}")
    return st.text_input(label, key=f"slotw_{slot}")


def _render_slot_form(question: str, missing_slots: list[str]) -> str | None:
    """되묻기(needs_input)에 자유 입력 대신 위젯 폼으로 답하게 한다.

    제출하면 각 값을 ``라벨: 값`` 줄로 이어붙여 돌려준다(파이프라인 N1이 다시
    파싱). 폼을 안 쓰고 아래 채팅창에 직접 입력해도 된다.
    """

    slots = [s for s in missing_slots if isinstance(s, str)]
    if not slots:
        return None

    with st.container(border=True):
        st.markdown(f":material/help: {question or '아래 정보를 알려주시면 맞춤 제도를 찾아드려요.'}")
        with st.form("slot_form", border=False, clear_on_submit=True):
            values: dict[str, object] = {}
            for slot in slots:
                values[slot] = _slot_widget(slot, SLOT_LABELS_KO.get(slot, slot))
            submitted = st.form_submit_button(
                "이 정보로 계속", type="primary", width="stretch"
            )

    if not submitted:
        return None

    parts: list[str] = []
    for slot, val in values.items():
        if val in (None, "", "선택하세요"):
            continue
        label = SLOT_LABELS_KO.get(slot, slot)
        parts.append(f"{label}: {val.isoformat() if isinstance(val, date) else val}")
    return "\n".join(parts) or None


def _active_needs_input() -> Mapping[str, object] | None:
    """직전 응답이 아직 답 안 한 되묻기면 그 result를 돌려준다."""

    if not st.session_state.get("awaiting_followup"):
        return None
    messages = st.session_state.get("messages") or []
    if not messages:
        return None
    result = messages[-1].get("result") if isinstance(messages[-1], Mapping) else None
    if isinstance(result, Mapping) and result.get("status") == "needs_input":
        return result
    return None


# ── 메인 화면 ────────────────────────────────────────────────────────


def _run_consultation(prompt: str, *, top_k: int, extra_interests: list[str]) -> None:
    """상담 한 턴: 파이프라인 실행 → 결과 저장 → rerun."""

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(prompt)

    result: Mapping[str, object] | None = None
    error_message: str | None = None
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        started = time.perf_counter()
        try:
            result = _run_with_progress(
                user_input=prompt,
                session_id=st.session_state.conversation_id,
                awaiting_followup=st.session_state.awaiting_followup,
                top_k=top_k,
                extra_interests=extra_interests,
            )
        except SystemExit:
            _LOG.exception("서비스 실행 설정 오류")
            error_message = _SETUP_ERROR_MESSAGE
        except Exception:  # 서비스 내부 정보나 traceback은 화면에 노출하지 않는다.
            _LOG.exception("상담 파이프라인 실패")
            error_message = _GENERIC_ERROR_MESSAGE

        if result is not None:
            _log_elapsed(result, time.perf_counter() - started)

        if error_message:
            st.error(error_message, icon=":material/error:")
        elif result is not None:
            _render_result_safely(result)

    if error_message:
        st.session_state.messages.append(
            {"role": "assistant", "error": error_message}
        )
    elif result is not None:
        st.session_state.messages.append({"role": "assistant", "result": dict(result)})
        if result.get("status") == "needs_input":
            st.session_state.awaiting_followup = True
        elif result.get("status") == "answered":
            new_conversation(st.session_state, clear_messages=False)
        # new_conversation()이 profile을 비우므로 반드시 그 뒤에 보관한다.
        # 답이 나온 뒤에도 "무슨 정보로 판단했는지"는 화면에 남아 있어야 한다.
        _remember_profile(result)

    st.rerun()


def page_chat() -> None:
    # 사이드바(계정 버튼 + 로그인 시 검색 설정)는 항상 한 번만 그린다.
    top_k, extra_interests = _render_sidebar()

    # 1) 로그인 게이트 - 로그인 전에는 상담 불가.
    if not st.session_state.get("auth_user"):
        st.caption("복지 에이전트")
        _render_login_required()
        return

    st.caption(
        "거주 지역·기본 정보를 바탕으로 지원 제도를 찾아 자격·지원금·중복수급을 "
        "근거와 함께 확인합니다."
    )
    _render_profile_sidebar()

    if not (VECTOR_DB_DIR / "chroma.sqlite3").is_file():
        st.error(
            "서비스 데이터베이스가 준비되지 않았습니다. 관리자에게 문의해 주세요.",
            icon=":material/database_off:",
        )
        return

    _render_history()
    if not st.session_state.messages and not st.session_state.pending_prompt:
        _render_intro()

    # 2) 되묻기는 위젯 폼으로 - 자유 입력 대신 골라서 답한다.
    needs_input = _active_needs_input()
    form_answer = (
        _render_slot_form(
            str(needs_input.get("question") or ""),
            list(needs_input.get("missing_slots") or []),
        )
        if needs_input
        else None
    )

    # 4) 정책 상세 문의는 화면 한가운데 뜨는 채팅방(모달)으로.
    if st.session_state.get("detail_chat_policy"):
        _detail_chat_dialog(st.session_state["detail_chat_policy"])

    typed = st.chat_input("메시지를 입력하세요 (예: 서울에 사는 30대 직장인입니다)")
    pending = st.session_state.pop("pending_prompt", None)
    prompt = typed or form_answer or pending
    if not prompt:
        return

    _run_consultation(prompt, top_k=top_k, extra_interests=extra_interests)
