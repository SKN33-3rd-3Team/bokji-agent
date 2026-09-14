"""공식 ``ChatResponse``를 Streamlit 위젯으로 렌더링한다."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from html import escape as _esc_html
from typing import Any

import streamlit as st

from .constants import (
    GUIDANCE_OFFICIAL,
    SLOT_LABELS_KO,
)
from .session import md_text


# ``st.metric(icon=...)``는 Streamlit 1.5x 이후에만 있다.
# ``requirements-streamlit.txt``는 1.62.0을 고정하지만, 설치된 버전이 더 낮은
# 환경에서 **장식용 아이콘 하나 때문에 답변 전체가 렌더링되지 않는 건**
# (TypeError -> "상담 처리 중 오류가 발생했습니다") 과하다. 예외를 삼켜
# 감추는 대신 시그니처를 한 번 보고 넘길지 말지 정한다.
_METRIC_SUPPORTS_ICON = "icon" in inspect.signature(st.metric).parameters


def _metric(container: Any, label: str, value: str, *, icon: str) -> None:
    kwargs: dict[str, Any] = {"border": True}
    if _METRIC_SUPPORTS_ICON:
        kwargs["icon"] = icon
    container.metric(label, value, **kwargs)


def _render_llm_status(status: Mapping[str, Any] | None) -> None:
    if not status:
        return

    if not status.get("enabled"):
        st.caption(
            ":material/info: AI 모델을 사용하지 않고 규칙 기반·템플릿 경로로 처리했습니다."
        )
        return

    model = status.get("model") or "설정된 모델"
    calls = status.get("calls")
    failures = status.get("failures") or 0
    successes = status.get("successes")
    if failures:
        st.warning(
            f"AI 모델 호출 일부가 실패해 규칙 기반 결과로 보완했습니다. "
            f"({model}, 성공 {successes or 0}회 / 실패 {failures}건)",
            icon=":material/warning:",
        )
        return

    if calls:
        st.caption(f":material/smart_toy: AI 분석 적용 · {model} · {calls}회 호출")
    else:
        st.caption(f":material/smart_toy: AI 모델 준비됨 · {model}")


def _render_duplicate_detail(policy: Mapping[str, Any]) -> None:
    """중복수급 판정의 근거를 성격에 맞는 문구로 보여준다.

    원천 문서의 "중복" 표현은 세 종류가 섞여 있어서(N11 duplicate_benefit.py
    참고) 한 문구로 뭉뚱그리면 오해를 부른다. "1가구 1회"를 "다른 제도와
    중복 불가"로 읽게 두지 않는 것이 이 함수의 목적이다.

      other + 상대 확인   -> 상대 정책 이름을 그대로 보여준다
      other + 상대 미상   -> "다른 제도와 제한 있음, 확인 필요"
      household           -> 중복수급이 아니라 신청 횟수 제한으로 따로 안내
      header              -> 조건이 안 적혀 있으니 공식 문서로 안내
    """

    kind = policy.get("duplicate_clause_kind")
    conflicts = [
        item for item in policy.get("duplicate_conflicts") or [] if isinstance(item, Mapping)
    ]

    if conflicts:
        names = ", ".join(
            str(item.get("title") or item.get("policy_id")) for item in conflicts
        )
        st.error(
            f"아래 제도와 함께 받을 수 없습니다 — {names}",
            icon=":material/block:",
        )
    elif kind == "other":
        st.warning(
            "다른 제도와 중복수급이 제한됩니다. 어떤 제도인지는 문서에 특정돼 있지 "
            "않으니 신청 기관에 확인해 주세요.",
            icon=":material/help:",
        )
    elif kind == "header":
        st.info(
            "문서에 중복수혜 제한 항목이 표시돼 있으나 구체적인 조건이 적혀 있지 "
            "않습니다. 공식 문서에서 직접 확인해 주세요.",
            icon=":material/description:",
        )
        _render_official_link(policy)

    for clause in policy.get("household_limit_clauses") or []:
        st.caption(f":material/counter_1: 신청 횟수·가구 제한: {clause}")

    duplicate_note = policy.get("duplicate_note")
    # 위에서 이미 같은 내용을 문구로 냈으면 원문을 두 번 보여주지 않는다.
    if duplicate_note and not conflicts and kind not in ("other", "header"):
        st.caption(str(duplicate_note))


def _render_official_link(policy: Mapping[str, Any]) -> None:
    """정책 공식 원문 링크. (c) 안내에서 "직접 확인"의 실제 경로를 준다."""

    source_url = (policy.get("detail") or {}).get("source_url")
    if source_url:
        st.link_button(
            "공식 문서에서 중복수혜 조건 확인",
            str(source_url),
            icon=":material/open_in_new:",
        )


def _html_text(value: object) -> str:
    """자유 텍스트(정책 원문·LLM 생성문)를 raw HTML에 안전하게 끼워 넣는다.

    ``st.html``은 ``st.markdown``과 달리 이스케이프를 대신 해 주지 않는다.
    """

    return _esc_html(md_text(value), quote=True)


def _is_dark_theme() -> bool:
    try:
        return getattr(st.context.theme, "type", "light") == "dark"
    except Exception:  # noqa: BLE001 - 컨텍스트 없으면 라이트로
        return False


# 시안(design canvas)과 ``.streamlit/config.toml``의 판정 색을 그대로 맞춘 값.
# 라이트는 config.toml [theme.light], 다크는 [theme.dark]를 그대로 옮긴 것이고
# bg/panel/text 계열만 시안 톤에 맞춰 보탰다.
_PALETTE: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#FCFCFD", "panel": "#FFFFFF", "text": "#1E2233", "text_muted": "#5B6072",
        "border": "#E4E6EF", "primary": "#4F46E5", "primary_soft": "#EEF2FF",
        "green": "#059669", "green_bg": "#D1FAE5", "green_text": "#065F46",
        "amber": "#D97706", "amber_bg": "#FEF3C7", "amber_text": "#92400E",
        "red": "#DC2626", "red_bg": "#FEE2E2", "red_text": "#991B1B",
        "gray_bg": "#EEF1F6", "gray_text": "#475569",
        "diff_bg": "#FFFBEB",
    },
    "dark": {
        "bg": "#0D1117", "panel": "#171B26", "text": "#E6E9F2", "text_muted": "#9AA3B8",
        "border": "#2A2F3C", "primary": "#6366F1", "primary_soft": "#1E1B3A",
        "green": "#34D399", "green_bg": "#0C2E24", "green_text": "#6EE7B7",
        "amber": "#FBBF24", "amber_bg": "#3A2A0E", "amber_text": "#FCD34D",
        "red": "#F87171", "red_bg": "#3B1A1A", "red_text": "#FCA5A5",
        "gray_bg": "#1E232E", "gray_text": "#CBD5E1",
        "diff_bg": "#2A2210",
    },
}


def _palette() -> dict[str, str]:
    return _PALETTE["dark" if _is_dark_theme() else "light"]


def _policy_css(p: dict[str, str]) -> str:
    """시안(정책 상세 화면 시안)의 배지·칩·박스 스타일을 그대로 옮긴 CSS."""

    return f"""
    <style>
    .bkw-badge{{display:inline-flex;align-items:center;gap:6px;padding:3px 10px 3px 8px;
      border-radius:999px;font-size:11.5px;font-weight:700;white-space:nowrap;}}
    .bkw-badge .bkw-dot{{width:7px;height:7px;border-radius:50%;}}
    .bkw-badge.green{{background:{p['green_bg']};color:{p['green_text']};}}
    .bkw-badge.green .bkw-dot{{background:{p['green']};}}
    .bkw-badge.red{{background:{p['red_bg']};color:{p['red_text']};}}
    .bkw-badge.red .bkw-dot{{background:{p['red']};}}
    .bkw-badge.amber{{background:{p['amber_bg']};color:{p['amber_text']};}}
    .bkw-badge.amber .bkw-dot{{background:{p['amber']};}}

    .bkw-region{{font-size:11.5px;color:{p['text_muted']};font-weight:500;}}
    .bkw-chip{{display:inline-flex;align-items:center;font-size:11.5px;font-weight:600;
      padding:4px 10px;border-radius:999px;background:{p['gray_bg']};color:{p['gray_text']};
      margin:6px 6px 0 0;}}
    .bkw-cond{{font-size:12.5px;padding:4px 10px;border-radius:999px;font-weight:500;
      margin:0 6px 6px 0;display:inline-block;}}
    .bkw-cond.on{{background:{p['green_bg']};color:{p['green_text']};}}
    .bkw-cond.off{{background:{p['gray_bg']};color:{p['gray_text']};}}

    .bkw-summary{{background:{p['primary_soft']};border:1px solid {p['border']};
      border-radius:10px;padding:14px 16px;margin:12px 0 4px;}}
    .bkw-summary-tag{{font-size:11.5px;font-weight:700;color:{p['primary']};margin-bottom:6px;}}
    .bkw-summary-text{{font-size:14.5px;line-height:1.6;color:{p['text']};margin:0;}}

    .bkw-section-label{{font-size:13px;font-weight:700;color:{p['text']};margin:18px 0 8px;}}

    .bkw-statgrid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px;}}
    .bkw-statbox{{border:1px solid {p['border']};border-radius:10px;padding:12px 14px;background:{p['panel']};}}
    .bkw-stathead{{font-size:12px;color:{p['text_muted']};font-weight:500;margin-bottom:4px;}}
    .bkw-statval{{font-size:15px;font-weight:700;color:{p['text']};}}
    .bkw-statnote{{font-size:11.5px;color:{p['text_muted']};margin-top:4px;line-height:1.5;}}

    .bkw-warnbox{{background:{p['amber_bg']};border:1px solid {p['amber']}55;
      border-radius:10px;padding:14px 16px;margin-top:16px;}}
    .bkw-warnhead{{font-size:13.5px;font-weight:700;color:{p['amber_text']};margin-bottom:8px;}}
    .bkw-warnlist{{margin:0;padding-left:18px;}}
    .bkw-warnlist li{{font-size:13px;line-height:1.6;color:{p['amber_text']};margin-bottom:4px;}}

    .bkw-lawrow{{margin-top:2px;}}
    .bkw-lawchip{{display:inline-flex;align-items:center;font-size:12.5px;color:{p['text']};
      background:{p['gray_bg']};border-radius:8px;padding:6px 10px;font-weight:500;
      margin:0 8px 8px 0;text-decoration:none;}}

    .bkw-doclist{{margin-top:2px;display:flex;flex-direction:column;align-items:flex-start;gap:6px;width:100%;}}
    .bkw-doclist-row{{font-size:12.5px;color:{p['text']};background:{p['gray_bg']};
      border-radius:8px;padding:8px 12px;font-weight:500;width:fit-content;max-width:100%;}}
    .bkw-doclist-subhead{{font-size:12.5px;font-weight:700;color:{p['text']};
      margin-top:6px;width:100%;}}
    .bkw-doclist-subhead:first-child{{margin-top:0;}}

    .bkw-cardtitle{{font-size:15.5px;font-weight:700;margin:0;color:{p['text']};}}
    .bkw-cardintro{{font-size:12.5px;line-height:1.5;color:{p['text_muted']};margin:4px 0 6px;}}

    .bkw-listtop h1{{font-size:20px;font-weight:700;margin:0 0 4px;color:{p['text']};}}
    .bkw-listtop p{{font-size:13.5px;color:{p['text_muted']};margin:0 0 14px;}}

    .bkw-table{{width:100%;border-collapse:collapse;background:{p['panel']};
      border:1px solid {p['border']};border-radius:14px;overflow:hidden;margin-top:6px;}}
    .bkw-table th,.bkw-table td{{padding:12px 14px;border-bottom:1px solid {p['border']};
      border-right:1px solid {p['border']};text-align:left;vertical-align:top;}}
    .bkw-table th:last-child,.bkw-table td:last-child{{border-right:none;}}
    .bkw-table tr:last-child th,.bkw-table tr:last-child td{{border-bottom:none;}}
    .bkw-table thead th.bkw-colhead{{background:{p['primary_soft']};}}
    .bkw-table tbody th{{background:{p['panel']};font-size:12.5px;font-weight:700;
      color:{p['text_muted']};white-space:nowrap;}}
    .bkw-table td.bkw-diffcell{{background:{p['diff_bg']};}}
    .bkw-difftag{{font-size:10px;font-weight:700;color:{p['amber_text']};background:{p['amber_bg']};
      border-radius:5px;padding:1px 6px;display:inline-block;margin-bottom:3px;}}
    .bkw-cellval{{font-size:13.5px;font-weight:600;color:{p['text']};display:block;}}
    .bkw-cellval + .bkw-cellval{{margin-top:6px;}}
    .bkw-cellsub{{font-size:11.5px;color:{p['text_muted']};line-height:1.5;display:block;margin-top:2px;}}
    </style>
    """


_BADGE_COLOR: dict[str, str] = {"충족": "green", "미충족": "red", "미확인": "amber"}


def _region_label(policy: Mapping[str, Any]) -> str:
    detail = policy.get("detail") or {}
    names = [md_text(item) for item in detail.get("region_names") or [] if item]
    if names:
        return " · ".join(names)
    if detail.get("region_scope") == "national":
        return "전국"
    return "지역 미확인"


def _card_intro(policy: Mapping[str, Any]) -> str:
    """카드 소개문 / 상세 화면 "AI 요약" 문구.

    ``verification_note``를 최우선으로 쓴다 - 이미 서비스가 검증 과정을 거쳐
    만든 문장이라 원문 발췌(purpose/support_details)보다 신뢰도를 그대로
    보여주기에 알맞다.
    """

    detail = policy.get("detail") or {}
    text = (
        policy.get("verification_note")
        or detail.get("support_details")
        or detail.get("purpose")
        or ""
    )
    text = md_text(text).strip()
    if len(text) > 90:
        text = text[:89].rstrip() + "…"
    return text or "정책 설명이 아직 확인되지 않았습니다."


def _document_chip_items(text: str) -> list[str] | None:
    """구비서류 원문이 한 줄에 항목 하나씩 나열된 형태면 목록으로, 줄바꿈 없는
    문단형이면 ``None``을 돌려준다.

    ``required_documents*``는 자유 서술 텍스트라 항상 깔끔한 목록은 아니다.
    줄이 길다고 문단으로 취급하면(예: 괄호 설명이 긴 서류명) 같은 정책 안에서
    어떤 서류는 목록으로, 어떤 서류는 문단으로 갈려 보인다 - 실제로는 원문
    자체가 이미 한 줄에 서류 하나씩이므로 길이로 걸러내지 않는다.
    """

    lines = [ln.strip(" -•·○\t") for ln in str(text).splitlines()]
    lines = [ln for ln in lines if ln]
    if len(lines) < 2:
        return None
    return lines[:12]


# 구비서류 원문 안에서 "□ 유형별 제출 서류", "1) 세대원 변경..." 처럼 실제
# 서류가 아니라 상위 구분(제목)으로 쓰인 줄을 알아본다. 전부 같은 동그라미
# 칩으로 뭉치면 "1)/2)/3) 유형" 구조가 사라져서 어떤 서류가 어느 상황에
# 필요한지 읽을 수 없게 된다.
_DOC_HEADER_RE = re.compile(r"^(?:[□■▪◇◆]|\d{1,2}[)\.])\s*\S")


def _is_document_header_line(text: str) -> bool:
    return bool(_DOC_HEADER_RE.match(text.strip()))


def _document_rows_html(esc, items: list[str], *, item_prefix: str = "") -> str:
    """구비서류 항목을 헤더/실제 서류로 갈라 그린다 - 헤더 줄은 동그라미 칩이
    아니라 굵은 구분 텍스트로, 나머지는 그대로 회색 칩으로."""

    parts = []
    for item in items:
        if _is_document_header_line(item):
            parts.append(f'<div class="bkw-doclist-subhead">{esc(item)}</div>')
        else:
            parts.append(f'<div class="bkw-doclist-row">{item_prefix}{esc(item)}</div>')
    return "".join(parts)


def _sync_policy_selection(selected_key: str, policy_id: str, checkbox_key: str) -> None:
    """체크박스 on_change 콜백: 선택 목록을 위젯과 별개인 plain 리스트로 관리한다.

    Streamlit은 위젯이 한 run이라도 그려지지 않으면(비교/상세 화면으로 넘어가는
    동안 체크박스는 렌더되지 않는다) 그 키의 값을 지워 버린다. 위젯 자신의
    상태가 아니라 이 별도 리스트를 선택의 원본으로 둬야 목록↔비교 화면을
    오가도 선택이 풀리지 않는다.
    """

    current = list(st.session_state.get(selected_key) or [])
    checked = bool(st.session_state.get(checkbox_key))
    if checked and policy_id not in current:
        current.append(policy_id)
    elif not checked and policy_id in current:
        current.remove(policy_id)
    st.session_state[selected_key] = current


def _open_policy_detail(view_key: str, detail_key: str, policy_id: str) -> None:
    st.session_state[view_key] = "detail"
    st.session_state[detail_key] = policy_id


def _open_policy_compare(view_key: str) -> None:
    st.session_state[view_key] = "compare"


def _back_to_policy_list(view_key: str) -> None:
    st.session_state[view_key] = "grid"


def _render_policy_card(
    policy: Mapping[str, Any],
    *,
    selected: bool,
    checkbox_key: str,
    selected_key: str,
    detail_button_key: str,
    view_key: str,
    detail_key: str,
    policy_id: str,
) -> None:
    """시안의 리스트 카드 한 장. 체크박스로 비교 담기, 버튼으로 상세 이동."""

    esc = _html_text
    verdict = str(policy.get("eligibility_status") or "미확인")
    color = _BADGE_COLOR.get(verdict, "amber")
    badge_label = md_text(policy.get("badge") or verdict)
    title = md_text(policy.get("title") or policy.get("policy_id") or "정책")
    region = _region_label(policy)
    intro = _card_intro(policy)
    amount = md_text(policy.get("amount_label") or "지원금액 확인 필요")
    duplicate = md_text(policy.get("duplicate_status") or "미확인")

    with st.container(border=True):
        check_col, body_col, action_col = st.columns(
            [0.6, 5, 1.6], vertical_alignment="center"
        )
        check_col.checkbox(
            "비교 선택",
            value=selected,
            key=checkbox_key,
            label_visibility="collapsed",
            on_change=_sync_policy_selection,
            args=(selected_key, policy_id, checkbox_key),
        )
        with body_col:
            st.html(
                f'<div class="bkw-cardtitle">{esc(title)} '
                f'<span class="bkw-region">· {esc(region)}</span></div>'
                f'<div class="bkw-cardintro">{esc(intro)}</div>'
                f'<span class="bkw-badge {color}"><span class="bkw-dot"></span>{esc(badge_label)}</span>'
                f'<span class="bkw-chip">{esc(amount)}</span>'
                f'<span class="bkw-chip">중복수급 {esc(duplicate)}</span>'
            )
        with action_col:
            if selected:
                action_col.caption("비교 목록에 담김")
            action_col.button(
                "자세히 보기",
                key=detail_button_key,
                icon=":material/chevron_right:",
                width="stretch",
                on_click=_open_policy_detail,
                args=(view_key, detail_key, policy_id),
            )


def _render_policy_grid_view(
    policies: list[Mapping[str, Any]],
    *,
    session_id: str,
    view_key: str,
    detail_key: str,
    selected_key: str,
) -> list[str]:
    esc = _html_text
    st.html(
        f'<div class="bkw-listtop"><h1>확인한 정책 {len(policies)}건</h1>'
        "<p>비교하고 싶은 정책을 선택하면 나란히 비교할 수 있어요</p></div>"
    )

    selected_ids = [str(pid) for pid in st.session_state.get(selected_key) or []]
    for idx, policy in enumerate(policies):
        policy_id = str(policy.get("policy_id") or idx)
        _render_policy_card(
            policy,
            selected=policy_id in selected_ids,
            checkbox_key=f"policy_selcb_{session_id}_{policy_id}",
            selected_key=selected_key,
            detail_button_key=f"policy_open_{session_id}_{policy_id}",
            view_key=view_key,
            detail_key=detail_key,
            policy_id=policy_id,
        )

    total = len(selected_ids)
    hint = (
        "정책을 선택하면 나란히 비교할 수 있어요"
        if total == 0
        else "1개만 더 선택하면 비교할 수 있어요"
        if total == 1
        else f"{total}개 선택됨"
    )
    bar_key = f"policy_comparebar_{session_id}"
    st.html(
        f"<style>[class*='st-key-{bar_key}']{{background:{_palette()['text']};"
        f"border-radius:14px;padding:6px 16px;margin-top:8px;}}"
        f"[class*='st-key-{bar_key}'] p{{color:{_palette()['bg']} !important;"
        "font-weight:600;font-size:13.5px;}}</style>"
    )
    bar = st.container(horizontal=True, vertical_alignment="center", key=bar_key)
    bar.markdown(hint)
    bar.button(
        "비교하기",
        icon=":material/compare_arrows:",
        disabled=total < 2,
        type="primary",
        key=f"policy_compare_btn_{session_id}",
        on_click=_open_policy_compare,
        args=(view_key,),
    )
    return selected_ids


def _render_policy_detail_view(
    policy: Mapping[str, Any],
    *,
    view_key: str,
) -> None:
    esc = _html_text
    detail = policy.get("detail") or {}
    verdict = str(policy.get("eligibility_status") or "미확인")
    color = _BADGE_COLOR.get(verdict, "amber")
    badge_label = md_text(policy.get("badge") or verdict)
    title = md_text(policy.get("title") or policy.get("policy_id") or "정책")
    region = _region_label(policy)

    st.button(
        "목록으로",
        icon=":material/arrow_back:",
        key=f"{view_key}_detail_back",
        on_click=_back_to_policy_list,
        args=(view_key,),
    )

    with st.container(border=True):
        st.html(
            f'<span class="bkw-badge {color}"><span class="bkw-dot"></span>{esc(badge_label)}</span> '
            f'<span class="bkw-cardtitle" style="font-size:19px">{esc(title)}</span> '
            f'<span class="bkw-region">{esc(region)}</span>'
        )

        st.html(
            '<div class="bkw-summary"><div class="bkw-summary-tag">'
            "✨ AI 요약 (원문 대조 검증 통과)</div>"
            f'<p class="bkw-summary-text">{esc(_card_intro(policy))}</p></div>'
        )

        checked = [md_text(item) for item in policy.get("verification_checked") or []]
        unchecked = [md_text(item) for item in policy.get("verification_unchecked") or []]
        if checked or unchecked:
            chips = "".join(f'<span class="bkw-cond on">확인함 · {esc(c)}</span>' for c in checked)
            chips += "".join(f'<span class="bkw-cond off">미확인 · {esc(u)}</span>' for u in unchecked)
            st.html(f'<div class="bkw-section-label">지원자격</div><div>{chips}</div>')

        amount_note = (
            "지급 상한 기준 금액이며, 실제 지급액은 가구 상황에 따라 다를 수 있습니다."
            if policy.get("amount_is_maximum")
            else "정책 원문 기준 금액입니다."
        )
        st.html(
            '<div class="bkw-statgrid">'
            '<div class="bkw-statbox"><div class="bkw-stathead">지원금액</div>'
            f'<div class="bkw-statval">{esc(md_text(policy.get("amount_label") or "지원금액 확인 필요"))}</div>'
            f'<div class="bkw-statnote">{esc(amount_note)}</div></div>'
            '<div class="bkw-statbox"><div class="bkw-stathead">중복수급</div>'
            f'<div class="bkw-statval">{esc(md_text(policy.get("duplicate_status") or "미확인"))}</div>'
            f'<div class="bkw-statnote">{esc(_dup_short_note(policy))}</div></div>'
            "</div>"
        )

        _render_duplicate_detail(policy)

        confirmations = [md_text(item) for item in policy.get("needs_confirmation") or []]
        if confirmations:
            items_html = "".join(f"<li>{esc(item)}</li>" for item in confirmations)
            st.html(
                '<div class="bkw-warnbox"><div class="bkw-warnhead">'
                f"추가 확인이 필요한 항목</div><ul class=\"bkw-warnlist\">{items_html}</ul></div>"
            )

        doc_specs = [
            ("required_documents", "구비서류"),
            ("required_documents_official", "공무원 확인 구비서류"),
            ("required_documents_self", "본인확인 필요 구비서류"),
        ]
        for key, label in doc_specs:
            value = detail.get(key)
            if not value:
                continue
            items = _document_chip_items(str(value))
            if items:
                rows_html = _document_rows_html(esc, items, item_prefix="○ ")
                st.html(f'<div class="bkw-section-label">{esc(label)}</div><div class="bkw-doclist">{rows_html}</div>')
            else:
                st.markdown(f"**{label}**")
                st.markdown(md_text(value))

        related_law = [law for law in policy.get("related_law") or [] if isinstance(law, Mapping)]
        if related_law:
            law_html = "".join(
                f'<a class="bkw-lawchip" href="{esc(law.get("source_url"))}" target="_blank" '
                f'rel="noopener">⚖️ {esc(law.get("law_name") or "관련 법령")}</a>'
                if law.get("source_url")
                else f'<span class="bkw-lawchip">⚖️ {esc(law.get("law_name") or "관련 법령")}</span>'
                for law in related_law
            )
            st.html(f'<div class="bkw-section-label">관련 법령</div><div class="bkw-lawrow">{law_html}</div>')

        # 근거 문서 링크. 새 근거 목록 기능이 아니라 기존 원문(공식 페이지) 링크를
        # 맨 아래로 옮기고 이름만 "근거 문서"로 바꾼 것 - 정책마다 이미 하나뿐인
        # detail.source_url 그대로다. 시안처럼 "확인 (1건)" 머리글과 그 아래
        # 링크 한 줄을 펼치지 않고도 항상 같이 보여준다(누르면 바로 이동하는
        # 링크는 그 아래 줄 하나뿐 - 접이식으로 한 번 더 누르게 하지 않는다).
        source_url = detail.get("source_url")
        if source_url:
            with st.container(border=True):
                st.markdown("📁 **근거 문서 확인 (1건)**")
                st.markdown(f"- [근거 문서]({source_url})")

        organization = detail.get("organization")
        if organization:
            st.caption(f"문의처: {md_text(organization)}")

        # 이 정책에 대해 상세 질문을 이어갈 수 있는 경량 채팅으로 진입한다.
        # 무거운 N1~N14 재실행 없이 이 화면이 이미 담고 있는 정보로만 답한다.
        if st.button(
            "이 정책에 대해 물어보기",
            key=f"askpolicy-{view_key}-{policy.get('policy_id')}",
            icon=":material/chat:",
            width="stretch",
        ):
            # 이 정책 전용 문의 채팅방(모달)을 연다. 다른 정책을 보던 중이면
            # 그 대화 기록은 버린다(채팅방은 한 번에 정책 하나).
            st.session_state["detail_chat_policy"] = dict(policy)
            st.session_state.pop("detail_chat_history", None)
            st.rerun()


def _dup_short_note(policy: Mapping[str, Any]) -> str:
    if [item for item in policy.get("duplicate_conflicts") or [] if isinstance(item, Mapping)]:
        return "다른 제도와 함께 받을 수 없습니다."
    kind = policy.get("duplicate_clause_kind")
    if kind == "other":
        return "다른 제도와 조건부로 제한될 수 있습니다."
    if kind == "header":
        return "제한 항목은 있으나 구체 조건은 문서에 없습니다."
    if policy.get("household_limit_clauses"):
        return "가구·신청 횟수 제한이 있습니다."
    return "별도 중복수급 제한 조항이 확인되지 않았습니다."


def _eligibility_summary(policy: Mapping[str, Any]) -> tuple[str, str | None]:
    """비교표 "지원자격" 셀 (시안 형식: "확인함 · 연령" + "그 외 N개 항목 미확인").

    확인된 조건이 하나도 없으면(시안 예시와 달리 실제로 자주 있는 경우) 그
    사실 자체를 값으로 보여준다 - "확인함 · 0건"처럼 건수만 보여주면 무엇이
    확인됐는지 알 수 없다.
    """

    checked = [md_text(item) for item in policy.get("verification_checked") or []]
    unchecked_count = len(policy.get("verification_unchecked") or [])
    value = "확인함 · " + ", ".join(checked) if checked else "확인된 조건 없음"
    note = f"그 외 {unchecked_count}개 항목 미확인" if unchecked_count else None
    return value, note


def _document_items_for_compare(policy: Mapping[str, Any]) -> list[str]:
    """비교표 "구비서류" 셀. 항목 나열이면 하나씩, 문단형이면 앞부분만 한 줄로."""

    detail = policy.get("detail") or {}
    value = detail.get("required_documents")
    if not value:
        return []
    items = _document_chip_items(str(value))
    if items:
        return items
    text = md_text(value).strip()
    if not text:
        return []
    return [text[:40] + "…" if len(text) > 40 else text]


def _compare_row(
    esc, label: str, values: list[str], subs: list[str | None] | None = None
) -> str:
    diff = len(set(values)) > 1
    cells = [f'<th class="bkw-rowlabel">{esc(label)}</th>']
    for i, value in enumerate(values):
        sub = subs[i] if subs else None
        sub_html = f'<span class="bkw-cellsub">{esc(sub)}</span>' if sub else ""
        if diff:
            cells.append(
                '<td class="bkw-diffcell"><span class="bkw-difftag">다름</span>'
                f'<span class="bkw-cellval">{esc(value)}</span>{sub_html}</td>'
            )
        else:
            cells.append(f'<td><span class="bkw-cellval">{esc(value)}</span>{sub_html}</td>')
    return "<tr>" + "".join(cells) + "</tr>"


def _compare_row_barlist(
    esc, label: str, value_lists: list[list[str]], *, empty_text: str, item_prefix: str = ""
) -> str:
    """항목이 여러 개인 셀(관련 법령·구비서류)을 시안처럼 글자 길이에 맞춘
    회색 바로 하나씩 세로로 쌓는다("다름" 판정은 항목 집합 자체가 다른지로
    본다 - 순서 차이는 무시).
    """

    diff = len({tuple(sorted(items)) for items in value_lists}) > 1
    cells = [f'<th class="bkw-rowlabel">{esc(label)}</th>']
    for items in value_lists:
        if items:
            rows_html = _document_rows_html(esc, items, item_prefix=item_prefix)
            body = f'<div class="bkw-doclist">{rows_html}</div>'
        else:
            body = f'<span class="bkw-cellval">{esc(empty_text)}</span>'
        if diff:
            cells.append(f'<td class="bkw-diffcell"><span class="bkw-difftag">다름</span>{body}</td>')
        else:
            cells.append(f"<td>{body}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def _render_policy_compare_view(
    policies: list[Mapping[str, Any]],
    *,
    session_id: str,
    view_key: str,
    detail_key: str,
) -> None:
    esc = _html_text
    st.button(
        "목록으로",
        icon=":material/arrow_back:",
        key=f"{view_key}_compare_back",
        on_click=_back_to_policy_list,
        args=(view_key,),
    )
    st.html(
        f'<div class="bkw-listtop"><h1>선택한 정책 비교</h1>'
        f"<p>{len(policies)}건을 나란히 비교합니다 · 값이 다른 항목은 노란색으로 표시돼요</p></div>"
    )

    titles = [md_text(p.get("title") or p.get("policy_id") or "정책") for p in policies]
    badges = [md_text(p.get("badge") or p.get("eligibility_status") or "미확인") for p in policies]
    colors = [
        _BADGE_COLOR.get(str(p.get("eligibility_status") or "미확인"), "amber") for p in policies
    ]
    regions = [_region_label(p) for p in policies]
    amounts = [md_text(p.get("amount_label") or "지원금액 확인 필요") for p in policies]
    duplicates = [md_text(p.get("duplicate_status") or "미확인") for p in policies]
    duplicate_notes = [_dup_short_note(p) for p in policies]
    eligibility_values, eligibility_notes = zip(
        *[_eligibility_summary(p) for p in policies]
    )
    law_lists = [
        [
            md_text(law.get("law_name"))
            for law in (p.get("related_law") or [])
            if isinstance(law, Mapping) and law.get("law_name")
        ]
        for p in policies
    ]
    document_lists = [_document_items_for_compare(p) for p in policies]

    head_html = "".join(
        f'<th class="bkw-colhead"><span class="bkw-badge {color}"><span class="bkw-dot"></span>'
        f'{esc(badge)}</span><div class="bkw-cardtitle" style="font-size:14px;margin-top:6px">'
        f"{esc(title)}</div></th>"
        for title, badge, color in zip(titles, badges, colors)
    )
    rows_html = "".join(
        [
            _compare_row(esc, "지역", regions),
            _compare_row(esc, "지원자격", list(eligibility_values), list(eligibility_notes)),
            _compare_row(esc, "지원금액", amounts),
            _compare_row(esc, "중복수급", duplicates, duplicate_notes),
            _compare_row_barlist(esc, "관련 법령", law_lists, empty_text="확인된 법령 없음"),
            _compare_row_barlist(
                esc, "구비서류", document_lists, empty_text="확인된 구비서류 없음", item_prefix="○ "
            ),
        ]
    )
    st.html(
        f'<table class="bkw-table"><thead><tr><th></th>{head_html}</tr></thead>'
        f"<tbody>{rows_html}</tbody></table>"
    )

    cols = st.columns(len(policies))
    for col, policy in zip(cols, policies):
        policy_id = str(policy.get("policy_id") or "")
        col.button(
            "상세보기",
            key=f"policy_cmp_open_{session_id}_{policy_id}",
            icon=":material/chevron_right:",
            width="stretch",
            on_click=_open_policy_detail,
            args=(view_key, detail_key, policy_id),
        )


def _render_policy_section(
    policies: list[Mapping[str, Any]],
    *,
    session_id: str,
) -> None:
    """정책을 리스트/상세/비교 3화면으로 보여준다(시안 "정책 상세 화면 시안" 기준).

    화면 상태는 ``session_id``별로 갈라 둔다 - ``_render_history()``가 매
    rerun마다 지난 답변까지 다시 그리므로, 대화에 답변이 여러 개면 이 위젯들이
    동시에 존재한다(carousel 때와 같은 이유, 위 옛 구현 설명 참고).
    """

    st.html(_policy_css(_palette()))

    view_key = f"policy_view_{session_id}"
    detail_key = f"policy_detail_{session_id}"
    selected_key = f"policy_selected_{session_id}"
    view = st.session_state.get(view_key, "grid")
    by_id = {str(p.get("policy_id") or idx): p for idx, p in enumerate(policies)}

    if view == "detail":
        detail_id = st.session_state.get(detail_key)
        policy = by_id.get(str(detail_id)) or policies[0]
        _render_policy_detail_view(policy, view_key=view_key)
        return

    if view == "compare":
        selected_ids = [
            pid for pid in st.session_state.get(selected_key) or [] if pid in by_id
        ]
        if len(selected_ids) >= 2:
            _render_policy_compare_view(
                [by_id[pid] for pid in selected_ids],
                session_id=session_id,
                view_key=view_key,
                detail_key=detail_key,
            )
            return
        # 선택이 풀려 비교할 게 없으면 목록으로 되돌린다.
        st.session_state[view_key] = "grid"

    _render_policy_grid_view(
        policies,
        session_id=session_id,
        view_key=view_key,
        detail_key=detail_key,
        selected_key=selected_key,
    )


def _summary_counts(result: Mapping[str, Any]) -> dict[str, int]:
    """요약 카드 수치. 서비스가 계산한 ``output_json["summary"]``를 우선 쓰고,
    없으면(예전 계약이나 테스트에서 직접 넘긴 응답) 정책 목록에서 센다.

    미충족과 미확인을 한 칸으로 합치는 규칙은 서비스 쪽 ``_build_summary``와
    같아야 한다 - 미확인을 따로 작게 표시하면 사용자가 "나머지는 되는구나"로
    읽는다.
    """

    summary = (result.get("output_json") or {}).get("summary")
    if isinstance(summary, Mapping):
        return {
            "checked": int(summary.get("checked") or 0),
            "eligible": int(summary.get("eligible") or 0),
            "not_eligible_or_unknown": int(summary.get("not_eligible_or_unknown") or 0),
        }

    statuses = [
        str(policy.get("eligibility_status") or "미확인")
        for policy in result.get("policies") or []
        if isinstance(policy, Mapping)
    ]
    return {
        "checked": len(statuses),
        "eligible": statuses.count("충족"),
        "not_eligible_or_unknown": statuses.count("미충족") + statuses.count("미확인"),
    }


def _render_summary_cards(result: Mapping[str, Any]) -> None:
    counts = _summary_counts(result)
    if not counts["checked"]:
        return

    st.markdown(f"입력하신 조건으로 **{counts['checked']}건**의 제도를 확인했어요.")
    cards = st.container(horizontal=True)
    _metric(cards, "확인한 제도", f"{counts['checked']}건", icon=":material/fact_check:")
    _metric(cards, "자격 충족", f"{counts['eligible']}건", icon=":material/verified:")
    _metric(
        cards,
        "미충족·미확인",
        f"{counts['not_eligible_or_unknown']}건",
        icon=":material/pending:",
    )


def _render_answer(result: Mapping[str, Any]) -> None:
    answer = md_text(result.get("final_answer") or "확인된 답변이 없습니다.")
    answer_status = result.get("answer_status")
    policies = [item for item in result.get("policies") or [] if isinstance(item, Mapping)]

    if answer_status == "abstained":
        st.warning(answer, icon=":material/gpp_maybe:")
    elif not policies:
        # 정책 카드가 없을 때는 이 문장이 유일한 답변 내용이라 그대로 보여준다.
        # 카드가 있으면 같은 내용을 카드가 구조화해서 보여주므로 중복 노출하지 않는다.
        if answer_status == "partial":
            st.info(answer, icon=":material/info:")
        else:
            st.markdown(answer)

    _render_summary_cards(result)

    if policies:
        session_id = str(result.get("session_id") or "response")
        _render_policy_section(policies, session_id=session_id)
    elif answer_status != "abstained":
        st.info("확인된 정책 카드가 없습니다.", icon=":material/search_off:")

    st.caption(GUIDANCE_OFFICIAL)


def _render_raw_outputs(result: Mapping[str, Any]) -> None:
    """서비스가 같은 결과로 만들어 둔 3가지 형식을 그대로 보여준다.

    ``ChatResponse``는 ``output_json`` / ``output_markdown`` / ``output_text``를
    항상 함께 반환하는데, 지금까지 화면은 셋 다 쓰지 않아서 사용자가 결과를
    다른 곳에 붙여넣거나 API 응답 모양을 확인할 방법이 없었다. 접이식으로
    두어 기본 화면은 그대로 두고, 필요할 때만 펼쳐 보게 한다.
    """

    output_json = result.get("output_json")
    output_markdown = result.get("output_markdown")
    output_text = result.get("output_text")
    if not (output_json or output_markdown or output_text):
        return

    with st.expander("응답 원본 보기 (Markdown 표 · JSON · 실행 로그)", icon=":material/data_object:"):
        tab_markdown, tab_json, tab_text, tab_trace = st.tabs(
            ["Markdown 표", "JSON", "텍스트", "실행 로그"]
        )

        with tab_markdown:
            # 원문 문자열을 st.code로 한 번 더 보여주지 않는다. 코드블록은
            # 줄바꿈이 없어서 표 한 줄이 오른쪽으로 잘려 보이는데, 잘린 채
            # 보여주느니 렌더링된 표만 두는 편이 낫다.
            if output_markdown:
                st.markdown(str(output_markdown))
            else:
                st.caption("Markdown 표가 없습니다.")

        with tab_json:
            if output_json:
                st.json(output_json, expanded=2)
            else:
                st.caption("JSON 응답이 없습니다.")

        with tab_text:
            if output_text:
                st.code(str(output_text), language="text")
            else:
                st.caption("텍스트 응답이 없습니다.")

        with tab_trace:
            _render_timing(result.get("timing"))


def _render_timing(timing: Mapping[str, Any] | None) -> None:
    """콘솔(BOKJI_TRACE)에 찍히는 것과 같은 노드 실행 순서·소요 시간.

    터미널을 못 보는 사람도(다른 기기에서 열었거나, 지난 답변을 다시 볼 때)
    "어디서 오래 걸렸는지"를 확인할 수 있게 응답에 실려온 값을 그대로 그린다.
    """

    if not isinstance(timing, Mapping):
        st.caption("실행 로그가 없습니다.")
        return

    node_path = [item for item in timing.get("node_path") or [] if isinstance(item, Mapping)]
    total = _request_total_seconds(timing)
    if total is not None:
        st.markdown(f"**총 소요 {total:.2f}초** · 노드 {len(node_path)}개")

    if not node_path:
        st.caption("실행된 노드 기록이 없습니다.")
        return

    lines = [
        f"{item.get('title') or item.get('node')}  ({float(item.get('seconds') or 0.0):.2f}초)"
        for item in node_path
    ]
    st.code("\n".join(lines), language="text")


def _request_total_seconds(timing: Mapping[str, Any] | None) -> float | None:
    """이번 요청 전체 소요 시간(``request_total`` 구간)."""

    if not isinstance(timing, Mapping):
        return None
    for phase in timing.get("phases") or []:
        if isinstance(phase, Mapping) and phase.get("name") == "request_total":
            try:
                return float(phase.get("total_s"))
            except (TypeError, ValueError):
                return None
    return None


def render_result(result: Mapping[str, Any]) -> None:
    """서비스 응답 상태만 보고 추가 질문 또는 최종 결과를 그린다."""

    status = result.get("status")
    if status == "needs_input":
        st.markdown(str(result.get("question") or "추가 정보가 필요합니다."))
        missing_slots = [
            SLOT_LABELS_KO.get(str(slot), str(slot))
            for slot in result.get("missing_slots") or []
        ]
        if missing_slots:
            st.caption("추가로 필요한 정보: " + ", ".join(missing_slots))
    elif status == "answered":
        _render_answer(result)
    else:
        st.error("서비스 응답을 표시할 수 없습니다.", icon=":material/error:")

    _render_llm_status(result.get("llm_status"))
    _render_raw_outputs(result)
