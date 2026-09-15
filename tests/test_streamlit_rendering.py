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


def _html_values(app) -> list[str]:
    """정책 카드/상세/비교 화면은 배지·칩을 ``st.html``로 그린다.

    ``AppTest``는 이 노드를 ``get('html')``로만 꺼낼 수 있다(전용 accessor 없음).
    반환되는 ``UnknownElement``의 실제 텍스트는 ``.body``(protobuf 필드)에 있다.
    """

    return [str(element.proto.body) for element in app.get("html")]


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
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인된 범위의 안내입니다.",
            "final_citations": [
                {"policy_id": "p1", "label": "정책 공식 페이지", "source_url": "https://gov.example/p1"}
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
    captions = " ".join(_values(app.caption))
    grid_html = " ".join(_html_values(app))

    # final_answer 문장은 카드가 있을 때는 중복이라 더 보여주지 않는다.
    assert "확인된 범위의 안내입니다." not in markdown
    assert "청년 주거 지원" in grid_html
    assert "연령만 확인" in grid_html  # 카드 소개문 = verification_note
    assert "월 최대 200,000원" in grid_html
    assert "AI 분석 적용" in captions

    # 지원자격 확인 조건·중복수급 상세는 "자세히 보기"를 눌러야 나온다.
    app = next(b for b in app.button if b.key == "policy_open_session-1_p1").click().run(timeout=10)
    detail_html = " ".join(_html_values(app))
    assert "확인함 · 연령" in detail_html
    assert "미확인 · 소득" in detail_html
    assert "조건부" in detail_html


def _two_policy_response() -> dict:
    return {
        "status": "answered",
        "session_id": "session-1",
        "answer_status": "complete",
        "final_answer": "두 정책을 확인했습니다.",
        "final_citations": [],
        "policies": [
            {
                "policy_id": "p1",
                "title": "정책 1",
                "eligibility_status": "충족",
                "amount_label": "10만원",
                "duplicate_status": "가능",
            },
            {
                "policy_id": "p2",
                "title": "정책 2",
                "eligibility_status": "미확인",
                "amount_label": "20만원",
                "duplicate_status": "확인 필요",
            },
        ],
        "llm_status": {"enabled": False},
    }


def test_policy_list_shows_every_card_with_a_detail_button() -> None:
    """시안(정책 상세 화면 시안)처럼 정책을 한 장씩 넘기지 않고 목록으로 다 보여준다."""

    app = _render(_two_policy_response())

    assert not app.exception
    grid_html = " ".join(_html_values(app))
    assert "정책 1" in grid_html
    assert "정책 2" in grid_html
    detail_buttons = {b.key for b in app.button if b.key and b.key.startswith("policy_open_")}
    assert detail_buttons == {"policy_open_session-1_p1", "policy_open_session-1_p2"}
    # 2개 미만 선택 상태에서는 "비교하기"가 비활성화돼 있다.
    compare_btn = next(b for b in app.button if b.key == "policy_compare_btn_session-1")
    assert compare_btn.disabled is True


def test_detail_button_opens_single_policy_view_with_back_link() -> None:
    app = _render(_two_policy_response())

    app = next(b for b in app.button if b.key == "policy_open_session-1_p2").click().run(timeout=10)

    assert not app.exception
    detail_html = " ".join(_html_values(app))
    assert "정책 2" in detail_html
    assert "정책 1" not in detail_html
    back_buttons = [b for b in app.button if "목록으로" in (b.label or "")]
    assert len(back_buttons) == 1

    app = back_buttons[0].click().run(timeout=10)
    assert not app.exception
    grid_html = " ".join(_html_values(app))
    assert "정책 1" in grid_html
    assert "정책 2" in grid_html


def test_required_documents_render_as_uniform_bulleted_bars_regardless_of_line_length() -> None:
    """구비서류 항목은 짧든 길든(괄호 설명이 붙어 60자를 넘든) 전부 "○" 접두사가
    붙은 회색 바 목록으로 통일해서 보여준다 - 정책마다 어떤 서류는 칩으로,
    어떤 서류는 문단으로 갈려 보이던 문제를 없앤다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "청년월세지원",
                    "amount_label": "10만원",
                    "duplicate_status": "가능",
                },
                {
                    "policy_id": "p2",
                    "title": "청년구직활동지원금",
                    "amount_label": "20만원",
                    "duplicate_status": "확인 필요",
                },
                {
                    "policy_id": "p3",
                    "title": "정책 A",
                    "detail": {
                        "required_documents": (
                            "정부지원 아이돌봄서비스 지원결정서(해당년도 2월 이후 발행분)\n"
                            "(아동과 4촌이내의 친인척 확인 가능한)가족관계증명서\n"
                            "수급자 또는 양육자 통장 사본"
                        )
                    },
                }
            ],
        }
    )

    app = next(b for b in app.button if b.key == "policy_open_session-1_p1").click().run(timeout=10)
    assert not app.exception
    # 요약 카드 3개 + "지금 보고 있는 정책 1건"의 지원금·중복수급 2개 = 5.
    # 두 정책이 한꺼번에 쌓이지 않는다.
    assert len(app.metric) == 5
    assert [metric.label for metric in app.metric][:3] == [
        "확인한 제도", "자격 충족", "미충족·미확인"
    ]
    markdown = " ".join(_values(app.markdown))
    assert "청년월세지원" in markdown
    assert "청년구직활동지원금" not in markdown
    assert "1 / 2" in markdown
    # 좌우 화살표가 있고, 첫 장에서는 "이전"이 눌리지 않는다.
    arrows = {button.label: button for button in app.button}
    assert set(arrows) == {"◀", "▶"}
    assert arrows["◀"].disabled is True
    assert arrows["▶"].disabled is False
    detail_html = " ".join(_html_values(app))
    assert '<div class="bkw-doclist-row">○ 정부지원 아이돌봄서비스 지원결정서(해당년도 2월 이후 발행분)</div>' in detail_html
    assert '<div class="bkw-doclist-row">○ 수급자 또는 양육자 통장 사본</div>' in detail_html
    # 문단 fallback(md_text로 그냥 뿌리는 경로)으로 빠지지 않는다.
    assert "**구비서류**" not in " ".join(_values(app.markdown))


def test_required_documents_group_headers_are_not_bulleted_like_leaf_items() -> None:
    """"□ 유형별 제출 서류", "1) 세대원 변경..." 같은 상위 구분 줄은 실제
    서류가 아니므로 동그라미 칩이 아니라 굵은 구분 텍스트로 따로 보여준다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "정책 A",
                    "detail": {
                        "required_documents": (
                            "□ 유형별 제출 서류\n"
                            "1) 세대원 변경[전입·전출·출산(입양)·사망 등]\n"
                            "세대 주민등록등본 (주소 변동 포함)\n"
                            "2) 혼인, 이혼, 출산(입양) 등 가족관계 변동\n"
                            "혼인관계증명서 (상세)"
                        )
                    },
                }
            ],
        }
    )

    app = next(b for b in app.button if b.key == "policy_open_session-1_p1").click().run(timeout=10)
    assert not app.exception
    detail_html = " ".join(_html_values(app))
    assert '<div class="bkw-doclist-subhead">□ 유형별 제출 서류</div>' in detail_html
    assert '<div class="bkw-doclist-subhead">1) 세대원 변경[전입·전출·출산(입양)·사망 등]</div>' in detail_html
    assert '<div class="bkw-doclist-subhead">2) 혼인, 이혼, 출산(입양) 등 가족관계 변동</div>' in detail_html
    # 실제 서류 항목은 그대로 동그라미 칩이다.
    assert '<div class="bkw-doclist-row">○ 세대 주민등록등본 (주소 변동 포함)</div>' in detail_html
    assert '<div class="bkw-doclist-row">○ 혼인관계증명서 (상세)</div>' in detail_html
    # 구분 줄은 동그라미 칩으로 이중 렌더링되지 않는다.
    assert '<div class="bkw-doclist-row">○ □ 유형별 제출 서류</div>' not in detail_html


def test_eligibility_reasons_are_not_shown_as_a_separate_section() -> None:
    """"자격 근거" 절은 시안에 없는 요소라 뺐다 - eligibility_reasons를 화면에
    별도 글머리 목록으로 그대로 쏟아내지 않는다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "정책 A",
                    "eligibility_reasons": [
                        "「평생함께 청년모두가 주거비 지원」 사업의 지원 대상자로 선정된 후 신고 의무가 있음"
                    ],
                }
            ],
        }
    )

    app = next(b for b in app.button if b.key == "policy_open_session-1_p1").click().run(timeout=10)
    assert not app.exception
    markdown = " ".join(_values(app.markdown))
    assert "자격 근거" not in markdown
    assert "평생함께 청년모두가 주거비 지원" not in markdown


def test_selecting_two_policies_enables_compare_view() -> None:
    app = _render(_two_policy_response())

    app = next(c for c in app.checkbox if c.key == "policy_selcb_session-1_p1").set_value(True).run(timeout=10)
    app = next(c for c in app.checkbox if c.key == "policy_selcb_session-1_p2").set_value(True).run(timeout=10)
    compare_btn = next(b for b in app.button if b.key == "policy_compare_btn_session-1")
    assert compare_btn.disabled is False

    app = compare_btn.click().run(timeout=10)
    assert not app.exception
    compare_html = " ".join(_html_values(app))
    assert "정책 1" in compare_html
    assert "정책 2" in compare_html
    assert "선택한 정책 비교" in compare_html
    # 자격 상태가 다르므로 비교표에 "다름" 표시가 붙는다.
    assert "다름" in compare_html


def test_compare_view_shows_documents_row_and_eligibility_in_mockup_style() -> None:
    """비교표의 "지원자격"은 건수(0건/5건)가 아니라 시안처럼 확인된 조건 이름과
    "그 외 N개 항목 미확인"으로 보여준다. "구비서류" 행도 비교표에 있어야 한다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "두 정책을 확인했습니다.",
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "정책 1",
                    "eligibility_status": "충족",
                    "verification_checked": ["연령"],
                    "verification_unchecked": ["장애 여부", "성별", "소득 수준", "취업 상태"],
                    "related_law": [{"law_name": "지방세특례제한법", "source_url": None}],
                    "detail": {"required_documents": "신분증\n주민등록등본\n가족관계증명서"},
                },
                {
                    "policy_id": "p2",
                    "title": "정책 2",
                    "eligibility_status": "미확인",
                    "verification_checked": [],
                    "verification_unchecked": ["연령", "장애 여부", "성별", "소득 수준", "취업 상태"],
                    "detail": {},
                },
            ],
        }
    )

    app = next(c for c in app.checkbox if c.key == "policy_selcb_session-1_p1").set_value(True).run(timeout=10)
    app = next(c for c in app.checkbox if c.key == "policy_selcb_session-1_p2").set_value(True).run(timeout=10)
    app = next(b for b in app.button if b.key == "policy_compare_btn_session-1").click().run(timeout=10)

    assert not app.exception
    compare_html = " ".join(_html_values(app))
    assert "확인함 · 연령" in compare_html
    assert "그 외 4개 항목 미확인" in compare_html
    assert "확인된 조건 없음" in compare_html
    assert "0건" not in compare_html
    assert "구비서류" in compare_html
    assert "신분증" in compare_html
    assert "확인된 구비서류 없음" in compare_html
    # 구비서류·관련 법령 모두 시안처럼 글자 길이에 맞춘 회색 바로 하나씩 쌓는다
    # (쉼표로 한 줄에 몰아넣지 않는다). 구비서류만 "○" 접두사로 통일한다.
    doc_row = next(v for v in _html_values(app) if "구비서류" in v)
    assert '<div class="bkw-doclist-row">○ 신분증</div>' in doc_row
    assert '<div class="bkw-doclist-row">○ 주민등록등본</div>' in doc_row
    assert "신분증, 주민등록등본" not in doc_row
    law_row = next(v for v in _html_values(app) if "관련 법령" in v)
    assert '<div class="bkw-doclist-row">지방세특례제한법</div>' in law_row
    assert "확인된 법령 없음" in law_row


def test_single_policy_grid_has_no_compare_bar_selection_needed() -> None:
    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "한 건입니다.",
            "policies": [{"policy_id": "p1", "title": "정책 1", "eligibility_status": "충족"}],
        }
    )

    assert not app.exception
    grid_html = " ".join(_html_values(app))
    assert "정책 1" in grid_html
    compare_btn = next(b for b in app.button if b.key == "policy_compare_btn_session-1")
    assert compare_btn.disabled is True


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
    assert "실패 1건" in visible
    assert "secret-internal-error" not in visible


# ── 응답 원본(Markdown 표 · JSON · 텍스트) 노출 ─────────────────────


def test_answer_exposes_markdown_table_and_json_outputs() -> None:
    """서비스가 만들어 둔 output_markdown / output_json / output_text 를
    화면에서 실제로 볼 수 있어야 한다(이전에는 셋 다 반환만 되고 어디에도
    쓰이지 않았다)."""

    markdown_table = (
        "**확인한 제도 1건** · 자격 충족 0건 · 미충족·미확인 1건\n\n"
        "| 순위 | 정책명 | 자격 확인 | 지원금 | 중복수급 | 출처 |\n"
        "|---:|---|---|---|---|---|\n"
        "| 1 | 유아학비 지원 | 미확인 | 지원금액 확인 필요 | 미확인 | - |"
    )
    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "확인된 범위의 안내입니다.",
            "policies": [],
            "output_markdown": markdown_table,
            "output_text": "확인된 정책이 없습니다.",
            "output_json": {
                "status": "answered",
                "summary": {"checked": 1, "eligible": 0, "not_eligible_or_unknown": 1},
                "profile": [{"key": "region", "label": "지역", "value": "서울특별시"}],
                "evidence_count": 0,
            },
        }
    )

    assert not app.exception
    # 표는 렌더링된 Markdown 으로만 보여준다. 원문을 st.code로 한 번 더
    # 뿌리면 코드블록에 줄바꿈이 없어서 표 한 줄이 잘려 보인다.
    assert any(markdown_table in value for value in _values(app.markdown))
    codes = _values(app.code)
    assert not any(markdown_table in value for value in codes)
    assert any("확인된 정책이 없습니다." in value for value in codes)
    # JSON 은 st.json 으로 나간다.
    assert len(app.json) == 1


def test_execution_trace_tab_shows_node_path_and_total_seconds() -> None:
    """콘솔(BOKJI_TRACE)에 찍히는 노드 실행 순서를 화면에서도 볼 수 있어야
    한다 - 터미널을 못 보는 상황이나 지난 답변을 다시 볼 때를 위해."""

    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [],
            "output_json": {"status": "answered"},
            "timing": {
                "phases": [{"name": "request_total", "total_s": 12.5}],
                "node_path": [
                    {"node": "slot_parser", "title": "N1 slot_parser - 슬롯 추출", "seconds": 8.0},
                    {"node": "policy_search", "title": "N4 policy_search - 후보 검색", "seconds": 4.5},
                ],
            },
        }
    )

    assert not app.exception
    assert any("총 소요 12.50초" in value for value in _values(app.markdown))
    codes = " ".join(_values(app.code))
    assert "N1 slot_parser - 슬롯 추출  (8.00초)" in codes
    assert "N4 policy_search - 후보 검색  (4.50초)" in codes


def test_needs_input_exposes_outputs_when_present() -> None:
    app = _render(
        {
            "status": "needs_input",
            "question": "소득 수준을 알려주세요.",
            "missing_slots": ["income_bracket"],
            "output_json": {"status": "needs_input", "profile": []},
        }
    )

    assert not app.exception
    assert len(app.json) == 1


def test_answer_without_output_fields_renders_no_raw_output_section() -> None:
    """output_* 가 없는 응답(예전 계약)에서도 깨지지 않고, 빈 섹션을 만들지도
    않는다."""

    app = _render(
        {
            "status": "answered",
            "answer_status": "complete",
            "final_answer": "확인된 범위의 안내입니다.",
            "policies": [],
        }
    )

    assert not app.exception
    assert len(app.json) == 0


# ── 근거 문서 링크 · Markdown ``~`` 처리 ────────────────────────────


def test_final_citations_do_not_drive_the_evidence_document_block() -> None:
    """``final_citations``(청크 단위 근거 목록) 자체는 화면 어디에도 그대로
    나열되지 않는다 - 근거 문서 접이식은 policy_id 매칭과 무관하게 detail.
    source_url 하나만 본다. p2는 source_url이 없으니 final_citations에
    항목이 있어도 근거 문서가 나오지 않는다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "final_citations": [
                {"policy_id": "p1", "label": "정책 A 공식 페이지", "source_url": "https://gov.example/a"},
                {"label": "출처 미상", "source_url": "https://gov.example/x"},
                {"policy_id": "p2", "label": "정책 B 공식 페이지", "source_url": "https://gov.example/b-citation"},
            ],
            "policies": [
                {"policy_id": "p1", "title": "정책 A", "detail": {"source_url": "https://gov.example/a"}},
                {"policy_id": "p2", "title": "정책 B"},
            ],
        }
    )

    assert not app.exception
    # 목록 화면에서는 아직 아무 카드도 열지 않았으니 근거 문서 접이식이 없다.
    assert not any("근거 문서" in label for label in _expander_labels(app))

    app = next(b for b in app.button if b.key == "policy_open_session-1_p2").click().run(timeout=10)
    assert not app.exception
    # p2는 detail.source_url이 없다 - final_citations에 p2 항목이 있어도
    # (엉뚱한 링크 "b-citation"이 새 나오면 안 됨) 근거 문서는 안 뜬다.
    assert not any("근거 문서" in label for label in _expander_labels(app))
    assert "b-citation" not in " ".join(_values(app.markdown))


def test_source_link_is_evidence_document_header_with_direct_link_line() -> None:
    """원문 링크는 시안처럼 "근거 문서 확인 (1건)" 머리글과 그 아래 링크 한 줄이
    펼치지 않아도 항상 같이 보인다(접이식 아님) - 링크 자체는 한 번 클릭으로
    바로 이동해야 한다(펼치고 또 눌러야 하는 2클릭이 아님). 관련 법령 칩에는
    법봉 아이콘을 붙인다. 원문/법령 링크가 없는 정책에서는 둘 다 나오지 않는다."""

    app = _render(
        {
            "status": "answered",
            "session_id": "session-1",
            "answer_status": "complete",
            "final_answer": "확인했습니다.",
            "policies": [
                {
                    "policy_id": "p1",
                    "title": "정책 A",
                    "related_law": [{"law_name": "농업기계화 촉진법", "source_url": "https://law.go.kr/a"}],
                    "detail": {"source_url": "https://gov.example/a", "organization": "농림축산식품부"},
                },
                {"policy_id": "p2", "title": "정책 B"},
            ],
        }
    )

    app = next(b for b in app.button if b.key == "policy_open_session-1_p1").click().run(timeout=10)
    assert not app.exception
    # 접이식(expander)이 아니다 - 펼치지 않아도 머리글과 링크가 항상 같이 보인다.
    assert not any("근거 문서" in label for label in _expander_labels(app))
    markdown = " ".join(_values(app.markdown))
    assert "근거 문서 확인 (1건)" in markdown
    assert "[근거 문서](https://gov.example/a)" in markdown
    detail_html = " ".join(_html_values(app))
    assert "⚖️ 농업기계화 촉진법" in detail_html

    # 원문/법령 링크가 없는 p2에서는 두 요소 모두 나오지 않는다.
    app = next(b for b in app.button if "목록으로" in (b.label or "")).click().run(timeout=10)
    app = next(
        b for b in app.button if b.key == "policy_open_session-1_p2"
    ).click().run(timeout=10)
    assert not app.exception
    assert "근거 문서 확인" not in " ".join(_values(app.markdown))
    assert "⚖️" not in " ".join(_html_values(app))


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
    # final_answer 문장 자체는 카드가 있어 더 보여주지 않지만(위 테스트 참고),
    # 같은 md_text() 경로를 타는 카드 쪽 필드에서 "~"가 살아남지 않는지 확인한다.
    markdown = " ".join(_values(app.markdown))
    grid_html = " ".join(_html_values(app))
    assert "만 3-5세 유아학비" in grid_html
    assert "월 10-20만원" in grid_html
    assert "~" not in markdown
    assert "~" not in grid_html
