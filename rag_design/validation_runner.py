"""Fixed-question evaluation runner and dependency-free report renderer."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

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
        required = {"question_id", "question", "expected_policy_ids", "should_abstain"}
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
        seen.add(question_id)
        questions.append(item)
    if not questions:
        raise ValueError(f"{path}: no questions")
    return questions


def _request_latency_ms(response: Mapping, elapsed_ms: int) -> int:
    phases = (response.get("timing") or {}).get("phases") or {}
    measured = phases.get("request_total")
    if isinstance(measured, (int, float)) and measured >= 0:
        return round(measured * 1000)
    return elapsed_ms


def run_questions(
    questions: Sequence[Mapping],
    ask_fn: Callable[..., Mapping],
    *,
    top_k: int = 5,
) -> list[dict]:
    """Inject every question into ``ask_fn`` and return public evaluation records."""

    records: list[dict] = []
    for item in questions:
        question_id = str(item["question_id"])
        started = time.perf_counter()
        error: str | None = None
        try:
            response = dict(ask_fn(
                str(item["question"]), f"validation-{question_id}", top_k=top_k
            ))
        except Exception as exc:  # one bad case must not hide the remaining failures
            response = {}
            error = type(exc).__name__
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        policies = response.get("policies") or []
        retrieved = [
            str(policy["policy_id"])
            for policy in policies
            if isinstance(policy, Mapping) and policy.get("policy_id")
        ]
        citations = response.get("final_citations") or []
        cited = [
            str(citation["policy_id"])
            for citation in citations
            if isinstance(citation, Mapping) and citation.get("policy_id")
        ]
        answer_status = response.get("answer_status")
        records.append({
            "question_id": question_id,
            "expected_policy_ids": list(item["expected_policy_ids"]),
            "retrieved_policy_ids": retrieved,
            "cited_policy_ids": cited,
            "should_abstain": item["should_abstain"],
            "abstained": answer_status == "abstained",
            "answer_status": answer_status,
            "latency_ms": _request_latency_ms(response, elapsed_ms),
            "error": error,
        })
    return records


def calculate_summary(records: Sequence[Mapping], *, top_k: int) -> dict:
    retrieval = retrieval_metrics([
        RetrievalCase(frozenset(row["expected_policy_ids"]), tuple(row["retrieved_policy_ids"]))
        for row in records
    ], top_k)
    citation_cases = []
    for row in records:
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
    return {
        "retrieval": asdict(retrieval),
        "citation": asdict(citations),
        "abstention": asdict(abstention),
        "operations": asdict(operations),
    }


def _svg(summary: Mapping) -> str:
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
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_hash = hashlib.sha256(question_path.read_bytes()).hexdigest()
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question_set": str(question_path),
        "question_set_sha256": dataset_hash,
        "top_k": top_k,
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
        f"retrieved={row['retrieved_policy_ids'][:top_k]}, abstained={row['abstained']}, error={row['error']}"
        for row in failures
    ] or ["- 없음"]
    report = f"""# RAG Dev 검증 결과

![검증 지표](metrics.svg)

질문 세트 SHA-256: `{dataset_hash}`

질문 수: {len(records)}, Top-k: {top_k}

| 영역 | 지표 | 값 | 설명 |
|---|---|---:|---|
| 검색 | Recall@{top_k} | {summary['retrieval']['recall_at_k']:.3f} | 정답 정책 중 Top-{top_k} 안에 검색된 비율의 질문별 평균 |
| 검색 | MRR@{top_k} | {summary['retrieval']['mrr_at_k']:.3f} | 첫 정답 정책 순위의 역수 평균 |
| 인용 | Precision | {summary['citation']['precision']:.3f} | 노출한 정책 인용 중 정답 정책 비율 |
| 인용 | Coverage | {summary['citation']['coverage']:.3f} | 정답 정책 중 실제 인용된 비율 |
| 보류 | Precision / Recall | {summary['abstention']['precision']:.3f} / {summary['abstention']['recall']:.3f} | 보류 판단의 정확성과 필요한 보류를 잡은 비율 |
| 운영 | p50 / p95 | {summary['operations']['p50_latency_ms']:.0f} / {summary['operations']['p95_latency_ms']:.0f} ms | 전체 질문 응답시간의 중앙값 / 95백분위 |
| 운영 | 오류율 | {summary['operations']['error_rate']:.3f} | 예외가 발생한 질문 비율 |

## 실패 사례

{chr(10).join(failure_lines)}

## 해석 시 주의

- Dev set 결과이며 Holdout 결과가 아닙니다. Holdout은 최종 설정 확정 뒤 Gate 6에서 한 번만 실행합니다.
- 자동 지표는 의미적 답변 정확성을 확정하지 않습니다. `results.jsonl`을 근거로 사람 검토를 병행해야 합니다.
- 검색 평가는 현재 서비스 공개 응답의 정책 ID 단위입니다. 세부 chunk 단위 평가는 검색 trace를 별도 공개할 때 추가합니다.
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")
