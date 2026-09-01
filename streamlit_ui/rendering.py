"""파이프라인 결과(``PipelineResult``) → Streamlit 위젯 렌더링.

그래프 로직은 없고, ``state`` 를 읽어 화면을 그리는 코드만 둔다.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from .constants import (
    ABSTENTION_REASON_KO,
    DISABILITY_KO,
    EMPLOYMENT_KO,
    GENDER_KO,
    GUIDANCE_OFFICIAL,
    HOUSEHOLD_KO,
    INCOME_KO,
    MARITAL_KO,
    PREGNANCY_KO,
    SECTION_LABELS_KO,
    VERDICT_STYLE,
)
from .pipeline import PipelineResult


# ── 렌더링 헬퍼 ─────────────────────────────────────────────────────
def _policy_titles(state: dict) -> dict[str, str]:
    # assembled_result.policies 는 (배선 계층 보정 후) doc_id 로 키가 잡히므로
    # doc_id 와 source_id 양쪽으로 제목을 찾을 수 있게 해 둔다.
    titles: dict[str, str] = {}
    for retrieved in state.get("subsidy_chunks", []):
        title = retrieved.chunk.text.split("\n", 1)[0].strip()
        titles.setdefault(retrieved.chunk.doc_id, title)
        source_id = retrieved.chunk.metadata.get("source_id")
        if source_id:
            titles.setdefault(source_id, title)
    return titles


def _evidence_by_policy(state: dict) -> dict[str, list]:
    by_id = {r.chunk.chunk_id: r for r in state.get("subsidy_chunks", [])}
    grouped: dict[str, list] = {}
    for claim in state.get("claim_plan", []):
        pid = claim.get("policy_id")
        for cid in claim.get("evidence_chunk_ids", []):
            retrieved = by_id.get(cid)
            if retrieved is not None:
                grouped.setdefault(pid, [])
                if retrieved not in grouped[pid]:
                    grouped[pid].append(retrieved)
    return grouped


def _render_general_law_refs(refs: list) -> None:
    if not refs:
        return
    st.caption(":material/gavel: 지역과 무관하게 적용되는 참고 법령")
    for ref in refs:
        title = getattr(ref, "document_title", str(ref))
        url = getattr(ref, "source_url", "")
        st.markdown(f"- [{title}]({url})" if url else f"- {title}")


def _section_label(code: Any) -> str:
    return SECTION_LABELS_KO.get(str(code), str(code) if code else "본문")


def _render_answer(result: PipelineResult) -> None:
    state = result.state
    assembled = state.get("assembled_result", {})
    policies = assembled.get("policies", {})
    titles = _policy_titles(state)
    evidence = _evidence_by_policy(state)

    if not policies:
        st.info(
            "검색된 정책 중 자격 조건 근거를 확인할 수 있는 제도가 없습니다. "
            + GUIDANCE_OFFICIAL,
            icon=":material/info:",
        )
        return

    verdicts = [
        e.get("eligibility", {}).get("verdict", "미확인") for e in policies.values()
    ]
    met = verdicts.count("충족")
    unmet = verdicts.count("미충족")
    unknown = len(verdicts) - met - unmet

    st.markdown(f"입력하신 조건으로 **{len(policies)}건**의 제도를 확인했어요.")
    with st.container(horizontal=True):
        st.metric("확인한 제도", f"{len(policies)}건", border=True,
                  icon=":material/fact_check:")
        st.metric("자격 충족", f"{met}건", border=True,
                  icon=":material/verified:")
        st.metric("미충족·미확인", f"{unmet + unknown}건", border=True,
                  icon=":material/pending:")

    for pid, entry in policies.items():
        elig = entry.get("eligibility", {})
        verdict = elig.get("verdict", "미확인")
        style = VERDICT_STYLE.get(verdict, VERDICT_STYLE["미확인"])

        with st.container(border=True):
            head = st.container(horizontal=True, vertical_alignment="center")
            head.badge(verdict, icon=style["icon"], color=style["color"])
            head.markdown(f"##### {titles.get(pid, pid)}")

            if elig.get("reasons"):
                st.markdown("**자격 근거**")
                for line in elig["reasons"]:
                    st.markdown(f"- {line}")

            cols = st.container(horizontal=True)
            amount = entry.get("benefit_amount")
            if verdict == "충족":
                if isinstance(amount, dict) and amount.get("amount") is not None:
                    value = float(amount["amount"])
                    cols.metric("예상 지원금", f"{value:,.0f}원",
                                border=True, icon=":material/payments:")
                else:
                    cols.metric("예상 지원금", "정보 부족", border=True,
                                icon=":material/payments:",
                                help="문서에 구조화된 금액이 없어 계산을 보류했습니다.")
            dup = entry.get("duplicate")
            if isinstance(dup, dict):
                cols.metric("중복수급", dup.get("status", "미확인"), border=True,
                            icon=":material/join_inner:")
                if dup.get("conflicts_with"):
                    st.caption("상충 제도: " + ", ".join(dup["conflicts_with"]))

            if isinstance(amount, dict) and amount.get("calculation_note"):
                st.caption(":material/functions: " + amount["calculation_note"])
            if entry.get("status_note"):
                st.caption(":material/info: " + entry["status_note"])

            for law in entry.get("related_law", []) or []:
                name = law.get("law_name", "")
                url = law.get("source_url", "")
                st.caption(
                    f":material/gavel: 관련 법령: [{name}]({url})"
                    if url else f":material/gavel: 관련 법령: {name}"
                )

            refs = evidence.get(pid, [])
            if refs:
                with st.expander(f"근거 문서 확인 ({len(refs)}건)",
                                 icon=":material/description:"):
                    for retrieved in refs:
                        meta = retrieved.chunk.metadata
                        src_url = meta.get("source_url", "")
                        label = _section_label(meta.get("section_type"))
                        st.markdown(
                            f"**{label}** · [출처 페이지]({src_url})"
                            if src_url else f"**{label}**"
                        )
                        st.code(retrieved.chunk.text[:1200], language="text",
                                wrap_lines=True)
            st.caption(f"정책 ID `{pid}`")

    tail = ":material/robot_2: 규칙 기반 판정 결과입니다(자연어 답변 생성 N13·최종 검증 N14 미구현)."
    if result.elapsed_sec:
        tail += f" · 응답 {result.elapsed_sec:.2f}초"
    st.caption(tail + " " + GUIDANCE_OFFICIAL)


def render_result(result: PipelineResult) -> None:
    if result.kind == "needs_input":
        summary = profile_summary(result.state.get("slots", {}))
        if summary:
            st.caption(":material/badge: 지금까지 파악한 정보 — " + " · ".join(summary))
        st.markdown(result.followup_question or "추가 정보가 필요합니다.")
        _render_general_law_refs(result.general_law_references)

    elif result.kind == "answer":
        _render_answer(result)

    elif result.kind == "abstain":
        reason_ko = ABSTENTION_REASON_KO.get(
            result.abstention_reason or "", "근거가 부족합니다"
        )
        st.warning(
            f"답변을 보류합니다 — {reason_ko}. {GUIDANCE_OFFICIAL}",
            icon=":material/gpp_maybe:",
        )
        if result.state.get("_law_search_error"):
            st.caption("법령 검색 단계 오류: " + result.state["_law_search_error"])

    elif result.kind == "no_candidates":
        st.info(
            "입력하신 조건에 해당하는 지원 제도를 찾지 못했어요. "
            "관심 분야(예: 육아, 주거, 취업)를 함께 알려주시면 다시 검색해 드릴게요.",
            icon=":material/search_off:",
        )

    elif result.kind == "error":
        st.error("파이프라인 실행 중 오류가 발생했습니다.", icon=":material/error:")
        with st.expander("오류 상세", icon=":material/bug_report:"):
            st.code(result.error or "", language="text", wrap_lines=True)

    with st.expander("노드 실행 추적 / 상태 보기", icon=":material/route:"):
        st.markdown(
            " ".join(f":blue-badge[{step}]" for step in result.trace)
            or "_추적 정보 없음_"
        )
        if st.session_state.get("debug"):
            st.json(_state_summary(result.state))


def _state_summary(state: dict) -> dict:
    def _safe(value: Any) -> Any:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

    keys = (
        "query_id", "slots", "missing_slots", "slot_ask_counts",
        "region_fallback_applied", "needs_input", "followup_question",
        "evidence_gate_verdict", "missing_document_claim_ids",
        "missing_law_claim_ids", "doc_retry_count", "law_retry_count",
        "node_trace",
    )
    summary = {k: _safe(state.get(k)) for k in keys if k in state}
    summary["subsidy_chunks"] = len(state.get("subsidy_chunks", []))
    summary["claim_plan"] = len(state.get("claim_plan", []))
    summary["eligibility_verdicts"] = _safe(state.get("eligibility_verdicts"))
    summary["benefit_amounts"] = _safe(state.get("benefit_amounts"))
    summary["duplicate_verdicts"] = _safe(state.get("duplicate_verdicts"))
    return summary


# ── 파악한 정보 요약 (사용자용) ─────────────────────────────────────
def profile_summary(slots: dict) -> list[str]:
    """슬롯값을 사람이 읽는 한글 문장 목록으로 바꾼다. 개발 용어는 노출하지 않는다."""

    if not slots:
        return []
    lines: list[str] = []

    names = slots.get("region_names") or []
    if names:
        if slots.get("region_scope") == "national" or names == ["전국"]:
            lines.append("지역: 전국 단위")
        else:
            lines.append("지역: " + ", ".join(names))

    age = slots.get("age")
    if isinstance(age, int):
        lines.append(f"나이: 만 {age}세")
    elif slots.get("birth_date"):
        lines.append(f"생년월일: {slots['birth_date']}")

    for key, mapping, label in (
        ("gender", GENDER_KO, "성별"),
        ("income_bracket", INCOME_KO, "소득"),
        ("disability_status", DISABILITY_KO, "장애"),
        ("employment_status", EMPLOYMENT_KO, "취업 상태"),
        ("marital_status", MARITAL_KO, "혼인"),
        ("pregnancy_status", PREGNANCY_KO, "임신"),
    ):
        value = slots.get(key)
        if value and value != "unknown" and value in mapping:
            lines.append(f"{label}: {mapping[value]}")

    household = [HOUSEHOLD_KO[x] for x in (slots.get("household_types") or [])
                if x in HOUSEHOLD_KO]
    if household:
        lines.append("가구 유형: " + ", ".join(household))

    if isinstance(slots.get("children_count"), int):
        lines.append(f"자녀 수: {slots['children_count']}명")
    if isinstance(slots.get("household_size"), int):
        lines.append(f"가구원 수: {slots['household_size']}명")

    interests = [x for x in (slots.get("interests") or []) if x]
    if interests:
        lines.append("관심 분야: " + ", ".join(interests))

    return lines
