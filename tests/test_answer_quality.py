"""rag_design/answer_quality.py: LLM-judge faithfulness/relevancy scoring."""

from __future__ import annotations

from rag_design.answer_quality import (
    AnswerQualityCase,
    Judgment,
    judge_faithfulness,
    judge_relevancy,
    run_answer_quality_eval,
    summarize_answer_quality,
)
from src.rag_chatbot.llm.client import FailingLLMClient


class ScriptedLLMClient:
    """Returns queued raw responses in call order; records every prompt/system
    it was given so a test can assert on prompt content if it needs to."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        return self._responses.pop(0)


GROUNDED_CASE = AnswerQualityCase(
    question_id="q1",
    question="혼자 사는데 월세가 부담돼요",
    final_answer="청년 월세 지원 정책으로 월 20만원을 지원받을 수 있습니다.",
    evidence_text="정책명: 청년 월세 지원\n지원내용: 월 20만원",
)


def test_faithfulness_grounded_answer_scores_full_credit():
    client = ScriptedLLMClient(
        ['{"판정": "완전근거", "근거없는_주장": [], "설명": "금액이 근거와 일치"}']
    )
    judgment = judge_faithfulness(client, GROUNDED_CASE)
    assert judgment == Judgment(judged=True, score=1.0, verdict="완전근거", reason="금액이 근거와 일치")


def test_faithfulness_catches_fabricated_amount():
    client = ScriptedLLMClient(
        ['{"판정": "근거없음", "근거없는_주장": ["월 40만원"], "설명": "원문은 20만원"}']
    )
    case = AnswerQualityCase(
        question_id="q2",
        question="월세 지원 얼마나 받나요",
        final_answer="월 40만원을 지원받을 수 있습니다.",
        evidence_text="정책명: 청년 월세 지원\n지원내용: 월 20만원",
    )
    judgment = judge_faithfulness(client, case)
    assert judgment.judged is True
    assert judgment.score == 0.0
    assert judgment.verdict == "근거없음"


def test_faithfulness_parses_code_fenced_json():
    client = ScriptedLLMClient(
        ['```json\n{"판정": "부분근거", "근거없는_주장": [], "설명": "일부만 확인됨"}\n```']
    )
    judgment = judge_faithfulness(client, GROUNDED_CASE)
    assert judgment.judged is True
    assert judgment.score == 0.5


def test_faithfulness_skips_when_no_cited_evidence_rather_than_scoring_zero():
    case = AnswerQualityCase(
        question_id="q3",
        question="아무 정책이나 추천해줘",
        final_answer="죄송합니다, 근거가 부족해 보류합니다.",
        evidence_text="",
    )
    judgment = judge_faithfulness(ScriptedLLMClient(["should not be called"]), case)
    assert judgment.judged is False
    assert judgment.score is None
    assert judgment.error == "인용된 정책 근거 없음"


def test_relevancy_skips_when_final_answer_empty():
    case = AnswerQualityCase(
        question_id="q4", question="질문", final_answer="", evidence_text="근거"
    )
    judgment = judge_relevancy(ScriptedLLMClient(["should not be called"]), case)
    assert judgment.judged is False
    assert judgment.error == "final_answer가 비어 있음"


def test_llm_call_failure_is_captured_not_raised():
    judgment = judge_relevancy(FailingLLMClient("네트워크 오류"), GROUNDED_CASE)
    assert judgment.judged is False
    assert judgment.score is None
    assert "네트워크 오류" in (judgment.error or "")


def test_unknown_verdict_label_is_rejected_not_coerced():
    client = ScriptedLLMClient(['{"판정": "모르겠음", "설명": "..."}'])
    judgment = judge_relevancy(client, GROUNDED_CASE)
    assert judgment.judged is False
    assert judgment.score is None


def test_non_json_response_is_captured_not_raised():
    client = ScriptedLLMClient(["이건 그냥 자연어 답변입니다, JSON이 아님"])
    judgment = judge_relevancy(client, GROUNDED_CASE)
    assert judgment.judged is False
    assert judgment.error is not None


def test_summarize_excludes_skipped_records_from_mean_and_reports_them_separately():
    no_evidence_case = AnswerQualityCase(
        question_id="q5", question="질문", final_answer="답변", evidence_text=""
    )
    client = ScriptedLLMClient(
        [
            '{"판정": "완전근거", "근거없는_주장": [], "설명": "ok"}',  # GROUNDED_CASE faithfulness
            '{"판정": "관련", "설명": "ok"}',  # GROUNDED_CASE relevancy
            '{"판정": "관련", "설명": "ok"}',  # no_evidence_case relevancy (faithfulness skipped, no call)
        ]
    )
    records = run_answer_quality_eval([GROUNDED_CASE, no_evidence_case], client)
    summary = summarize_answer_quality(records)

    assert summary["question_count"] == 2
    assert summary["faithfulness"]["mean_score"] == 1.0
    assert summary["faithfulness"]["judged_count"] == 1
    assert summary["faithfulness"]["skipped_count"] == 1
    assert summary["relevancy"]["mean_score"] == 1.0
    assert summary["relevancy"]["judged_count"] == 2
    assert summary["relevancy"]["skipped_count"] == 0


def test_summarize_all_skipped_reports_none_not_zero():
    case = AnswerQualityCase(question_id="q6", question="질문", final_answer="답변", evidence_text="")
    records = run_answer_quality_eval([case], FailingLLMClient("장애"))
    summary = summarize_answer_quality(records)
    assert summary["faithfulness"]["mean_score"] is None
    assert summary["relevancy"]["mean_score"] is None
    assert summary["relevancy"]["skipped_count"] == 1


class ConstantLLMClient:
    """Thread-safe fake: always returns the same verdict, regardless of which
    case called it - unlike ScriptedLLMClient's queue, safe to share across
    concurrent judge calls in a workers>1 test."""

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int | None = None) -> str:
        return '{"판정": "완전근거", "근거없는_주장": [], "설명": "ok"}'


def test_run_answer_quality_eval_rejects_invalid_workers():
    import pytest

    with pytest.raises(ValueError):
        run_answer_quality_eval([GROUNDED_CASE], ConstantLLMClient(), workers=0)


def test_run_answer_quality_eval_parallel_scores_every_case_and_reports_progress():
    cases = [
        AnswerQualityCase(
            question_id=f"q{i}",
            question="질문",
            final_answer="답변",
            evidence_text="근거",
        )
        for i in range(8)
    ]
    progress_calls: list[tuple[int, int, str]] = []

    records = run_answer_quality_eval(
        cases,
        ConstantLLMClient(),
        workers=4,
        progress_callback=lambda done, total, qid: progress_calls.append((done, total, qid)),
    )

    assert {r.question_id for r in records} == {c.question_id for c in cases}
    assert all(r.faithfulness.judged and r.faithfulness.score == 1.0 for r in records)
    # every case reported exactly once, "done" covers 1..N with no duplicate/gap
    # even though completion order across threads is not guaranteed
    assert len(progress_calls) == len(cases)
    assert sorted(done for done, _, _ in progress_calls) == list(range(1, len(cases) + 1))
    assert all(total == len(cases) for _, total, _ in progress_calls)
    assert {qid for _, _, qid in progress_calls} == {c.question_id for c in cases}


def test_run_answer_quality_eval_default_workers_is_sequential_and_unaffected():
    # Backward compatibility: existing callers that don't pass workers/
    # progress_callback see identical behaviour to before this was added.
    records = run_answer_quality_eval([GROUNDED_CASE], ConstantLLMClient())
    assert len(records) == 1
    assert records[0].faithfulness.score == 1.0
