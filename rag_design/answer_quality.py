"""LLM-as-judge scoring for *final answer text*: faithfulness and relevancy.

``evaluation.py``/``validation_runner.py`` compute Recall@k, MRR@k, citation
precision/coverage and abstention precision/recall — all of them compare
**policy IDs** the pipeline returned against labelled gold IDs. They cannot see
whether the Korean sentence in ``final_answer`` actually says something true
about that policy, or whether it addresses the user's question at all. A run
can score perfectly on every ID-based metric while the generated text still
states the wrong amount, invents an eligibility condition, or answers a
different question than the one asked.

This module adds two small, RAGAS-style checks that read ``final_answer``
against the evidence of the policies the pipeline actually cited:

- **Faithfulness** — does every factual claim in the answer trace back to the
  cited policies' text, or does it add something the evidence does not
  support? This is the same fabrication risk
  ``experiments/model_evaluation/rag_eval.ipynb`` probes with synthetic
  corruption, and the same one D4-C/D5 in the retrieval work addressed for
  *which* policy gets surfaced — this metric checks the *sentence*, not the ID.
- **Answer relevancy** — does the answer actually address the user's question
  (including a well-explained abstention), or is it generic/off-topic even
  though retrieval succeeded?

Both are judged on a 3-point scale (0.0 / 0.5 / 1.0), not a free-form float — a
single LLM call cannot reliably produce a stable continuous score, so the
label space is kept small and each label is spelled out in the prompt (mirrors
the 일치/불일치 pattern already used in ``rag_eval.ipynb``). A judgment the
judge LLM could not complete (call failure, unparseable JSON, no cited
evidence to check against) is recorded with ``judged=False`` and excluded from
the aggregate — never silently scored as 0 and never silently dropped without
a trace, per ``docs/PROJECT_COMPLIANCE.md`` ("실패한 검증과 알려진 한계를
숨기지 않는다").

Known limitation (documented, not hidden): by default the judge is whatever
``llm_client`` the caller passes in — if that is the same client
``service.build_llm_client()`` builds for the pipeline itself, this is a
**self-evaluation**, not an independent judge, and will not catch a systematic
blind spot the model has in its own generation. Pass a different, stronger
``llm_client`` for an independent check when one is available.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from src.rag_chatbot.llm.client import LLMCallError, LLMClient, loads_json_object

_VERDICT_SCORES: Mapping[str, float] = {"완전근거": 1.0, "부분근거": 0.5, "근거없음": 0.0}
_RELEVANCY_SCORES: Mapping[str, float] = {"관련": 1.0, "부분관련": 0.5, "무관": 0.0}

_JUDGE_SYSTEM = (
    "당신은 복지정책 안내 챗봇의 답변을 근거 문서·사용자 질문과 대조해 "
    "검수하는 평가자입니다. 반드시 JSON 객체 하나만 답하세요."
)


@dataclass(frozen=True, slots=True)
class AnswerQualityCase:
    """One question's final answer plus the evidence it should be grounded in.

    ``evidence_text`` is the title + detail text of the policies the pipeline
    actually *cited* for this question (not every retrieved policy) — an
    empty string means there was nothing to check faithfulness against
    (e.g. an abstained answer with no citation), which is a legitimate,
    distinct state from a failed judgment.
    """

    question_id: str
    question: str
    final_answer: str
    evidence_text: str


@dataclass(frozen=True, slots=True)
class Judgment:
    judged: bool
    score: float | None
    verdict: str | None
    reason: str
    error: str | None = None


def _judge(
    llm_client: LLMClient,
    prompt: str,
    *,
    score_map: Mapping[str, float],
) -> Judgment:
    try:
        raw = llm_client.complete(prompt, system=_JUDGE_SYSTEM, max_tokens=512)
    except LLMCallError as exc:
        return Judgment(judged=False, score=None, verdict=None, reason="", error=str(exc))
    try:
        data = loads_json_object(raw)
        verdict = data.get("판정")
        if verdict not in score_map:
            raise ValueError(f"알 수 없는 판정값: {verdict!r}")
    except (ValueError, TypeError) as exc:
        return Judgment(
            judged=False,
            score=None,
            verdict=None,
            reason="",
            error=f"판정 파싱 실패: {exc}",
        )
    reason = data.get("설명")
    return Judgment(
        judged=True,
        score=score_map[verdict],
        verdict=verdict,
        reason=str(reason) if reason else "",
    )


def _faithfulness_prompt(case: AnswerQualityCase) -> str:
    return f"""[근거 문서]
{case.evidence_text}

[챗봇 답변]
{case.final_answer}

[챗봇 답변]에 [근거 문서]로 뒷받침되지 않는 사실 주장(금액, 자격 조건, 신청
방법, 기한 등)이 있는지 판정하세요.

- 모든 사실 주장이 [근거 문서]로 뒷받침되면 "완전근거"
- 일부만 뒷받침되거나 [근거 문서]로 확인할 수 없는 내용이 섞여 있으면 "부분근거"
- 핵심 주장이 [근거 문서]에 없거나 [근거 문서]와 반대되면 "근거없음"

다음 JSON 형식으로만 답하세요:
{{"판정": "완전근거 | 부분근거 | 근거없음", "근거없는_주장": ["..."], "설명": "..."}}
"""


def _relevancy_prompt(case: AnswerQualityCase) -> str:
    return f"""[사용자 질문]
{case.question}

[챗봇 답변]
{case.final_answer}

[챗봇 답변]이 [사용자 질문]에 실제로 답하고 있는지 판정하세요. 근거가 부족해
보류하는 답변이라도, 그 사정을 질문에 맞게 설명하면 "관련"입니다. 질문과
무관한 내용이거나 같은 되묻기를 반복할 뿐이면 "무관"입니다.

- 질문에 맞게 답했으면 "관련"
- 질문의 일부만 다루면 "부분관련"
- 질문과 무관하면 "무관"

다음 JSON 형식으로만 답하세요:
{{"판정": "관련 | 부분관련 | 무관", "설명": "..."}}
"""


def judge_faithfulness(llm_client: LLMClient, case: AnswerQualityCase) -> Judgment:
    if not case.evidence_text.strip():
        return Judgment(
            judged=False, score=None, verdict=None, reason="", error="인용된 정책 근거 없음"
        )
    return _judge(llm_client, _faithfulness_prompt(case), score_map=_VERDICT_SCORES)


def judge_relevancy(llm_client: LLMClient, case: AnswerQualityCase) -> Judgment:
    if not case.final_answer.strip():
        return Judgment(
            judged=False, score=None, verdict=None, reason="", error="final_answer가 비어 있음"
        )
    return _judge(llm_client, _relevancy_prompt(case), score_map=_RELEVANCY_SCORES)


@dataclass(frozen=True, slots=True)
class AnswerQualityRecord:
    question_id: str
    faithfulness: Judgment
    relevancy: Judgment


def run_answer_quality_eval(
    cases: Sequence[AnswerQualityCase],
    llm_client: LLMClient,
    *,
    workers: int = 1,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[AnswerQualityRecord]:
    """Score every case. A bad single case never aborts the batch — see
    ``Judgment.error`` for that case instead.

    Each case needs two LLM calls (faithfulness + relevancy), so a large
    question set run one case at a time can take a very long time with no
    visible progress in between - exactly the "did it hang?" situation this
    module has caused in practice. ``workers`` (default 1, matching the
    previous sequential behaviour so existing callers see no change) runs
    cases concurrently like ``validation_runner.run_questions`` already does
    for the main pipeline. ``progress_callback(done, total, question_id)`` is
    called after each case finishes (in completion order, not input order) so
    a caller can print something a person waiting on a slow run can see.
    """

    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    total = len(cases)
    done = 0
    lock = threading.Lock()

    def run_one(case: AnswerQualityCase) -> AnswerQualityRecord:
        nonlocal done
        record = AnswerQualityRecord(
            question_id=case.question_id,
            faithfulness=judge_faithfulness(llm_client, case),
            relevancy=judge_relevancy(llm_client, case),
        )
        with lock:
            done += 1
            current = done
        if progress_callback is not None:
            progress_callback(current, total, case.question_id)
        return record

    if workers == 1:
        return [run_one(case) for case in cases]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="answer-quality") as pool:
        return list(pool.map(run_one, cases))


def _summarize_judgments(judgments: Sequence[Judgment]) -> dict:
    judged = [j for j in judgments if j.judged and j.score is not None]
    counts: dict[str, int] = {}
    for j in judged:
        if j.verdict:
            counts[j.verdict] = counts.get(j.verdict, 0) + 1
    return {
        "mean_score": (sum(j.score for j in judged) / len(judged)) if judged else None,
        "judged_count": len(judged),
        "skipped_count": len(judgments) - len(judged),
        "verdict_counts": dict(sorted(counts.items())),
    }


def summarize_answer_quality(records: Sequence[AnswerQualityRecord]) -> dict:
    """Mean scores over judged-only records. A record whose judgment was
    skipped (no evidence to check, LLM call failed, unparseable output) is
    reported in ``skipped_count`` — never averaged in as a 0 and never
    silently omitted from the totals."""

    return {
        "question_count": len(records),
        "faithfulness": _summarize_judgments([r.faithfulness for r in records]),
        "relevancy": _summarize_judgments([r.relevancy for r in records]),
    }
