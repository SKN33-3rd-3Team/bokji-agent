"""공식 ``ChatResponse``를 Streamlit 위젯으로 렌더링한다."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

import streamlit as st

from .constants import (
    GUIDANCE_OFFICIAL,
    SECTION_LABELS_KO,
    SLOT_LABELS_KO,
    VERDICT_STYLE,
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


def _citations_by_policy(
    citations: list[Mapping[str, Any]]
) -> tuple[dict[str, list[Mapping[str, Any]]], list[Mapping[str, Any]]]:
    """근거를 ``policy_id``별로 나눈다.

    ``CitationEntry``는 policy_id를 갖고 있어서 어느 정책의 근거인지 알 수
    있다. 어디에도 붙지 않는 근거(policy_id가 없거나, 카드로 만들어지지 않은
    정책의 근거)는 **버리지 않고** 따로 돌려준다 - 검증된 근거를 화면에서
    조용히 없애면 "근거가 없다"로 읽힌다(docs/PROJECT_COMPLIANCE.md).
    """

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    orphans: list[Mapping[str, Any]] = []
    for citation in citations:
        policy_id = citation.get("policy_id")
        if policy_id:
            grouped.setdefault(str(policy_id), []).append(citation)
        else:
            orphans.append(citation)
    return grouped, orphans


def _render_citation_list(citations: list[Mapping[str, Any]]) -> None:
    for citation in citations:
        label = md_text(citation.get("label") or "공식 출처")
        source_url = citation.get("source_url")
        if source_url:
            st.markdown(f"- [{label}]({source_url})")
        else:
            st.markdown(f"- {label} (링크 없음)")


def _render_citations(citations: list[Mapping[str, Any]], *, title: str) -> None:
    if not citations:
        return

    with st.expander(f"{title} ({len(citations)}건)", icon=":material/source:"):
        _render_citation_list(citations)


def _render_policy_detail(policy: Mapping[str, Any]) -> None:
    detail = policy.get("detail") or {}
    with st.expander("정책 상세 보기", icon=":material/description:"):
        facts: list[str] = []
        organization = detail.get("organization")
        if organization:
            facts.append(f"담당 기관: {organization}")
        region_names = detail.get("region_names") or []
        if region_names:
            facts.append("지역: " + ", ".join(md_text(item) for item in region_names))
        age_start = detail.get("age_start")
        age_end = detail.get("age_end")
        if age_start is not None or age_end is not None:
            facts.append(
                f"연령 기준: {age_start if age_start is not None else '제한 없음'}"
                f"-{age_end if age_end is not None else '제한 없음'}세"
            )
        for fact in facts:
            st.caption(md_text(fact))

        for section_type, label in SECTION_LABELS_KO.items():
            value = detail.get(section_type)
            if value:
                st.markdown(f"**{label}**")
                st.markdown(md_text(value))

        source_url = detail.get("source_url")
        if source_url:
            st.link_button(
                "공식 원문 확인",
                str(source_url),
                icon=":material/open_in_new:",
            )


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


def _render_policy(
    policy: Mapping[str, Any],
    *,
    metric_key: str,
    citations: list[Mapping[str, Any]] | None = None,
) -> None:
    verdict = str(policy.get("eligibility_status") or "미확인")
    style = VERDICT_STYLE.get(verdict, VERDICT_STYLE["미확인"])
    title = md_text(policy.get("title") or policy.get("policy_id") or "정책")

    with st.container(border=True):
        head = st.container(horizontal=True, vertical_alignment="center")
        head.badge(
            str(policy.get("badge") or verdict),
            icon=style["icon"],
            color=style["color"],
        )
        head.markdown(f"##### {title}")

        verification_note = policy.get("verification_note")
        if verification_note:
            st.info(md_text(verification_note), icon=":material/fact_check:")

        checked = [md_text(item) for item in policy.get("verification_checked") or []]
        unchecked = [md_text(item) for item in policy.get("verification_unchecked") or []]
        if checked:
            st.caption("확인한 조건: " + ", ".join(checked))
        if unchecked:
            st.caption("확인하지 못한 조건: " + ", ".join(unchecked))

        reasons = [md_text(item) for item in policy.get("eligibility_reasons") or []]
        if reasons:
            st.markdown("**자격 근거**")
            for reason in reasons:
                st.markdown(f"- {reason}")

        metrics = st.container(horizontal=True)
        _metric(
            metrics,
            "지원금",
            md_text(policy.get("amount_label") or "지원금액 확인 필요"),
            icon=":material/payments:",
        )
        duplicate_metric = metrics.container(key=metric_key)
        _metric(
            duplicate_metric,
            "중복수급",
            md_text(policy.get("duplicate_status") or "미확인"),
            icon=":material/join_inner:",
        )
        # duplicate_note = policy.get("duplicate_note")
        # if duplicate_note:
        #     st.caption(md_text(duplicate_note))
        _render_duplicate_detail(policy)

        confirmations = [md_text(item) for item in policy.get("needs_confirmation") or []]
        if confirmations:
            st.warning("추가 확인이 필요한 항목", icon=":material/help:")
            for item in confirmations:
                st.markdown(f"- {item}")

        for law in policy.get("related_law") or []:
            name = md_text(law.get("law_name") or "관련 법령")
            source_url = law.get("source_url")
            st.caption(
                f":material/gavel: [{name}]({source_url})"
                if source_url
                else f":material/gavel: {name}"
            )

        # 근거 링크는 카드 안에 둔다 - 답변 전체에 하나로 묶어 두면 어느 정책의
        # 근거인지 알 수 없다. CitationEntry.policy_id 로 갈라 담는다.
        _render_citations(citations or [], title="근거 문서 확인")

        # _render_policy_detail(policy) # conflict


def _step_policy_page(state_key: str, delta: int, total: int) -> None:
    """캐러셀 위치를 한 칸 옮긴다(버튼 on_click 콜백)."""

    current = int(st.session_state.get(state_key, 0))
    st.session_state[state_key] = min(max(current + delta, 0), total - 1)


def _render_policy_carousel(
    policies: list[Mapping[str, Any]],
    *,
    session_id: str,
    citations_by_policy: dict[str, list[Mapping[str, Any]]],
) -> None:
    """정책을 세로로 다 쌓지 않고 한 번에 한 장씩, 좌우 화살표로 넘겨 본다.

    상태 키를 ``session_id``로 묶는 이유: ``_render_history()``가 매 rerun마다
    지난 답변까지 전부 다시 그리므로, 대화에 답변이 여러 개면 카드 위젯이
    동시에 존재한다. 응답마다 다른 session_id를 키에 넣어야 위젯 키가 겹치지
    않고(Streamlit DuplicateWidgetID), 각 답변이 자기 위치를 따로 기억한다.
    """

    total = len(policies)
    state_key = f"policy_page_{session_id}"
    # 저장된 값이 범위를 벗어날 수 있다(정책 수가 다른 응답을 다시 그릴 때).
    index = min(max(int(st.session_state.get(state_key, 0)), 0), total - 1)
    st.session_state[state_key] = index

    if total > 1:
        nav = st.container(horizontal=True, vertical_alignment="center")
        # on_click 콜백으로 옮기는 이유: 버튼 반환값으로 index를 바꾸면
        # 이미 그려진 "N / M"과 화살표 비활성 상태가 한 박자씩 늦는다
        # (Streamlit은 위에서 아래로 한 번만 그린다). 콜백은 rerun 전에
        # 먼저 실행돼서, 이 함수가 읽는 시점에는 값이 이미 갱신돼 있다.
        nav.button(
            "◀",
            key=f"{state_key}_prev",
            disabled=index == 0,
            help="이전 정책",
            on_click=_step_policy_page,
            args=(state_key, -1, total),
        )
        nav.markdown(f"**{index + 1} / {total}**")
        nav.button(
            "▶",
            key=f"{state_key}_next",
            disabled=index >= total - 1,
            help="다음 정책",
            on_click=_step_policy_page,
            args=(state_key, 1, total),
        )

    policy = policies[index]
    policy_id = str(policy.get("policy_id") or "")
    _render_policy(
        policy,
        metric_key=f"dup-metric-{session_id}-{index}",
        citations=citations_by_policy.get(policy_id, []),
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
    if answer_status == "abstained":
        st.warning(answer, icon=":material/gpp_maybe:")
    elif answer_status == "partial":
        st.info(answer, icon=":material/info:")
    else:
        st.markdown(answer)

    _render_summary_cards(result)

    citations = [
        item for item in result.get("final_citations") or [] if isinstance(item, Mapping)
    ]
    citations_by_policy, orphan_citations = _citations_by_policy(citations)

    policies = [item for item in result.get("policies") or [] if isinstance(item, Mapping)]
    if policies:
        st.markdown(f"#### 확인한 정책 {len(policies)}건")
        st.html(
            "<style>"
            "[class*='st-key-dup-metric-'] [data-testid='stMetricLabel'],"
            "[class*='st-key-dup-metric-'] [data-testid='stMetricValue']"
            "{font-size:0.875rem;font-weight:500;line-height:1.4}</style>"
        )
        session_id = str(result.get("session_id") or "response")
        shown = {str(policy.get("policy_id") or "") for policy in policies}
        _render_policy_carousel(
            policies,
            session_id=session_id,
            citations_by_policy=citations_by_policy,
        )
        # 카드로 만들어지지 않은 정책의 근거까지 카드 밖에서 챙긴다.
        orphan_citations = orphan_citations + [
            citation
            for policy_id, group in citations_by_policy.items()
            if policy_id not in shown
            for citation in group
        ]
    elif answer_status != "abstained":
        st.info("확인된 정책 카드가 없습니다.", icon=":material/search_off:")
        orphan_citations = citations

    # 어느 카드에도 붙지 않는 근거는 버리지 않고 여기서 보여준다.
    _render_citations(orphan_citations, title="그 밖의 검증된 출처")

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
