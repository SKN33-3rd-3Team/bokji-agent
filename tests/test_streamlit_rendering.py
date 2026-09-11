from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render(response: dict) -> AppTest:
    script = (
        "from streamlit_ui.rendering import render_result\n"
        f"render_result({response!r})\n"
    )
    return AppTest.from_string(script).run(timeout=10)


def _values(elements) -> list[str]:
    return [str(element.value) for element in elements]


def _expander_labels(app) -> list[str]:
    """접이식 블록의 라벨. ``st.expander(icon=...)``는 AppTest 트리에서
    Expander 가 아니라 Status 로 분류돼서 양쪽을 다 모아야 한다."""

    return [element.label for element in app.expander] + [
        element.label for element in app.status
    ]


def test_needs_input_renders_question_and_llm_status() -> None:
    app = _render(
        {
            "status": "needs_input",
            "question": "거주 지역과 생년월일을 알려주세요.",
            "missing_slots": ["region", "birth_date"],
            "llm_status": {"enabled": False},
        }
    )

    # 어떤 항목이 필요한지는 위젯 폼(chat.py)이 보여준다 - render_result는
    # 질문 문장과 AI 사용 여부만 낸다.
    assert "거주 지역과 생년월일을 알려주세요." in _values(app.markdown)
    assert "규칙 기반" in " ".join(_values(app.caption))


def test_answer_renders_verified_policy_fields_and_llm_status() -> None:
    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "확인된 범위의 안내입니다.",
            "final_citations": [
                {"label": "정책 공식 페이지", "source_url": "https://gov.example/p1"}
            ],
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "청년 주거 지원",
                    "badge": "우선 검토",
                    "eligibility_status": "충족",
                    "eligibility_reasons": ["연령 기준을 확인했습니다."],
                    "verification_checked": ["연령"],
                    "verification_unchecked": ["소득"],
                    "verification_note": "연령만 확인했으며 소득은 확인이 필요합니다.",
                    "amount_label": "월 최대 200,000원",
                    "duplicate_status": "조건부",
                    "duplicate_note": "원문 조건을 확인하세요.",
                    "needs_confirmation": ["소득 기준 확인"],
                    "related_law": [],
                    "detail": {
                        "purpose": "주거비 부담 완화",
                        "source_url": "https://gov.example/p1",
                    },
                }
            ],
            "llm_status": {
                "enabled": True,
                "model": "test/model",
                "calls": 2,
                "successes": 2,
                "failures": 0,
            },
        }
    )

    markdown = " ".join(_values(app.markdown))
    info = " ".join(_values(app.info))
    captions = " ".join(_values(app.caption))
    metrics = " ".join(_values(app.metric))

    assert "확인된 범위의 안내입니다." in markdown
    assert "청년 주거 지원" in markdown
    assert "연령만 확인" in info
    assert "확인한 조건: 연령" in captions
    assert "확인하지 못한 조건: 소득" in captions
    assert "월 최대 200,000원" in metrics
    assert "조건부" in metrics
    assert "정책 공식 페이지" in markdown
    # AI 분석이 정상 적용된 경우엔 "N회 호출" 같은 내부 수치를 화면에 내지 않는다.
    assert "AI 분석 적용" not in captions


def test_policies_are_shown_one_card_at_a_time_with_arrows() -> None:
    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "두 정책을 확인했습니다.",
            "final_citations": [],
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "정책 1",
                    "amount_label": "10만원",
                    "duplicate_status": "가능",
                },
                {
                    "policy_id": "p2",
                    "title": "정책 2",
                    "amount_label": "20만원",
                    "duplicate_status": "확인 필요",
                },
            ],
            "llm_status": {"enabled": False},
        }
    )

    assert not app.exception
    # 요약 카드 3개 + "지금 보고 있는 정책 1건"의 지원금·중복수급 2개 = 5.
    # 두 정책이 한꺼번에 쌓이지 않는다.
    assert len(app.metric) == 5
    assert [metric.label for metric in app.metric][:3] == [
        "확인한 제도", "자격 충족", "미충족·미확인"
    ]
    markdown = " ".join(_values(app.markdown))
    assert "정책 1" in markdown
    assert "정책 2" not in markdown
    assert "1 / 2" in markdown
    # 좌우 화살표가 있고, 첫 장에서는 "이전"이 눌리지 않는다.
    # (카드마다 "이 정책에 대해 물어보기" 버튼도 있으므로 화살표만 추린다.)
    arrows = {
        button.label: button for button in app.button if button.label in ("◀", "▶")
    }
    assert set(arrows) == {"◀", "▶"}
    assert arrows["◀"].disabled is True
    assert arrows["▶"].disabled is False


def test_carousel_arrow_moves_to_the_next_policy() -> None:
    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "두 정책을 확인했습니다.",
            "policies": [
                {"policy_id": "p1", "title": "정책 1"},
                {"policy_id": "p2", "title": "정책 2"},
            ],
        }
    )

    app = next(b for b in app.button if b.label == "▶").click().run(timeout=10)

    assert not app.exception
    markdown = " ".join(_values(app.markdown))
    assert "정책 2" in markdown
    assert "정책 1" not in markdown
    assert "2 / 2" in markdown
    arrows = {button.label: button for button in app.button}
    assert arrows["◀"].disabled is False
    assert arrows["▶"].disabled is True


def test_single_policy_renders_without_arrows() -> None:
    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "한 건입니다.",
            "policies": [{"policy_id": "p1", "title": "정책 1"}],
        }
    )

    assert not app.exception
    # 정책이 하나면 좌우 화살표는 없다("이 정책에 대해 물어보기" 버튼은 있음).
    assert not any(button.label in ("◀", "▶") for button in app.button)
    assert "정책 1" in " ".join(_values(app.markdown))


def test_summary_cards_count_unmet_and_unknown_together() -> None:
    """미충족과 미확인은 한 칸에 합산한다 - 따로 세면 사용자가 미확인을
    "되는 것"으로 읽는다."""

    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [
                {"policy_id": "p1", "title": "정책 1", "eligibility_status": "충족"},
                {"policy_id": "p2", "title": "정책 2", "eligibility_status": "미충족"},
                {"policy_id": "p3", "title": "정책 3", "eligibility_status": "미확인"},
            ],
        }
    )

    assert not app.exception
    summary = {metric.label: metric.value for metric in app.metric}
    assert summary["확인한 제도"] == "3건"
    assert summary["자격 충족"] == "1건"
    assert summary["미충족·미확인"] == "2건"
    assert any("**3건**의 제도를 확인했어요" in v for v in _values(app.markdown))


def test_summary_cards_prefer_service_computed_output_json_summary() -> None:
    """수치는 서비스가 계산한 output_json["summary"]를 우선 쓴다 - 화면에서
    다시 세면 서비스와 값이 갈릴 수 있다."""

    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [{"policy_id": "p1", "title": "정책 1", "eligibility_status": "미확인"}],
            "output_json": {
                "summary": {"checked": 9, "eligible": 4, "not_eligible_or_unknown": 5}
            },
        }
    )

    assert not app.exception
    summary = {metric.label: metric.value for metric in app.metric}
    assert summary["확인한 제도"] == "9건"
    assert summary["자격 충족"] == "4건"


def test_summary_cards_hidden_when_no_policies() -> None:
    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [],
        }
    )

    assert not app.exception
    assert len(app.metric) == 0


def test_abstained_answer_uses_warning_without_inventing_policy() -> None:
    app = _render(
        {
            "status": "answered",
            "answer_status": "abstained",
            "final_answer": "확인된 근거가 부족해 답변을 제공할 수 없습니다.",
            "final_citations": [],
            "policies": [],
            "llm_status": {"enabled": False},
        }
    )

    assert any("확인된 근거가 부족" in value for value in _values(app.warning))
    assert not any("확인된 정책 카드" in value for value in _values(app.info))


def test_llm_failure_status_does_not_expose_internal_error() -> None:
    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "규칙 기반으로 보완한 답변입니다.",
            "final_citations": [],
            "policies": [],
            "llm_status": {
                "enabled": True,
                "model": "test/model",
                "calls": 2,
                "successes": 1,
                "failures": 1,
                "messages": ["secret-internal-error"],
            },
        }
    )

    visible = " ".join(
        _values(app.markdown) + _values(app.caption) + _values(app.warning)
    )
    assert "규칙 기반 결과로 보완" in visible
    assert "secret-internal-error" not in visible


# ── 근거 링크 위치 · Markdown ``~`` 처리 ────────────────────────────


def test_citations_render_inside_the_matching_policy_card() -> None:
    """근거 링크는 답변 아래 한 덩어리가 아니라 해당 정책 카드 안에 있어야
    어느 정책의 근거인지 알 수 있다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "final_citations": [
                {"policy_id": "p1", "label": "정책 A 공식 페이지", "source_url": "https://gov.example/a"},
                {"policy_id": "p1", "label": "근거 법령", "source_url": "https://law.go.kr/a"},
                {"policy_id": "p2", "label": "정책 B 공식 페이지", "source_url": "https://gov.example/b"},
            ],
            "policies": [
                {"policy_id": "p1", "title": "정책 A"},
                {"policy_id": "p2", "title": "정책 B"},
            ],
        }
    )

    assert not app.exception
    labels = _expander_labels(app)
    # 지금 보이는 카드(p1)의 근거 2건만 카드 안에 있다.
    assert "근거 문서 확인 (2건)" in labels
    markdown = " ".join(_values(app.markdown))
    assert "[정책 A 공식 페이지](https://gov.example/a)" in markdown
    assert "정책 B 공식 페이지" not in markdown
    # 카드에 붙은 근거는 "그 밖의 출처"로 중복 노출되지 않는다.
    assert not any("그 밖의 검증된 출처" in label for label in labels)


def test_citations_without_policy_id_are_not_dropped() -> None:
    """어느 카드에도 붙지 않는 근거를 화면에서 없애면 "근거가 없다"로 읽힌다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "final_citations": [
                {"label": "출처 미상", "source_url": "https://gov.example/x"},
                {"policy_id": "없는정책", "label": "버려질 뻔한 근거", "source_url": "https://gov.example/y"},
            ],
            "policies": [{"policy_id": "p1", "title": "정책 A"}],
        }
    )

    assert not app.exception
    assert "그 밖의 검증된 출처 (2건)" in _expander_labels(app)
    markdown = " ".join(_values(app.markdown))
    assert "버려질 뻔한 근거" in markdown


def test_tilde_is_replaced_with_hyphen_in_markdown_output() -> None:
    """``~``는 GFM 취소선(``~~``)과 겹쳐서 한 줄에 두 번 나오면 그 사이가
    통째로 취소선이 된다. 범위 표기는 ``-``로 바꿔 그린다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "중위소득 30~50% 또는 75~100% 가구가 대상입니다.",
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "만 3~5세 유아학비",
                    "eligibility_reasons": ["만 3~5세 아동에 해당"],
                    "amount_label": "월 10~20만원",
                }
            ],
        }
    )

    assert not app.exception
    markdown = " ".join(_values(app.markdown))
    assert "중위소득 30-50% 또는 75-100%" in markdown
    assert "만 3-5세 유아학비" in markdown
    assert "만 3-5세 아동에 해당" in markdown
    assert "~" not in markdown
    assert any("월 10-20만원" == metric.value for metric in app.metric)
