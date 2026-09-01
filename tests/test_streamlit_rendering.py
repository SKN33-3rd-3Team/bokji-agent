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


def test_needs_input_renders_question_and_missing_slot_labels() -> None:
    app = _render(
        {
            "status": "needs_input",
            "question": "거주 지역과 생년월일을 알려주세요.",
            "missing_slots": ["region", "birth_date"],
            "llm_status": {"enabled": False},
        }
    )

    assert "거주 지역과 생년월일을 알려주세요." in _values(app.markdown)
    captions = " ".join(_values(app.caption))
    assert "거주 지역" in captions
    assert "생년월일" in captions
    assert "규칙 기반" in captions


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
    assert "AI 분석 적용" in captions


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
    assert "실패 1건" in visible
    assert "secret-internal-error" not in visible
