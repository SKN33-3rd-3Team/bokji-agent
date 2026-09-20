"""Evaluate the deployed chatbot: pipeline metrics + answer-quality LLM-judge.

Wraps ``scripts/run_dev_validation.py``'s pipeline (Recall@k/MRR@k, citation
precision/coverage, abstention precision/recall, p50/p95 latency, error rate —
unchanged, see ``docs/EVALUATION_AUTOMATION.md``) and adds a second layer that
reads ``final_answer`` text: Faithfulness and Answer relevancy, scored by an
LLM judge (``rag_design/answer_quality.py``). See
``docs/MODEL_EVALUATION.md`` for why both layers are needed and how to read
the output.

Every output file name carries the evaluated model name and a unique run ID, so
repeated and concurrent runs stay isolated and stay easy to diff:

    artifacts/evaluation/<run-name>/report_<model>_<run_id>.md
    artifacts/evaluation/<run-name>/metrics_<model>_<run_id>.svg
    artifacts/evaluation/<run-name>/answer_quality_<model>_<run_id>.svg
    artifacts/evaluation/<run-name>/summary_<model>_<run_id>.json
    artifacts/evaluation/<run-name>/results_<model>_<run_id>.jsonl

The unnamed pipeline-only files (``report.md``, ``metrics.svg``,
``summary.json``, ``results.jsonl``) are also written, unchanged, to a
``_pipeline_only/<run_id>/`` subdirectory — that is exactly what
``run_dev_validation.py`` alone would have produced, kept for anyone who wants
to diff against the plain pipeline run.

Usage (PowerShell, repo root, project venv active):

    python scripts/run_model_evaluation.py

    python scripts/run_model_evaluation.py `
        --questions data/evaluation/dev_questions.jsonl `
        --output-dir artifacts/evaluation/qwen3.5-9b-baseline `
        --top-k 5 --workers 4 --max-turns 4 --judge-max-questions 30

Two different "N questions" knobs, easy to mix up:

- ``--max-questions`` limits BOTH stages (pipeline + answer-quality) to the
  first N questions in ``--questions`` — use this for a genuine small smoke
  test, e.g. ``--max-questions 5``.
- ``--judge-max-questions`` limits ONLY the (slower, LLM-judge) answer-quality
  stage, applied after the pipeline stage has already run against the full
  question set (or the ``--max-questions``-limited subset, if given) — use
  this to skip judge cost on most questions while still getting full-set
  Recall@k/MRR@k/etc.

They compose: ``--max-questions 5`` alone already implies at most 5 questions
reach the judge stage too.

A third knob, ``--question-ids``, picks questions by id or id prefix regardless
of their position in the file — ``--question-ids dev-form-`` runs only the
form-variation set appended in 2026-09-15, which ``--max-questions`` (which
counts from the top) can never reach.

Concurrency: the runner sizes ``LLM_PREFETCH_WORKERS`` from
``--provider-concurrency`` (default 10) so that ``--workers x (1 + prefetch)``
stays inside what the provider allows. Exceeding it produces 429s, and a 429'd
call silently falls back to the rule-based path — the run gets slower *and* the
quality numbers drop.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4


_EVAL_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_EVAL_PHONE_PATTERN = re.compile(r"01[0-9]-?\d{3,4}-?\d{4}")
_EVAL_RESIDENT_ID_PATTERN = re.compile(r"\d{6}-?[1-4]\d{6}")
# 날짜 형태 전체를 가리면 정책의 공식 날짜(신청기한·시행일)까지 지워져 날짜가
# 맞는 답과 틀린 답이 채점기 입력에서 똑같아진다. 그래서 날짜 형태는 "이 질문의
# fixture에서 확인된 생년월일"을 찾는 데만 쓰고, 그 값(과 표기 변형)만 가린다.
_EVAL_DATE_PATTERN = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?(?!\d)"
)


def _birth_date_patterns(slot_answers: Mapping | None) -> list[re.Pattern]:
    text = str((slot_answers or {}).get("birth_date") or "")
    patterns = []
    for year, month, day in _EVAL_DATE_PATTERN.findall(text):
        patterns.append(
            re.compile(
                rf"(?<!\d){year}\s*[-./년]\s*0?{int(month)}\s*[-./월]\s*0?{int(day)}(?:\s*일)?(?!\d)"
            )
        )
    return patterns


def _redact_evaluation_text(text: str, birth_date_patterns: Sequence[re.Pattern] = ()) -> str:
    """Remove direct identifiers before sending or persisting evaluation text.

    Only birth dates known from the question's own profile fixture are masked;
    other dates (policy deadlines etc.) are evidence and must survive.
    """

    redacted = _EVAL_EMAIL_PATTERN.sub("[이메일]", str(text))
    redacted = _EVAL_PHONE_PATTERN.sub("[전화번호]", redacted)
    redacted = _EVAL_RESIDENT_ID_PATTERN.sub("[주민번호]", redacted)
    for pattern in birth_date_patterns:
        redacted = pattern.sub("[생년월일]", redacted)
    return redacted

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_design.answer_quality import (
    AnswerQualityCase,
    run_answer_quality_eval,
    summarize_answer_quality,
)
from rag_design.validation_runner import (
    calculate_summary,
    load_questions,
    run_questions,
    write_report,
)


def _create_run_directory(output_dir: Path) -> tuple[str, Path]:
    """Reserve an isolated directory before writing any run artifacts."""
    parent = output_dir / "_pipeline_only"
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:8]
        pipeline_dir = parent / run_id
        try:
            pipeline_dir.mkdir()
        except FileExistsError:
            continue
        return run_id, pipeline_dir


def _sanitize_for_filename(name: str) -> str:
    """Model names contain '/' (e.g. ``Qwen/Qwen3.5-9B``) - make them filename-safe."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return cleaned or "model"


def _policy_evidence_text(policy: Mapping) -> str:
    """Compact grounding text for one cited PolicyView - title + detail fields
    a faithfulness judge can check claims against.

    ``amount_label`` is deliberately left out: it is not source text, it is a
    value ``service._format_amount_label`` *computed* from the pipeline's own
    benefit_calculator output, and that same computed value is what ends up
    in ``final_answer``. Feeding it back in as "evidence" would let a wrong
    calculation confirm itself (the judge just compares the answer's number
    to this text, and they are the same number by construction) - it is not
    independent grounding. ``support_details``(지원내용) below is real
    document text and often already states the amount as written in the
    source, so amount claims are still checkable when the source actually
    documents them.
    """

    detail = policy.get("detail") or {}
    parts = [f"정책명: {policy.get('title') or policy.get('policy_id')}"]
    for label, key in (
        ("지원대상", "support_target"),
        ("선정기준", "eligibility_criteria"),
        ("지원내용", "support_details"),
        ("신청방법", "application_method"),
        ("신청기한", "application_period"),
        ("근거법령(명칭·목록 정보이며 조문 본문이 아님)", "legal_basis"),
    ):
        value = detail.get(key)
        if value:
            parts.append(f"{label}: {value}")
    # related_law는 answer_generation.py가 금액을 못 구했을 때 답변에
    # "관련 법령: <법령명> (<url>)" 줄로 그대로 옮겨 쓴다(N12가 원문에서 찾은
    # 법령명 - amount_label과 달리 파이프라인이 계산한 값이 아니라 원문
    # 발췌라 순환 검증 문제가 없다). 이걸 evidence_text에서 빼먹으면 답변에
    # 법령명이 있어도 judge에게는 근거가 하나도 없는 것처럼 보여, 실제로
    # 원문에 있는 법령명까지 "근거없음"으로 오판정될 수 있다.
    related_law = policy.get("related_law") or []
    law_names = [
        entry.get("law_name")
        for entry in related_law
        if isinstance(entry, Mapping) and entry.get("law_name")
    ]
    if law_names:
        parts.append("관련 법령(명칭·목록 정보이며 조문 본문이 아님): " + ", ".join(law_names))
    return "\n".join(parts)


def _full_policy_evidence_text(policy: Mapping, store=None) -> str:
    """Grounding text built from ALL stored chunks of the policy.

    ``PolicyView.detail`` comes from a top_k=1 lookup per section, so a section
    split into several chunks only shows one of them while the answer may have
    been written from another. Reading every chunk (in document order) keeps the
    judge's evidence a superset of what the answer could have used. Falls back to
    the detail-based text when the store is unavailable or returns nothing.
    """

    fallback = _policy_evidence_text(policy)
    if store is None:
        return fallback
    policy_id = str(policy.get("policy_id"))
    try:
        from rag_design.contracts import SourceType
        from rag_design.vector_store import VectorSearchFilter

        hits = store.search(
            SourceType.SUBSIDY,
            policy_id,
            query_id=f"eval-evidence-{policy_id}",
            top_k=200,
            search_filter=VectorSearchFilter(metadata_equals={"source_id": policy_id}),
        )
    except Exception:
        return fallback
    chunks = sorted({h.chunk.chunk_id: h.chunk for h in hits}.values(), key=lambda c: c.ordinal)
    if not chunks:
        return fallback
    body = "\n".join(chunk.text for chunk in chunks)
    law_lines = [line for line in fallback.splitlines() if line.startswith("관련 법령")]
    return "\n".join([body, *law_lines])


def _build_answer_quality_cases(
    questions: Sequence[Mapping],
    records: Sequence[Mapping],
    captured_responses: Mapping[str, Mapping],
    store=None,
) -> list[AnswerQualityCase]:
    """Pair each completed question with its final_answer + cited-evidence text.

    Reuses ``records`` (already validated by run_questions) for question/session
    bookkeeping and ``captured_responses`` (this script's own side-channel, see
    ``_capturing`` below) for the full ChatResponse - ``run_questions`` itself
    only returns policy IDs, not answer text, and we do not touch that
    (tested) function to add fields it does not need for its own job.
    """

    questions_by_id = {str(q["question_id"]): q for q in questions}
    cases: list[AnswerQualityCase] = []
    for record in records:
        if record.get("terminal_status") != "answered":
            continue
        response = captured_responses.get(record["session_id"])
        if not response:
            continue
        final_answer = response.get("final_answer")
        if not isinstance(final_answer, str) or not final_answer.strip():
            continue
        cited_ids = {str(v) for v in record.get("cited_policy_ids", [])}
        policies = response.get("policies") or []
        evidence_text = "\n\n".join(
            _full_policy_evidence_text(policy, store)
            for policy in policies
            if isinstance(policy, Mapping) and str(policy.get("policy_id")) in cited_ids
        )
        question_item = questions_by_id.get(record["question_id"], {})
        birth_patterns = _birth_date_patterns(question_item.get("slot_answers"))
        question_text = _redact_evaluation_text(
            str(question_item.get("question", "")), birth_patterns
        )
        final_answer = _redact_evaluation_text(final_answer, birth_patterns)
        evidence_text = _redact_evaluation_text(evidence_text, birth_patterns)
        cases.append(
            AnswerQualityCase(
                question_id=str(record["question_id"]),
                question=question_text,
                final_answer=final_answer,
                evidence_text=evidence_text,
            )
        )
    return cases


def _capturing(ask_fn, answer_followup_fn):
    """Wrap ask()/answer_followup() to also stash the last raw ChatResponse per
    session_id (without changing run_questions' inputs/outputs at all), and to
    print one line per completed service call.

    Why: run_questions() itself prints nothing until every question is done,
    and a 100-question set through a reasoning model (each LLM-calling node
    can take tens of seconds) can legitimately run for a long time. Without
    this, the terminal sits blank the whole time and looks identical whether
    it is working or hung - this printed heartbeat is the only way to tell
    the difference from the outside.
    """

    captured: dict[str, dict] = {}
    # 질문별 LLM 호출 성적. ChatResponse["llm_status"]는 그 **요청 하나**의
    # 기록이라(RecordingLLMClient.request_scope), 되묻기로 여러 턴을 돈
    # 질문은 마지막 턴 것만 남으면 앞 턴의 실패가 사라진다. 턴마다 누적한다.
    llm_stats: dict[str, dict] = {}
    lock = threading.Lock()
    call_count = 0
    started_at = time.monotonic()

    def _accumulate_llm_status(session_id: str, response: Mapping) -> None:
        status = response.get("llm_status")
        if not isinstance(status, Mapping):
            return
        with lock:
            agg = llm_stats.setdefault(
                session_id,
                {
                    "enabled": bool(status.get("enabled")),
                    "model": status.get("model"),
                    "calls": 0,
                    "successes": 0,
                    "failures": 0,
                    "messages": [],
                },
            )
            agg["enabled"] = bool(agg["enabled"] or status.get("enabled"))
            agg["model"] = agg["model"] or status.get("model")
            for key in ("calls", "successes", "failures"):
                value = status.get(key)
                # 기록 기능이 없는 클라이언트를 주입하면 None이 온다 - 그 경우
                # 숫자를 지어내지 않고 None으로 남겨 "셀 수 없음"을 드러낸다.
                if value is None:
                    agg[key] = None
                elif agg[key] is not None:
                    agg[key] += int(value)
            for message in status.get("messages") or []:
                if message not in agg["messages"]:
                    agg["messages"].append(str(message))

    def _report(label: str, session_id: str, call_elapsed: float) -> None:
        nonlocal call_count
        with lock:
            call_count += 1
            current = call_count
        print(
            f"[진행] {label} 완료 #{current} (이번 호출 {call_elapsed:.1f}s, "
            f"전체 경과 {time.monotonic() - started_at:.0f}s) session={session_id}",
            flush=True,
        )

    def wrapped_ask(question, session_id, *, top_k):
        call_started = time.monotonic()
        response = ask_fn(question, session_id, top_k=top_k)
        if isinstance(response, Mapping):
            captured[session_id] = dict(response)
            _accumulate_llm_status(session_id, response)
        _report("ask", session_id, time.monotonic() - call_started)
        return response

    def wrapped_followup(session_id, user_input):
        call_started = time.monotonic()
        response = answer_followup_fn(session_id, user_input)
        if isinstance(response, Mapping):
            captured[session_id] = dict(response)
            _accumulate_llm_status(session_id, response)
        _report("answer_followup", session_id, time.monotonic() - call_started)
        return response

    return wrapped_ask, wrapped_followup, captured, llm_stats


def _llm_summary(llm_stats: Mapping[str, Mapping]) -> dict:
    """질문별 LLM 호출 성적을 실행 전체로 합친다.

    왜 필요한가: N1/N5/N9/N10/N13은 LLM 호출이 실패해도 규칙 기반으로 폴백해
    그래프를 끝까지 돌린다(의도된 설계). 그래서 LLM이 전 구간 죽어 있어도
    100문항이 정상 완료되고 Recall 같은 숫자가 멀쩡히 찍힌다 - 2026-09-14
    실측: extra_body 때문에 모든 호출이 HTTP 400을 받는 동안 실행이 끝까지
    돌았고, judge 에러를 파헤치고 나서야 알았다. 그 실행을 Baseline으로
    착각하지 않도록 호출 성적을 산출물에 남긴다.
    """

    questions_with_failure = 0
    fully_degraded = 0
    calls = successes = failures = 0
    countable = True
    messages: list[str] = []
    for status in llm_stats.values():
        if status.get("calls") is None:
            countable = False
        else:
            calls += int(status.get("calls") or 0)
            successes += int(status.get("successes") or 0)
            failures += int(status.get("failures") or 0)
            if status.get("failures"):
                questions_with_failure += 1
            if status.get("calls") and not status.get("successes"):
                fully_degraded += 1
        for message in status.get("messages") or []:
            if message not in messages:
                messages.append(str(message))
    return {
        "question_count": len(llm_stats),
        "calls": calls if countable else None,
        "successes": successes if countable else None,
        "failures": failures if countable else None,
        "questions_with_any_failure": questions_with_failure if countable else None,
        "questions_with_no_successful_call": fully_degraded if countable else None,
        "messages": messages[:10],
    }


def _llm_status_notice_md(llm: Mapping) -> str:
    """LLM이 degrade된 실행이면 리포트 맨 위에 경고를 박는다."""

    if llm.get("calls") is None:
        return (
            "> [!NOTE]\n"
            "> LLM 호출 기록이 없는 클라이언트라 이번 실행에서 LLM이 실제로\n"
            "> 돌았는지 셀 수 없습니다.\n"
        )
    calls = int(llm.get("calls") or 0)
    failures = int(llm.get("failures") or 0)
    if calls == 0:
        return (
            "> [!CAUTION]\n"
            "> **LLM이 한 번도 호출되지 않았습니다.** 모든 노드가 규칙 기반·템플릿\n"
            "> 경로로만 동작한 결과이므로, 아래 수치를 모델 성능으로 읽으면 안 됩니다.\n"
        )
    if failures == 0:
        return f"LLM 호출: {calls}건 전부 성공 (규칙 기반 폴백 없음)\n"
    ratio = failures / calls
    level = "CAUTION" if ratio >= 0.5 else "WARNING"
    dead = int(llm.get("questions_with_no_successful_call") or 0)
    lines = [
        f"> [!{level}]",
        f"> **LLM 호출 {calls}건 중 {failures}건 실패({ratio:.1%})** - 실패한 노드는"
        " 규칙 기반으로",
        "> 폴백했으므로 그만큼 이 실행은 모델 성능이 아니라 폴백 성능을 잰 것입니다.",
    ]
    if dead:
        lines.append(
            f"> 질문 {llm.get('questions_with_any_failure')}건에서 실패가 있었고,"
            f" 그중 **{dead}건은 성공한 호출이 하나도 없습니다**."
        )
    else:
        lines.append(
            f"> 질문 {llm.get('questions_with_any_failure')}건에서 실패가 있었지만,"
            " 모든 질문이 성공한 호출을 하나 이상 갖고 있습니다."
        )
    lines.append("> 원인은 `results_*.jsonl`의 `llm_status.messages`를 보세요.")
    for message in (llm.get("messages") or [])[:3]:
        lines.append(f"> - {message[:200]}")
    return "\n".join(lines) + "\n"


def _quality_svg(summary: Mapping) -> str:
    rows_spec = [
        ("Faithfulness", summary["faithfulness"]["mean_score"]),
        ("Answer relevancy", summary["relevancy"]["mean_score"]),
    ]
    rows = []
    for index, (label, value) in enumerate(rows_spec):
        y = 56 + index * 42
        display = "N/A" if value is None else f"{value:.3f}"
        width = 0.0 if value is None else max(0.0, min(1.0, float(value))) * 360
        rows.append(
            f'<text x="12" y="{y + 15}" font-size="13">{label}</text>'
            f'<rect x="180" y="{y}" width="360" height="22" rx="4" fill="#e5e7eb"/>'
            f'<rect x="180" y="{y}" width="{width:.1f}" height="22" rx="4" fill="#7c3aed"/>'
            f'<text x="548" y="{y + 16}" font-size="13">{display}</text>'
        )
    faithfulness = summary["faithfulness"]
    relevancy = summary["relevancy"]
    footnote = (
        '<text x="12" y="150" font-size="12" fill="#6b7280">'
        f"판정 대상 {summary['question_count']}건 · Faithfulness "
        f"{faithfulness['judged_count']}건 판정/{faithfulness['skipped_count']}건 제외 · "
        f"Relevancy {relevancy['judged_count']}건 판정/{relevancy['skipped_count']}건 제외"
        "</text>"
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="170" '
        'viewBox="0 0 600 170"><rect width="100%" height="100%" fill="white"/>'
        '<text x="12" y="28" font-size="20" font-weight="bold">답변 품질 (LLM-as-Judge)</text>'
        + "".join(rows)
        + footnote
        + "</svg>"
    )


def _answer_quality_section_md(
    summary: Mapping, svg_filename: str, records, cases_by_id: Mapping[str, AnswerQualityCase]
) -> str:
    faithfulness = summary["faithfulness"]
    relevancy = summary["relevancy"]

    def _fmt(value: float | None) -> str:
        return "게시 불가 (판정된 건 없음)" if value is None else f"{value:.3f}"

    worst = sorted(
        (r for r in records if r.faithfulness.judged and r.faithfulness.score is not None),
        key=lambda r: r.faithfulness.score,
    )[:5]
    worst_lines = []
    for record in worst:
        if record.faithfulness.score >= 1.0:
            break
        case = cases_by_id.get(record.question_id)
        question_text = case.question if case else ""
        worst_lines.append(
            f"- `{record.question_id}` (판정: {record.faithfulness.verdict}, "
            f"점수: {record.faithfulness.score:.1f}): {question_text!r} — "
            f"{_redact_evaluation_text(record.faithfulness.reason) or '설명 없음'}"
        )
    worst_section = "\n".join(worst_lines) if worst_lines else "- 없음 (표본 내 근거 미흡 답변 없음)"

    return f"""
## 답변 품질 (LLM-as-Judge, RAGAS 방식)

![답변 품질]({svg_filename})

이 섹션은 정책 **ID**가 맞았는지가 아니라, 생성된 한국어 답변 **문장**이 인용한
정책 근거와 실제로 일치하는지(Faithfulness), 사용자 질문에 실제로
답했는지(Answer relevancy)를 LLM 판정으로 측정합니다. 위쪽 표(Recall/MRR/인용
precision·coverage)는 ID 단위라 "맞는 정책을 골랐는가"만 보고, 이 섹션은
"그 정책에 대해 만든 문장이 사실인가"를 봅니다 — 둘은 서로 다른 실패를 잡습니다.

> [!WARNING]
> 기본적으로 파이프라인 자신과 같은 LLM이 스스로의 답변을 채점하는
> self-evaluation입니다. 그 모델이 공통으로 갖는 맹점(예: 특정 패턴의 환각을
> 항상 놓침)은 이 지표로 잡히지 않습니다. 독립적인 판정이 필요하면 더 강한
> 모델을 judge로 지정해서 다시 실행하세요.

| 지표 | 평균 점수 | 판정 건수 | 제외 건수 |
|---|---:|---:|---:|
| Faithfulness | {_fmt(faithfulness['mean_score'])} | {faithfulness['judged_count']} | {faithfulness['skipped_count']} |
| Answer relevancy | {_fmt(relevancy['mean_score'])} | {relevancy['judged_count']} | {relevancy['skipped_count']} |

판정값 분포 — Faithfulness: {json.dumps(faithfulness['verdict_counts'], ensure_ascii=False)}, Relevancy: {json.dumps(relevancy['verdict_counts'], ensure_ascii=False)}

제외 건수는 인용된 정책 근거가 없거나(보류 답변 등), LLM 판정 호출이 실패했거나,
판정 결과를 파싱하지 못한 경우입니다 — 0점으로 집계하지 않고 평균에서
제외했습니다 (근거는 `results_*.jsonl`의 `answer_quality` 필드).

### Faithfulness 최저점 사례 (최대 5건)

{worst_section}
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dev 질문 자동 검증 + 답변 품질(LLM-as-Judge) 평가, 모델명·날짜가 들어간 파일로 출력"
    )
    parser.add_argument("--questions", type=Path, default=ROOT / "data/evaluation/dev_questions.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/evaluation/model-eval")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4, help="병렬 질문 실행 수(1~32, 기본 4)")
    parser.add_argument("--max-turns", type=int, default=4, help="질문별 최대 서비스 호출 수(1~32, 기본 4)")
    parser.add_argument(
        "--max-questions",
        type=int,
        default=None,
        help="--questions 파일에서 앞의 N개만 실행합니다(파이프라인 지표 포함, 두 단계 전부). "
        "소규모 시험 실행용 - 이 값을 안 주면 파일에 있는 질문 전체가 파이프라인 단계에서 "
        "실행됩니다(--judge-max-questions와는 별개입니다).",
    )
    parser.add_argument(
        "--judge-max-questions",
        type=int,
        default=None,
        help="답변 품질(LLM-judge) 채점을 적용할 최대 질문 수(파이프라인이 완료한 질문 중 "
        "앞의 N개). 생략하면 완료된 질문 전체. 이 값은 답변 품질 채점 단계에만 적용되고, "
        "파이프라인 지표(Recall/MRR 등) 자체는 --max-questions로 줄이지 않는 한 항상 "
        "(잘려나가지 않은) 전체 질문 수로 계산됩니다.",
    )
    parser.add_argument(
        "--skip-answer-quality",
        action="store_true",
        help="답변 품질(LLM-judge) 채점을 건너뛰고 파이프라인 지표만 계산합니다.",
    )
    parser.add_argument(
        "--judge-workers",
        type=int,
        default=4,
        help="답변 품질(LLM-judge) 채점 병렬 실행 수(1~32, 기본 4). 질문당 LLM 호출이 2번 더 "
        "필요하므로, 1로 두면 대형 질문 세트에서 매우 오래 걸립니다.",
    )
    parser.add_argument(
        "--question-ids",
        type=str,
        default=None,
        help="쉼표로 구분한 question_id 또는 그 접두사만 실행합니다. "
        "--max-questions가 '파일 앞에서부터'인 것과 달리 위치와 무관하게 고릅니다 "
        "(예: --question-ids dev-form- 은 나중에 덧붙인 형식 다양화 50건만).",
    )
    parser.add_argument(
        "--provider-concurrency",
        type=int,
        default=10,
        help="LLM provider가 허용하는 동시 요청 수(기본 10 - featherless-ai 사용자당 한도). "
        "이 예산에 맞춰 LLM_PREFETCH_WORKERS를 자동으로 정합니다. 0을 주면 자동 조정을 "
        "끄고 환경변수를 그대로 씁니다.",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=None,
        help="출력 파일명에 쓸 모델명. 생략하면 실제로 연결된 LLM 클라이언트의 모델명을 쓰고, "
        "그마저 없으면 'no-llm'을 씁니다.",
    )
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        parser.error("--top-k must be between 1 and 20")
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    if not 1 <= args.max_turns <= 32:
        parser.error("--max-turns must be between 1 and 32")
    if args.max_questions is not None and args.max_questions < 1:
        parser.error("--max-questions must be at least 1")
    if args.judge_max_questions is not None and args.judge_max_questions < 1:
        parser.error("--judge-max-questions must be at least 1")
    if not 1 <= args.judge_workers <= 32:
        parser.error("--judge-workers must be between 1 and 32")
    if args.provider_concurrency < 0:
        parser.error("--provider-concurrency must be 0 or more")

    # 동시 요청 예산 배분.
    #
    # 한 질문 worker는 자기 LLM 호출 1개 + claim_extractor의 prefetch
    # (LLM_PREFETCH_WORKERS개)를 동시에 띄울 수 있다. 그래서 실제 최대 동시
    # 요청 수는 workers * (1 + prefetch)다. 이게 provider 한도를 넘으면 429가
    # 나고, 429가 난 호출은 조용히 규칙 기반으로 폴백한다 - 느려지는 데 그치지
    # 않고 품질 지표가 함께 내려간다(2026-09-15 실측: .env의 PREFETCH=5와
    # --workers 4 조합은 최대 24 동시 요청으로 한도 10의 두 배를 넘겼다).
    #
    # 예산을 다 쓰되 넘지는 않도록 prefetch를 여기서 정한다. 질문 수준 병렬이
    # 예산을 이미 다 먹으면 prefetch는 1(= prefetch 없음)로 내려간다.
    if args.provider_concurrency:
        prefetch = max(1, args.provider_concurrency // max(1, args.workers) - 1)
        os.environ["LLM_PREFETCH_WORKERS"] = str(prefetch)
        peak = args.workers * (1 + prefetch)
        note = "" if peak <= args.provider_concurrency else (
            f" - 경고: --workers {args.workers}만으로 예산을 넘습니다. "
            f"--workers를 {max(1, args.provider_concurrency // 2)} 이하로 낮추세요."
        )
        print(
            f"동시 요청 예산 {args.provider_concurrency}: --workers {args.workers} x "
            f"(1 + prefetch {prefetch}) = 최대 {peak}건 동시{note}",
            flush=True,
        )

    try:
        from src.rag_chatbot.service import (
            answer_followup,
            ask,
            build_llm_client,
            get_graph,
            get_store,
        )
    except ModuleNotFoundError as exc:
        parser.error(
            f"프로젝트 실행 의존성이 없습니다: {exc.name}. "
            "프로젝트 가상환경에서 requirements-graph.txt와 "
            "requirements-vector.txt를 설치한 뒤 다시 실행하세요."
        )

    questions = load_questions(args.questions)
    if args.question_ids:
        wanted = [part.strip() for part in args.question_ids.split(",") if part.strip()]
        if not wanted:
            parser.error("--question-ids must contain at least one id or prefix")
        selected = [
            item
            for item in questions
            if any(item["question_id"].startswith(prefix) for prefix in wanted)
        ]
        if not selected:
            parser.error(
                f"--question-ids {args.question_ids}: 해당하는 질문이 없습니다 "
                f"({args.questions} {len(questions)}건 중 0건)"
            )
        print(
            f"--question-ids {args.question_ids}: {len(questions)}건 중 {len(selected)}건만 "
            "실행합니다(파이프라인 지표도 이 부분집합 기준입니다 - 다른 실행과 비교할 땐 "
            "같은 필터로 맞추세요).",
            flush=True,
        )
        questions = selected
    if args.max_questions is not None and args.max_questions < len(questions):
        print(
            f"--max-questions {args.max_questions}: {len(questions)}건 중 앞 "
            f"{args.max_questions}건만 실행합니다(파이프라인 지표도 이 부분집합 기준입니다 - "
            "Baseline과 비교할 땐 반드시 같은 --max-questions로 맞추세요).",
            flush=True,
        )
        questions = questions[: args.max_questions]
    print(
        f"질문 {len(questions)}건 로드 완료. 벡터DB·그래프·LLM 클라이언트를 초기화합니다 "
        "(첫 실행이면 임베딩 모델 로딩 등으로 수 분 걸릴 수 있습니다)...",
        flush=True,
    )
    init_started = time.monotonic()
    # get_graph()/get_store()의 지연 초기화는 프로세스 전역 cache를 사용한다 -
    # 여러 worker가 첫 요청에서 동시에 초기화하지 않도록 한 번 직렬로 준비한다.
    get_graph()
    print(f"초기화 완료 ({time.monotonic() - init_started:.0f}s). 질문 실행을 시작합니다.", flush=True)

    wrapped_ask, wrapped_followup, captured, llm_stats = _capturing(ask, answer_followup)
    print(
        f"파이프라인 지표용 질문 {len(questions)}건을 시작합니다(--workers {args.workers}). "
        "질문 하나가 여러 번 호출될 수 있어(추가 정보 요청), 아래 진행 로그는 호출 단위입니다 - "
        "숫자가 계속 올라가면 정상적으로 진행 중인 것입니다.",
        flush=True,
    )
    run_started = time.monotonic()
    records = run_questions(
        questions,
        wrapped_ask,
        wrapped_followup,
        top_k=args.top_k,
        workers=args.workers,
        max_turns=args.max_turns,
    )
    print(
        f"파이프라인 질문 실행 완료 ({time.monotonic() - run_started:.0f}s, {len(records)}건).",
        flush=True,
    )
    summary = calculate_summary(records, top_k=args.top_k)

    run_id, pipeline_dir = _create_run_directory(args.output_dir)
    write_report(
        pipeline_dir,
        records,
        summary,
        question_path=args.questions,
        top_k=args.top_k,
        workers=args.workers,
        max_turns=args.max_turns,
    )

    # judge용 LLM 클라이언트: 별도 지정 기능은 아직 없고, 기본적으로 파이프라인
    # 자신이 쓰는 클라이언트를 그대로 재사용한다 (self-evaluation 한계는
    # report.md에 경고로 남긴다).
    llm_client = build_llm_client()
    model_name = args.model_name or getattr(llm_client, "model", None) or "no-llm"
    model_slug = _sanitize_for_filename(model_name)
    date_str = run_id[:8]

    quality_summary = None
    quality_records: list = []
    cases_by_id: dict[str, AnswerQualityCase] = {}
    if args.skip_answer_quality:
        print("답변 품질(LLM-judge) 평가를 건너뜁니다 (--skip-answer-quality).")
    elif llm_client is None:
        print(
            "HF_TOKEN이 설정되지 않아 LLM 클라이언트가 없습니다 - 답변 품질(LLM-judge) "
            "평가를 건너뜁니다. 파이프라인 지표만 게시합니다.",
            file=sys.stderr,
        )
    else:
        cases = _build_answer_quality_cases(questions, records, captured, get_store())
        if args.judge_max_questions is not None:
            cases = cases[: args.judge_max_questions]
        cases_by_id = {case.question_id: case for case in cases}
        print(
            f"답변 품질(LLM-judge) 채점 {len(cases)}건을 시작합니다(--judge-workers "
            f"{args.judge_workers}, 건당 LLM 호출 최대 2번).",
            flush=True,
        )
        judge_started = time.monotonic()

        def _judge_progress(done: int, total: int, question_id: str) -> None:
            print(
                f"[진행] 답변 품질 채점 {done}/{total} 완료 "
                f"(경과 {time.monotonic() - judge_started:.0f}s) question_id={question_id}",
                flush=True,
            )

        quality_records = run_answer_quality_eval(
            cases, llm_client, workers=args.judge_workers, progress_callback=_judge_progress
        )
        quality_summary = summarize_answer_quality(quality_records)
        print(
            f"답변 품질 채점 완료 ({time.monotonic() - judge_started:.0f}s, {len(quality_records)}건).",
            flush=True,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_name = f"report_{model_slug}_{run_id}.md"
    metrics_svg_name = f"metrics_{model_slug}_{run_id}.svg"
    quality_svg_name = f"answer_quality_{model_slug}_{run_id}.svg"
    summary_name = f"summary_{model_slug}_{run_id}.json"
    results_name = f"results_{model_slug}_{run_id}.jsonl"

    base_report = (pipeline_dir / "report.md").read_text(encoding="utf-8")
    base_metrics_svg = (pipeline_dir / "metrics.svg").read_text(encoding="utf-8")
    (args.output_dir / metrics_svg_name).write_text(base_metrics_svg, encoding="utf-8")

    # write_report()가 만든 report.md는 같은 폴더에 있는 "metrics.svg"를 상대경로로
    # 가리킨다. 그 원본은 _pipeline_only/<run_id>/ 안에 그대로 두고, 여기서 만드는 모델명·날짜가
    # 붙은 사본은 args.output_dir에 놓이므로 링크를 그대로 복사하면 존재하지 않는
    # 파일을 가리켜 이미지가 깨진다(실측 2026-09-14). 파일명을 같이 바꿔준다.
    report_text = base_report.replace("](metrics.svg)", f"]({metrics_svg_name})")
    if "](metrics.svg)" in base_report and metrics_svg_name not in report_text:
        raise RuntimeError(
            "report.md의 metrics.svg 링크를 바꾸지 못했습니다 - write_report()의 "
            "이미지 링크 형식이 바뀌었는지 확인하세요."
        )

    # LLM 호출 성적은 표보다 위에 둔다. 이 실행을 Baseline으로 써도 되는지가
    # 여기서 갈리므로, 스크롤해야 보이는 위치면 있으나 마나다.
    llm_summary = _llm_summary(llm_stats)
    notice = _llm_status_notice_md(llm_summary)
    marker = f"]({metrics_svg_name})\n"
    if marker in report_text:
        report_text = report_text.replace(marker, marker + "\n" + notice, 1)
    else:
        report_text = notice + "\n" + report_text
    if quality_summary is not None:
        (args.output_dir / quality_svg_name).write_text(_quality_svg(quality_summary), encoding="utf-8")
        report_text += _answer_quality_section_md(
            quality_summary, quality_svg_name, quality_records, cases_by_id
        )
    else:
        report_text += (
            "\n## 답변 품질 (LLM-as-Judge, RAGAS 방식)\n\n"
            "이번 실행에서는 건너뛰었습니다"
            + (" (--skip-answer-quality)" if args.skip_answer_quality else " (LLM 클라이언트 없음)")
            + ".\n"
        )
    (args.output_dir / report_name).write_text(report_text, encoding="utf-8")

    summary_payload = dict(json.loads((pipeline_dir / "summary.json").read_text(encoding="utf-8")))
    summary_payload["model_name"] = model_name
    summary_payload["run_date"] = date_str
    summary_payload["run_id"] = run_id
    summary_payload["pipeline_directory"] = pipeline_dir.relative_to(args.output_dir).as_posix()
    summary_payload["llm_status"] = llm_summary
    if quality_summary is not None:
        summary_payload["answer_quality"] = quality_summary
    (args.output_dir / summary_name).write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    quality_by_id = {r.question_id: r for r in quality_records}
    results_lines = []
    for line in (pipeline_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        status = llm_stats.get(str(row.get("session_id")))
        if status is not None:
            row["llm_status"] = status
        record = quality_by_id.get(str(row.get("question_id")))
        if record is not None:
            row["answer_quality"] = {
                "faithfulness": {
                    "judged": record.faithfulness.judged,
                    "score": record.faithfulness.score,
                    "verdict": record.faithfulness.verdict,
                    "reason": _redact_evaluation_text(record.faithfulness.reason),
                    "error": _redact_evaluation_text(record.faithfulness.error or "") or None,
                },
                "relevancy": {
                    "judged": record.relevancy.judged,
                    "score": record.relevancy.score,
                    "verdict": record.relevancy.verdict,
                    "reason": _redact_evaluation_text(record.relevancy.reason),
                    "error": _redact_evaluation_text(record.relevancy.error or "") or None,
                },
            }
        results_lines.append(json.dumps(row, ensure_ascii=False))
    (args.output_dir / results_name).write_text(
        "".join(line + "\n" for line in results_lines), encoding="utf-8"
    )

    print(f"모델: {model_name}")
    _calls = llm_summary.get("calls")
    if _calls is None:
        print("LLM 호출: 기록 없는 클라이언트라 집계 불가", file=sys.stderr)
    elif _calls == 0:
        print(
            "LLM 호출: 0건 - 규칙 기반/템플릿 경로로만 돈 실행입니다. "
            "이 수치를 모델 성능으로 쓰지 마세요.",
            file=sys.stderr,
        )
    elif llm_summary.get("failures"):
        print(
            f"LLM 호출: {_calls}건 중 {llm_summary['failures']}건 실패 - "
            f"질문 {llm_summary['questions_with_no_successful_call']}건은 성공한 호출이 "
            "하나도 없습니다(규칙 기반 폴백). 원인은 summary의 llm_status.messages 참고.",
            file=sys.stderr,
        )
    else:
        print(f"LLM 호출: {_calls}건 전부 성공")
    if summary["quality_metrics_valid"]:
        print(f"파이프라인 검증 완료: {len(records)}건, 품질 지표 게시 가능")
    else:
        print("파이프라인 검증 실패: 일부 질문이 terminal answered에 도달하지 못했습니다.", file=sys.stderr)
    if quality_summary is not None:
        print(
            f"답변 품질: Faithfulness={quality_summary['faithfulness']['mean_score']}, "
            f"Relevancy={quality_summary['relevancy']['mean_score']} "
            f"({quality_summary['question_count']}건 대상)"
        )
    print(f"보고서: {args.output_dir / report_name}")
    print(f"그래프: {args.output_dir / metrics_svg_name}"
          + (f", {args.output_dir / quality_svg_name}" if quality_summary is not None else ""))
    print(f"원본(모델명·날짜 없는 파일): {pipeline_dir}")
    return 0 if summary["quality_metrics_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
