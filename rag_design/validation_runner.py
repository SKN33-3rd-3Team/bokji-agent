"""Fixed-question evaluation runner and dependency-free report renderer."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from .evaluation import (
    AbstentionCase,
    CitationCase,
    RetrievalCase,
    abstention_metrics,
    citation_metrics,
    operational_metrics,
    retrieval_metrics,
)


def load_questions(path: Path) -> list[dict]:
    """Load and strictly validate a frozen JSONL question set."""

    questions: list[dict] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        required = {
            "question_id",
            "question",
            "expected_policy_ids",
            "should_abstain",
        }
        missing = required.difference(item)
        if missing:
            raise ValueError(f"{path}:{line_number}: missing {sorted(missing)}")
        question_id = item["question_id"]
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError(f"{path}:{line_number}: question_id must be non-empty")
        if question_id in seen:
            raise ValueError(f"{path}:{line_number}: duplicate question_id {question_id!r}")
        if not isinstance(item["question"], str) or not item["question"].strip():
            raise ValueError(f"{path}:{line_number}: question must be non-empty")
        if not isinstance(item["expected_policy_ids"], list) or not all(
            isinstance(value, str) and value for value in item["expected_policy_ids"]
        ):
            raise ValueError(f"{path}:{line_number}: expected_policy_ids must be strings")
        if type(item["should_abstain"]) is not bool:
            raise ValueError(f"{path}:{line_number}: should_abstain must be boolean")
        slot_answers = item.get("slot_answers")
        if slot_answers is not None and (
            not isinstance(slot_answers, dict)
            or not all(
                isinstance(slot, str)
                and slot
                and isinstance(value, str)
                and value.strip()
                for slot, value in slot_answers.items()
            )
        ):
            raise ValueError(
                f"{path}:{line_number}: slot_answers must map slots to non-empty strings"
            )
        seen.add(question_id)
        questions.append(item)
    if not questions:
        raise ValueError(f"{path}: no questions")
    return questions


def run_questions(
    questions: Sequence[Mapping],
    ask_fn: Callable[..., Mapping],
    answer_followup_fn: Callable[[str, str], Mapping],
    *,
    top_k: int = 5,
    workers: int = 4,
    max_turns: int = 4,
    run_nonce: str | None = None,
) -> list[dict]:
    """Run each case to a terminal answer and preserve input order in results.

    Latency is the whole case's wall-clock duration.  Turns within one case remain
    serial and reuse that case's service session.
    """

    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 32:
        raise ValueError("workers must be an integer between 1 and 32")
    if (
        isinstance(max_turns, bool)
        or not isinstance(max_turns, int)
        or not 1 <= max_turns <= 32
    ):
        raise ValueError("max_turns must be an integer between 1 and 32")
    if run_nonce is None:
        run_nonce = uuid4().hex
    elif not isinstance(run_nonce, str) or not run_nonce.strip():
        raise ValueError("run_nonce must be a non-empty string")

    def run_one(item: Mapping) -> dict:
        question_id = str(item["question_id"])
        session_id = f"validation-{run_nonce}-{question_id}"
        started = time.perf_counter()
        error: str | None = None
        turn_count = 0
        response: dict = {}
        first_turn_status: str | None = None
        first_missing_slots: list[str] = []
        requested_missing_slots: list[str] = []
        seen_requests: set[tuple[str, ...]] = set()

        def invoke(fn: Callable, *args, **kwargs) -> tuple[dict, str | None]:
            nonlocal turn_count
            try:
                result = dict(fn(*args, **kwargs))
            except Exception as exc:  # one bad case must not hide remaining failures
                turn_count += 1
                return {}, type(exc).__name__
            turn_count += 1
            return result, None

        response, error = invoke(
            ask_fn, str(item["question"]), session_id, top_k=top_k
        )
        if error is None and isinstance(response.get("status"), str):
            first_turn_status = response["status"]

        while error is None:
            status = response.get("status")
            if response.get("session_id") != session_id:
                error = "SessionMismatch"
                break
            if status == "answered":
                if response.get("answer_status") not in {
                    "complete",
                    "partial",
                    "abstained",
                } or not isinstance(response.get("final_answer"), str) or not response[
                    "final_answer"
                ].strip() or not isinstance(response.get("policies"), list) or not isinstance(
                    response.get("final_citations"), list
                ):
                    error = "IncompleteAnsweredResponse"
                break
            if status != "needs_input":
                error = "UnexpectedStatus"
                break

            missing_slots = response.get("missing_slots")
            if not isinstance(missing_slots, list) or not missing_slots or not all(
                isinstance(slot, str) and slot.strip() for slot in missing_slots
            ) or len(set(missing_slots)) != len(missing_slots):
                error = "InvalidNeedsInput"
                break
            if not isinstance(response.get("question"), str) or not response[
                "question"
            ].strip():
                error = "InvalidNeedsInput"
                break

            if first_turn_status == "needs_input" and not first_missing_slots:
                first_missing_slots = list(missing_slots)
            for slot in missing_slots:
                if slot not in requested_missing_slots:
                    requested_missing_slots.append(slot)

            request_signature = tuple(sorted(missing_slots))
            if request_signature in seen_requests:
                error = "RepeatedNeedsInput"
                break
            seen_requests.add(request_signature)
            if turn_count >= max_turns:
                error = "MaxTurnsExceeded"
                break

            slot_answers = item.get("slot_answers")
            if slot_answers is None:
                error = "MissingSlotFixture"
                break
            if not isinstance(slot_answers, Mapping) or not all(
                isinstance(slot, str)
                and slot
                and isinstance(value, str)
                and value.strip()
                for slot, value in slot_answers.items()
            ):
                error = "InvalidSlotFixture"
                break
            if any(slot not in slot_answers for slot in missing_slots):
                error = "MissingSlotFixture"
                break
            followup_answer = " ".join(
                str(slot_answers[slot]).strip() for slot in missing_slots
            )

            response, error = invoke(
                answer_followup_fn, session_id, followup_answer
            )

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        terminal_status = "answered" if error is None else "failed"
        policies = response.get("policies") if terminal_status == "answered" else []
        retrieved = [
            str(policy["policy_id"])
            for policy in policies
            if isinstance(policy, Mapping) and policy.get("policy_id")
        ]
        citations = (
            response.get("final_citations") if terminal_status == "answered" else []
        )
        cited = [
            str(citation["policy_id"])
            for citation in citations
            if isinstance(citation, Mapping) and citation.get("policy_id")
        ]
        answer_status = response.get("answer_status")
        return {
            "question_id": question_id,
            "session_id": session_id,
            "expected_policy_ids": list(item["expected_policy_ids"]),
            "retrieved_policy_ids": retrieved,
            "cited_policy_ids": cited,
            "should_abstain": item["should_abstain"],
            "abstained": terminal_status == "answered"
            and answer_status == "abstained",
            "answer_status": answer_status,
            "first_turn_status": first_turn_status,
            "terminal_status": terminal_status,
            "last_response_status": response.get("status"),
            "first_missing_slots": first_missing_slots,
            "requested_missing_slots": requested_missing_slots,
            "turn_count": turn_count,
            "latency_ms": elapsed_ms,
            "error": error,
        }

    if workers == 1:
        return [run_one(item) for item in questions]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag-validation") as pool:
        return list(pool.map(run_one, questions))


def calculate_summary(records: Sequence[Mapping], *, top_k: int) -> dict:
    completed = [
        row
        for row in records
        if not row.get("error") and row.get("terminal_status") == "answered"
    ]
    quality_metrics_valid = bool(records) and len(completed) == len(records)
    retrieval = retrieval_metrics([
        RetrievalCase(frozenset(row["expected_policy_ids"]), tuple(row["retrieved_policy_ids"]))
        for row in completed
    ], top_k)
    citation_cases = []
    for row in completed:
        expected = frozenset(row["expected_policy_ids"])
        claim_ids = frozenset(f"policy:{value}" for value in expected)
        cited = tuple(row["cited_policy_ids"])
        citation_cases.append(CitationCase(
            evidence_chunk_ids=expected,
            cited_chunk_ids=cited,
            claim_citations={f"policy:{value}": (value,) for value in cited},
            required_claim_ids=claim_ids,
            claim_evidence_chunk_ids={f"policy:{value}": frozenset({value}) for value in expected},
        ))
    citations = citation_metrics(citation_cases)
    abstention = abstention_metrics([
        AbstentionCase(bool(row["should_abstain"]), bool(row["abstained"]), bool(row["error"]))
        for row in records
    ])
    operations = operational_metrics(
        [int(row["latency_ms"]) for row in records],
        [bool(row["error"]) for row in records],
    )
    terminal_status_counts = Counter(
        str(row.get("terminal_status", "unknown")) for row in records
    )
    first_missing_slot_counts = Counter(
        str(slot)
        for row in records
        for slot in row.get("first_missing_slots", [])
    )
    answer_status_counts = Counter(
        str(row["answer_status"])
        for row in completed
        if row.get("answer_status") is not None
    )
    turn_counts = [int(row.get("turn_count", 0)) for row in records]
    first_turn_answered_count = sum(
        row.get("first_turn_status") == "answered" for row in records
    )
    first_turn_needs_input_count = sum(
        row.get("first_turn_status") == "needs_input" for row in records
    )
    return {
        "quality_metrics_valid": quality_metrics_valid,
        "retrieval": asdict(retrieval),
        "citation": asdict(citations),
        "abstention": asdict(abstention),
        "operations": asdict(operations),
        "conversation": {
            "first_turn_answered_count": first_turn_answered_count,
            "first_turn_needs_input_count": first_turn_needs_input_count,
            "first_turn_failed_count": (
                len(records)
                - first_turn_answered_count
                - first_turn_needs_input_count
            ),
            "terminal_status_counts": dict(sorted(terminal_status_counts.items())),
            "answer_status_counts": dict(sorted(answer_status_counts.items())),
            "first_missing_slot_counts": dict(
                sorted(first_missing_slot_counts.items())
            ),
            "quality_eligible_count": len(completed),
            "total_turn_count": sum(turn_counts),
            "max_turn_count": max(turn_counts, default=0),
        },
    }


def _svg(summary: Mapping) -> str:
    if not summary.get("quality_metrics_valid"):
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="150" '
            'viewBox="0 0 600 150"><rect width="100%" height="100%" fill="white"/>'
            '<text x="20" y="42" font-size="20" font-weight="bold" fill="#b91c1c">'
            'Quality metrics not publishable</text>'
            '<text x="20" y="78" font-size="14">At least one case did not reach a valid terminal answer.</text>'
            '<text x="20" y="106" font-size="14">Use results.jsonl for failure diagnosis, then rerun the full set.</text>'
            "</svg>"
        )

    metrics = [
        ("Recall@k", summary["retrieval"]["recall_at_k"]),
        ("MRR@k", summary["retrieval"]["mrr_at_k"]),
        ("Citation precision", summary["citation"]["precision"]),
        ("Citation coverage", summary["citation"]["coverage"]),
        ("Abstention precision", summary["abstention"]["precision"]),
        ("Abstention recall", summary["abstention"]["recall"]),
        ("Success rate", 1 - summary["operations"]["error_rate"]),
    ]
    rows = []
    for index, (label, value) in enumerate(metrics):
        y = 56 + index * 42
        width = max(0, min(1, float(value))) * 360
        rows.append(
            f'<text x="12" y="{y + 15}" font-size="13">{label}</text>'
            f'<rect x="160" y="{y}" width="360" height="22" rx="4" fill="#e5e7eb"/>'
            f'<rect x="160" y="{y}" width="{width:.1f}" height="22" rx="4" fill="#2563eb"/>'
            f'<text x="528" y="{y + 16}" font-size="13">{value:.3f}</text>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="370" '
        'viewBox="0 0 600 370"><rect width="100%" height="100%" fill="white"/>'
        '<text x="12" y="28" font-size="20" font-weight="bold">RAG validation metrics</text>'
        + "".join(rows) + "</svg>"
    )


def write_report(
    output_dir: Path,
    records: Sequence[Mapping],
    summary: Mapping,
    *,
    question_path: Path,
    top_k: int,
    workers: int = 1,
    max_turns: int = 4,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_hash = hashlib.sha256(question_path.read_bytes()).hexdigest()
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question_set": str(question_path),
        "question_set_sha256": dataset_hash,
        "top_k": top_k,
        "workers": workers,
        "max_turns": max_turns,
        "question_count": len(records),
        **summary,
    }
    (output_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "metrics.svg").write_text(_svg(summary), encoding="utf-8")
    failures = [row for row in records if row["error"] or (
        row["expected_policy_ids"] and not set(row["expected_policy_ids"]).intersection(row["retrieved_policy_ids"][:top_k])
    ) or bool(row["should_abstain"]) != bool(row["abstained"])]
    failure_lines = [
        f"- `{row['question_id']}`: expected={row['expected_policy_ids']}, "
        f"retrieved={row['retrieved_policy_ids'][:top_k]}, abstained={row['abstained']}, "
        f"first_missing={row.get('first_missing_slots', [])}, "
        f"terminal={row.get('terminal_status')}, turns={row.get('turn_count')}, "
        f"error={row['error']}"
        for row in failures
    ] or ["- 없음"]
    conversation = summary["conversation"]
    quality_metrics_valid = bool(summary.get("quality_metrics_valid"))
    if quality_metrics_valid:
        recall_value = f"{summary['retrieval']['recall_at_k']:.3f}"
        mrr_value = f"{summary['retrieval']['mrr_at_k']:.3f}"
        citation_precision_value = f"{summary['citation']['precision']:.3f}"
        citation_coverage_value = f"{summary['citation']['coverage']:.3f}"
        abstention_value = (
            f"{summary['abstention']['precision']:.3f} / "
            f"{summary['abstention']['recall']:.3f}"
        )
        quality_notice = "품질 지표 유효: 모든 질문이 terminal answered 상태로 완료되었습니다."
    else:
        recall_value = mrr_value = "게시 불가"
        citation_precision_value = citation_coverage_value = "게시 불가"
        abstention_value = "게시 불가"
        quality_notice = (
            "> [!WARNING]\n"
            "> 하나 이상의 질문이 terminal answered 상태에 도달하지 못해 "
            "품질 지표를 비교 가능한 Baseline으로 게시할 수 없습니다. "
            "완료된 일부 질문의 내부 계산값은 summary.json에 진단용으로만 남습니다."
        )
    report = f"""# RAG Dev 검증 결과

![검증 지표](metrics.svg)

질문 세트 SHA-256: `{dataset_hash}`

질문 수: {len(records)}, Top-k: {top_k}, 병렬 worker: {workers}, 질문별 최대 턴: {max_turns}

{quality_notice}

첫 턴 answered: {conversation['first_turn_answered_count']}건, 첫 턴 needs_input: {conversation['first_turn_needs_input_count']}건, 첫 턴 실패: {conversation['first_turn_failed_count']}건

첫 턴 부족 슬롯 빈도: {json.dumps(conversation['first_missing_slot_counts'], ensure_ascii=False, sort_keys=True)}

최종 답변 상태: {json.dumps(conversation['answer_status_counts'], ensure_ascii=False, sort_keys=True)}, 최종 실패: {conversation['terminal_status_counts'].get('failed', 0)}건, 최대 턴: {conversation['max_turn_count']}

| 영역 | 지표 | 값 | 설명 |
|---|---|---:|---|
| 검색 | Recall@{top_k} | {recall_value} | 정답 정책 중 Top-{top_k} 안에 검색된 비율의 질문별 평균 |
| 검색 | MRR@{top_k} | {mrr_value} | 첫 정답 정책 순위의 역수 평균 |
| 인용 | Precision | {citation_precision_value} | 노출한 정책 인용 중 정답 정책 비율 |
| 인용 | Coverage | {citation_coverage_value} | 정답 정책 중 실제 인용된 비율 |
| 보류 | Precision / Recall | {abstention_value} | 보류 판단의 정확성과 필요한 보류를 잡은 비율 |
| 운영 | p50 / p95 | {summary['operations']['p50_latency_ms']:.0f} / {summary['operations']['p95_latency_ms']:.0f} ms | 전체 질문 응답시간의 중앙값 / 95백분위 |
| 운영 | 오류율 | {summary['operations']['error_rate']:.3f} | 예외가 발생한 질문 비율 |

## 실패 사례

{chr(10).join(failure_lines)}

## 해석 시 주의

- Dev set 결과이며 Holdout 결과가 아닙니다. Holdout은 최종 설정 확정 뒤 Gate 6에서 한 번만 실행합니다.
- 이 benchmark는 fixture가 있는 추가 정보 요청을 같은 세션에서 완료한 뒤의 multi-turn 품질을 측정합니다. 첫 턴 추출 회귀는 별도 first-turn KPI로 확인해야 합니다.
- 자동 지표는 의미적 답변 정확성을 확정하지 않습니다. `results.jsonl`을 근거로 사람 검토를 병행해야 합니다.
- 검색 평가는 현재 서비스 공개 응답의 정책 ID 단위입니다. 세부 chunk 단위 평가는 검색 trace를 별도 공개할 때 추가합니다.
- worker 수와 관계없이 첫 요청부터 terminal 결과까지 질문 작업별 전체 wall-clock 시간을 지연시간으로 계산합니다.
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")
