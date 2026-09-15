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
from ..session import clear_conversation_state, escape_md, logout, md_text, new_conversation
from ..theme import render_avatar

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


@st.dialog("새 상담을 시작할까요?", width="small")
def _confirm_reset_dialog() -> None:
    """"새 상담 시작" 확인 팝업 - 실수로 눌러 대화가 통째로 사라지는 걸
    막는다(2026-09-15, PR 리뷰 피드백 반영). 취소/시작 둘 다 명시적으로
    골라야 닫히므로 ``dismissible``은 기본값(True)으로 둔다 - 여기선 실수
    방지가 목적이라, 바깥 클릭으로 취소되는 것도 자연스럽다.
    """

    st.warning(
        "지금까지의 대화 내용이 모두 사라져요. 계속할까요?",
        icon=":material/delete_sweep:",
    )
    col1, col2 = st.columns(2)
    if col1.button("취소", key="confirm_reset_cancel", width="stretch"):
        st.session_state.pop("confirm_new_chat", None)
        st.rerun()
    if col2.button(
        "새 상담 시작", key="confirm_reset_go", width="stretch",
        type="primary", icon=":material/delete_sweep:",
    ):
        st.session_state.pop("confirm_new_chat", None)
        _reset_conversation()
        st.rerun()


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
    """계정 영역 + (로그인 시) 상담 관리·검색 설정을 담은 사이드바.

    ``(top_k, extra_interests)``를 돌려준다. 로그인 여부와 무관하게 항상 한
    번만 그린다 - 로그인 전에는 로그인/회원가입 버튼만, 로그인 후에는
    아바타·이름·마이페이지·로그아웃에 더해 "새 상담 시작"과 접어둔 "검색
    범위 조정"(지원조건·관심 분야 pills, 정책 후보 수)까지 보여준다.

    지원조건·관심 분야는 **검색 질의를 넓히는 힌트**이지 자격 판정 조건이
    아니다. interests는 소프트 슬롯이라 하드 게이트나 검색 필터에 쓰이지
    않는다 - 고른다고 해서 "그 조건에 해당한다"고 판정되지 않는다.

    로그인 사용자는 "지원조건" pills의 기본 선택값을 회원가입 때 저장한
    값으로 미리 채운다(``default=``는 위젯이 이 세션에서 처음 그려질 때만
    쓰이므로, 로그인 직후 한 번만 적용되고 이후엔 사용자가 고른 값이
    session_state에 남아 그대로 유지된다 - 로그인/로그아웃 경계에서
    ``clear_conversation_state()``가 위젯 키를 비워야 다음 로그인 때 다시
    적용된다). "관심 분야"는 회원가입에서 받지 않는 값이라 그대로 빈 채로
    시작한다. 어느 쪽이든 이 자리에서 더 고르거나 지우는 건 이번 상담에만
    적용되고 회원 프로필 자체를 바꾸지 않는다.
    """

    auth_user = st.session_state.get("auth_user")
    selected_conditions: list[str] = []
    selected_fields: list[str] = []
    top_k = DEFAULT_TOP_K

    with st.sidebar:
        if auth_user:
            name = auth_user.get("display_name") or auth_user.get("username", "")
            acc_row = st.container(horizontal=True, vertical_alignment="center")
            render_avatar(
                acc_row, photo=auth_user.get("avatar"), name=name, size=40,
                key="sidebar_avatar",
            )
            acc_row.markdown(f"**{escape_md(name)} 님**")
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
                width="stretch", type="secondary", key="sb_new_chat",
            ):
                st.session_state["confirm_new_chat"] = True
                st.rerun()

            with st.expander(":material/tune: 검색 범위 조정 (선택)", expanded=False):
                # 회원가입 때 고른 지원조건을 기본값으로 - 로그인 직후 첫
                # 렌더에서만 적용된다(2026-09-15, PR 리뷰 피드백 반영).
                # st.pills는 key에 이미 값이 있으면 default를 무시하므로,
                # 로그인 이후 사용자가 직접 바꾼 선택은 그대로 유지된다.
                signup_interests = [
                    i for i in (auth_user.get("interests") or []) if i in INTEREST_OPTIONS
                ]
                selected_conditions = st.pills(
                    "지원조건", INTEREST_OPTIONS, selection_mode="multi",
                    default=signup_interests, key="interests_pick",
                    help="검색 질의를 넓히는 힌트입니다. 자격 판정 조건은 아닙니다.",
                ) or []
                selected_fields = st.pills(
                    "관심 분야", INTEREST_FIELD_OPTIONS, selection_mode="multi",
                    default=[], key="fields_pick",
                ) or []
                top_k = st.slider(
                    "정책 후보 수", min_value=3, max_value=15,
                    value=DEFAULT_TOP_K, key="topk",
                )

    # 같은 값을 두 번 고른 경우(예: 두 목록에 다 있는 "장애인")를 합치되
    # 사용자가 고른 순서는 유지한다.
    extra_interests = list(dict.fromkeys([*selected_conditions, *selected_fields]))
    return top_k, extra_interests


def _known_region(auth_user: dict | None) -> str | None:
    """회원가입 때 저장한 거주 지역. "선택 안 함"이었으면 빈 문자열이라 None."""

    if not auth_user:
        return None
    return auth_user.get("region") or None


def _known_gender(auth_user: dict | None) -> str | None:
    """회원가입 때 저장한 성별. 미입력이면 빈 문자열이라 None."""

    if not auth_user:
        return None
    return auth_user.get("gender") or None


def _known_birth_date(auth_user: dict | None) -> str | None:
    """회원가입 때 저장한 생년월일(ISO). 미입력이면 빈 문자열이라 None."""

    if not auth_user:
        return None
    return auth_user.get("birth_date") or None


def _known_disability_status(auth_user: dict | None) -> str | None:
    """회원가입 때 저장한 장애 등록 여부. 미입력이면 빈 문자열이라 None."""

    if not auth_user:
        return None
    return auth_user.get("disability_status") or None


def _known_income_bracket(auth_user: dict | None) -> str | None:
    """회원가입 때 저장한 소득 수준. 미입력이면 빈 문자열이라 None."""

    if not auth_user:
        return None
    return auth_user.get("income_bracket") or None


def _known_household_types(auth_user: dict | None) -> list[str] | None:
    """회원가입 때 저장한 가구유형 목록. 미입력이면 빈 리스트라 None."""

    if not auth_user:
        return None
    return list(auth_user.get("household_types") or []) or None


def _known_veteran_status(auth_user: dict | None) -> str | None:
    """회원가입 때 저장한 보훈대상자 여부. 미입력이면 빈 문자열이라 None."""

    if not auth_user:
        return None
    return auth_user.get("veteran_status") or None


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


def _clear_detail_chat() -> None:
    """상세 문의 채팅방 상태를 비운다(닫기 버튼·모달 X 공통)."""

    st.session_state.pop("detail_chat_policy", None)
    st.session_state.pop("detail_chat_history", None)
    st.session_state.pop("_detail_chat_opened", None)


@st.dialog("정책 문의 채팅방", width="large", dismissible=False)
def _detail_chat_dialog(policy: Mapping[str, object]) -> None:
    """정책 상세 문의를 화면 한가운데 뜨는 채팅방(모달)으로 띄운다.

    메인 대화(``messages``)와 분리해 ``detail_chat_history``에 쌓는다 - 추천
    결과 화면은 그대로 두고, 특정 정책 하나만 이어서 물어본다. 모달이라
    "어디 열렸는지" 헷갈릴 일이 없다.

    ``dismissible=False``로 X 버튼·바깥 클릭·ESC를 전부 막는다(2026-09-15,
    PR 리뷰 피드백 반영) - 채팅 중간에 바깥을 실수로 클릭해 대화가 통째로
    날아가는 게 원래 문제였는데, Streamlit ``dismissible``은 세 경로를 한
    스위치로만 켜고 끌 수 있어 "바깥 클릭만" 막는 옵션은 없다. 그래서 셋 다
    막고, 닫는 길은 아래 "닫기" 버튼(``_clear_detail_chat`` 직접 호출) 하나로
    남긴다.
    """

    history = st.session_state.setdefault("detail_chat_history", [])
    title = md_text(policy.get("title") or policy.get("policy_id") or "이 정책")

    # dismissible=False라 Streamlit 기본 X 버튼이 없다 - 그 자리(우상단)에
    # "닫기"를 직접 놓는다(2026-09-15, 사용자 피드백 반영. 원래는 채팅
    # 입력창 아래에 있어서 눈에 잘 안 띄었다). 컨테이너 자체엔 좌우 정렬
    # 옵션이 없어 st-key 클래스에 CSS로 space-between을 건다(theme.py
    # 헤더·rendering.py 비교 바와 같은 패턴).
    title_row_key = "detail_chat_title_row"
    st.html(
        f"<style>[class*='st-key-{title_row_key}']{{justify-content:space-between;}}"
        f"[class*='st-key-{title_row_key}'] [data-testid='stMarkdownContainer']"
        "{margin-top:0;}</style>"
    )
    title_row = st.container(horizontal=True, vertical_alignment="center", key=title_row_key)
    title_row.markdown(f"##### :material/chat: {title}")
    if title_row.button("", key="detail_chat_exit", icon=":material/close:", type="tertiary"):
        _clear_detail_chat()
        st.rerun()

    st.caption("이 정책 전용 채팅방이에요. 신청·자격·서류 등을 물어보세요.")

    # box는 미리 만들어 두고, chat_input으로 새 질문을 먼저 history에 얹은
    # 뒤에 채운다 - 그래야 엔터 치자마자(=이번 rerun 안에서 바로) 내 질문이
    # 답변보다 먼저 화면에 보인다(2026-09-15, PR 리뷰 피드백 반영). box를
    # chat_input보다 먼저 만들어도(컨테이너는 나중에 채워도 그 자리에 그려짐)
    # 코드 순서상 chat_input은 그대로 아래쪽이라 도킹 위치는 안 바뀐다.
    box = st.container(height=360)

    typed = st.chat_input(f"{title}에 대해 물어보세요", key="detail_chat_input")
    if typed:
        history.append({"role": "user", "content": typed})

    if not history:
        box.caption("예: “신청은 어디서 하나요?”, “제가 서울 사는데 대상인가요?”")
    for message in history:
        avatar = USER_AVATAR if message["role"] == "user" else BOT_AVATAR
        with box.chat_message(message["role"], avatar=avatar):
            if "light_answer" in message:
                _render_light_answer(message["light_answer"])
            else:
                st.markdown(message["content"])

    # 마지막 메시지가 아직 답 없는 사용자 질문이면(= 방금 얹혔으면) 로딩
    # 말풍선을 먼저 그린 뒤(이 시점에 이미 브라우저로 전송된다 - Streamlit은
    # 스크립트가 끝나길 기다리지 않고 만들어지는 대로 델타를 보낸다) 무거운
    # 호출을 이어서 한다. 답이 오면 rerun해서 로딩 말풍선을 실제 답으로
    # 바꾼다.
    if history and history[-1]["role"] == "user":
        with box.chat_message("assistant", avatar=BOT_AVATAR):
            st.markdown(":material/search: 정책 검색 중…")
        _answer_detail_chat_turn(policy, history)


def _answer_detail_chat_turn(policy: Mapping[str, object], history: list) -> None:
    """직전에 쌓인 사용자 질문 한 턴에 답한다 - 무거운 파이프라인 없이 경량
    응답으로.

    호출 시점에는 이미 로딩 말풍선이 그려진 뒤다(``_detail_chat_dialog``
    참고). 답이 오면 history에 붙이고 rerun해서 로딩 말풍선을 실제 답으로
    바꾼다.
    """

    from src.rag_chatbot.light_followup import respond_to_policy_question
    from src.rag_chatbot.service import get_llm_client

    prompt = str(history[-1]["content"])

    # 사이드바 "파악한 정보"(지역·나이·소득 등)를 함께 넘겨 "우리 지역도
    # 되나요?" 같은 질문에 답할 수 있게 한다. 답변 완료 후에도 세션에 남는다
    # (_remember_profile).
    profile = st.session_state.get("profile") or []

    try:
        client = get_llm_client()
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

# 기준중위소득 대비 비율(소득인정액)은 가구원 수·공제·재산환산에 따라 달라져서
# 우리가 "연봉 얼마"로 직접 환산해 보여줄 수 없다(IncomeBracket 참고,
# src/rag_chatbot/graph/slot_schema.py). 대신 복지로의 공식 모의계산 페이지로
# 안내한다 - 가구 정보를 입력하면 실제 소득 분위를 계산해 준다.
_INCOME_CALCULATOR_URL = "https://www.bokjiro.go.kr/ssis-tbu/twatbz/mkclAsis/mkclPage.do"


def _slot_widget(slot: str, label: str, *, default: str | None = None):
    """missing_slot 하나를 알맞은 위젯으로 그린다(자유 입력 대신).

    ``default``는 지역 충돌(``region_conflict``) 재확인 때만 쓴다 - 채팅에서
    사용자가 직접 말한 지역을 빈 선택지가 아니라 이미 체크된 상태로 보여주고,
    그래도 수정할 수 있게 선택형 위젯 그대로 둔다(2026-09-15, 사용자 요청
    반영 - 원래는 충돌 시 빈 선택지로 되물었는데, 사용자가 방금 채팅에 직접
    입력한 값이니 자동으로 골라져 있는 게 더 자연스럽다는 피드백).
    """

    if slot == "region":
        options = ("선택하세요", *SIDO_OPTIONS)
        index = options.index(default) if default in SIDO_OPTIONS else 0
        return st.selectbox(label, options, index=index, key=f"slotw_{slot}")
    if slot == "birth_date":
        return st.date_input(
            label, value=None, min_value=date(1920, 1, 1), max_value=date.today(),
            format="YYYY-MM-DD", key=f"slotw_{slot}",
        )
    if slot == "gender":
        return st.radio(label, ("남성", "여성"), index=None, horizontal=True, key=f"slotw_{slot}")
    if slot == "income_bracket":
        value = st.selectbox(label, ("선택하세요", *_INCOME_CHOICES), key=f"slotw_{slot}")
        st.caption(
            "본인 소득 분위를 모르면 "
            f"[나의 소득 분위 알아보기]({_INCOME_CALCULATOR_URL})에서 확인할 수 있어요."
        )
        return value
    if slot == "disability_status":
        return st.radio(
            label, ("장애 없음", "장애 등록", "모름"), index=None, horizontal=True,
            key=f"slotw_{slot}",
        )
    if slot == "employment_status":
        return st.radio(label, _EMPLOYMENT_CHOICES, index=None, horizontal=True, key=f"slotw_{slot}")
    return st.text_input(label, key=f"slotw_{slot}")


def _render_slot_form(
    missing_slots: list[str],
    region_conflict: Mapping[str, str] | None = None,
) -> str | None:
    """되묻기(needs_input)에 자유 입력 대신 위젯 폼으로 답하게 한다.

    제출하면 각 값을 ``라벨: 값`` 줄로 이어붙여 돌려준다(파이프라인 N1이 다시
    파싱). 폼을 안 쓰고 아래 채팅창에 직접 입력해도 된다.

    질문 문구 자체는 이 폼이 아니라 바로 위 채팅 말풍선(``_render_history``
    -> ``render_result``)이 이미 보여준다 - 예전엔 이 폼도 같은 문구를 헤더로
    한 번 더 찍어서, 특히 지역 충돌 재확인처럼 문구가 길 때 화면에 똑같은
    문장이 두 번 나왔다(2026-09-15, 사용자 피드백 - "이게 반복되는거 보여?"
    지적 반영). 폼 바로 위에 그 문구가 이미 있으므로 여기서는 위젯만 그린다.

    ``region``만은 회원가입 때 이미 알고 있으면(``auth_user.region``) 위젯을
    아예 안 그리고 그 값을 자동으로 제출한다(2026-09-15, PR 리뷰 피드백
    반영) - 로그인 안내 화면이 "회원가입 때 입력한 지역이 자동으로
    반영됩니다"라고 약속하는데 실제로는 매번 다시 물어봐서 생긴 공백이었다.
    생년월일·성별·소득·장애·취업상태는 회원가입 때 아예 안 받으므로 그대로
    묻는다.

    ``region_conflict``가 있으면(채팅에서 말한 지역이 회원 프로필과 달라
    파이프라인이 되묻는 경우, service.ChatResponse.region_conflict 참고)
    자동 채움(위젯 없이 프로필 값을 바로 재제출)은 끈다(2026-09-15 추가) -
    안 그러면 사용자가 충돌 사실을 확인할 기회도 없이 조용히 "해결"돼버린다.
    대신 위젯은 그리되, 채팅에서 방금 말한 지역(``region_conflict["chat"]``)을
    기본 선택값으로 미리 체크해 둔다(2026-09-15, 사용자 요청 반영 - 처음엔
    빈 선택지로 되물었는데, "다시 물어보되 채팅에서 입력한 값으로 자동
    체크돼서 수정 가능하게" 해달라는 피드백을 받음). 충돌 사실은 위 채팅
    말풍선의 문구(request_missing_slots.py가 조립)로 이미 알려줬으니,
    사용자는 필요하면 선택값을 프로필 쪽 지역으로 직접 바꾸면 된다.
    """

    slots = [s for s in missing_slots if isinstance(s, str)]
    if not slots:
        return None

    auth_user = st.session_state.get("auth_user") or {}
    known_region = str(auth_user.get("region") or "").strip()
    skip_region = bool(known_region) and "region" in slots and not region_conflict

    with st.container(border=True):
        if skip_region:
            st.caption(
                f":material/check_circle: 거주 지역은 회원가입 정보"
                f"(**{known_region}**)를 사용할게요."
            )
        with st.form("slot_form", border=False, clear_on_submit=True):
            values: dict[str, object] = {}
            for slot in slots:
                if slot == "region" and skip_region:
                    continue
                default = (
                    region_conflict.get("chat")
                    if slot == "region" and region_conflict
                    else None
                )
                values[slot] = _slot_widget(
                    slot, SLOT_LABELS_KO.get(slot, slot), default=default
                )
            submitted = st.form_submit_button(
                "이 정보로 계속", type="primary", width="stretch"
            )

    if not submitted:
        return None

    parts: list[str] = []
    if skip_region:
        parts.append(f"{SLOT_LABELS_KO.get('region', '거주 지역')}: {known_region}")
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


def page_chat() -> None:
    # 사이드바(계정 버튼 + 로그인 시 검색 설정)는 항상 한 번만 그린다.
    top_k, extra_interests = _render_sidebar()

    # "새 상담 시작" 확인 팝업 - detail_chat_policy와 같은 패턴(세션 상태
    # 플래그로 열림 여부를 표시하고, 매 rerun마다 여기서 다시 확인한다).
    if st.session_state.get("confirm_new_chat"):
        _confirm_reset_dialog()

    # 로그인 게이트 - 로그인 전에는 상담 불가.
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

    # 되묻기는 위젯 폼으로 - 자유 입력 대신 골라서 답한다.
    needs_input = _active_needs_input()
    form_answer = (
        _render_slot_form(
            list(needs_input.get("missing_slots") or []),
            needs_input.get("region_conflict"),
        )
        if needs_input
        else None
    )

    # 정책 상세 문의는 화면 한가운데 뜨는 채팅방(모달)으로.
    if st.session_state.get("detail_chat_policy"):
        _detail_chat_dialog(st.session_state["detail_chat_policy"])

    # 메인 채팅 입력창을 포인트 컬러 테두리 + 그림자로 눈에 띄게 한다 - 기본
    # 상태로는 연한 회색 테두리라 화면 맨 아래로 시선이 잘 안 간다.
    st.html(
        "<style>[data-testid='stChatInput']{border:1.5px solid #4F46E5;"
        "border-radius:14px;box-shadow:0 2px 10px rgba(79,70,229,.12);}</style>"
    )
    typed = st.chat_input("메시지를 입력하세요 (예: 서울에 사는 30대 직장인입니다)")
    pending = st.session_state.pop("pending_prompt", None)
    prompt = typed or form_answer or pending
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(prompt)

    result: Mapping[str, object] | None = None
    error_message: str | None = None
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        started = time.perf_counter()
        try:
            auth_user = st.session_state.get("auth_user") or {}
            result = _run_with_progress(
                user_input=prompt,
                session_id=st.session_state.conversation_id,
                awaiting_followup=st.session_state.awaiting_followup,
                top_k=top_k,
                extra_interests=extra_interests,
                known_region=_known_region(auth_user),
                known_gender=_known_gender(auth_user),
                known_birth_date=_known_birth_date(auth_user),
                known_disability_status=_known_disability_status(auth_user),
                known_income_bracket=_known_income_bracket(auth_user),
                known_household_types=_known_household_types(auth_user),
                known_veteran_status=_known_veteran_status(auth_user),
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
