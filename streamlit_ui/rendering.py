"""공식 ``ChatResponse``를 Streamlit 위젯으로 렌더링한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from .constants import (
    GUIDANCE_OFFICIAL,
    SECTION_LABELS_KO,
    SLOT_LABELS_KO,
    VERDICT_STYLE,
)


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


def _render_citations(citations: list[Mapping[str, Any]]) -> None:
    if not citations:
        return

    with st.expander(f"검증된 출처 ({len(citations)}건)", icon=":material/source:"):
        for citation in citations:
            label = str(citation.get("label") or "공식 출처")
            source_url = citation.get("source_url")
            if source_url:
                st.markdown(f"- [{label}]({source_url})")
            else:
                st.markdown(f"- {label}")


def _render_policy_detail(policy: Mapping[str, Any]) -> None:
    detail = policy.get("detail") or {}
    with st.expander("정책 상세 보기", icon=":material/description:"):
        facts: list[str] = []
        organization = detail.get("organization")
        if organization:
            facts.append(f"담당 기관: {organization}")
        region_names = detail.get("region_names") or []
        if region_names:
            facts.append("지역: " + ", ".join(str(item) for item in region_names))
        age_start = detail.get("age_start")
        age_end = detail.get("age_end")
        if age_start is not None or age_end is not None:
            facts.append(
                f"연령 기준: {age_start if age_start is not None else '제한 없음'}"
                f"~{age_end if age_end is not None else '제한 없음'}세"
            )
        for fact in facts:
            st.caption(fact)

        for section_type, label in SECTION_LABELS_KO.items():
            value = detail.get(section_type)
            if value:
                st.markdown(f"**{label}**")
                st.markdown(str(value))

        source_url = detail.get("source_url")
        if source_url:
            st.link_button(
                "공식 원문 확인",
                str(source_url),
                icon=":material/open_in_new:",
            )


def _render_policy(policy: Mapping[str, Any]) -> None:
    verdict = str(policy.get("eligibility_status") or "미확인")
    style = VERDICT_STYLE.get(verdict, VERDICT_STYLE["미확인"])
    title = str(policy.get("title") or policy.get("policy_id") or "정책")

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
            st.info(str(verification_note), icon=":material/fact_check:")

        checked = [str(item) for item in policy.get("verification_checked") or []]
        unchecked = [str(item) for item in policy.get("verification_unchecked") or []]
        if checked:
            st.caption("확인한 조건: " + ", ".join(checked))
        if unchecked:
            st.caption("확인하지 못한 조건: " + ", ".join(unchecked))

        reasons = [str(item) for item in policy.get("eligibility_reasons") or []]
        if reasons:
            st.markdown("**판정 근거**")
            for reason in reasons:
                st.markdown(f"- {reason}")

        metrics = st.container(horizontal=True)
        metrics.metric(
            "지원금",
            str(policy.get("amount_label") or "지원금액 확인 필요"),
            border=True,
            icon=":material/payments:",
        )
        metrics.metric(
            "중복수급",
            str(policy.get("duplicate_status") or "미확인"),
            border=True,
            icon=":material/join_inner:",
        )
        duplicate_note = policy.get("duplicate_note")
        if duplicate_note:
            st.caption(str(duplicate_note))

        confirmations = [str(item) for item in policy.get("needs_confirmation") or []]
        if confirmations:
            st.warning("추가 확인이 필요한 항목", icon=":material/help:")
            for item in confirmations:
                st.markdown(f"- {item}")

        for law in policy.get("related_law") or []:
            name = str(law.get("law_name") or "관련 법령")
            source_url = law.get("source_url")
            st.caption(
                f":material/gavel: [{name}]({source_url})"
                if source_url
                else f":material/gavel: {name}"
            )

        _render_policy_detail(policy)


def _render_answer(result: Mapping[str, Any]) -> None:
    answer = str(result.get("final_answer") or "확인된 답변이 없습니다.")
    answer_status = result.get("answer_status")
    if answer_status == "abstained":
        st.warning(answer, icon=":material/gpp_maybe:")
    elif answer_status == "partial":
        st.info(answer, icon=":material/info:")
    else:
        st.markdown(answer)

    citations = [item for item in result.get("final_citations") or [] if isinstance(item, Mapping)]
    _render_citations(citations)

    policies = [item for item in result.get("policies") or [] if isinstance(item, Mapping)]
    if policies:
        st.markdown(f"#### 확인한 정책 {len(policies)}건")
        for policy in policies:
            _render_policy(policy)
    elif answer_status != "abstained":
        st.info("확인된 정책 카드가 없습니다.", icon=":material/search_off:")

    st.caption(GUIDANCE_OFFICIAL)


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
