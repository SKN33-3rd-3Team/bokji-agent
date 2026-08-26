"""Pure metric calculations for fixed Dev/Holdout result records."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from statistics import median
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    expected_chunk_ids: frozenset[str]
    retrieved_chunk_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_at_k: float
    mrr_at_k: float
    evaluated_queries: int


def retrieval_metrics(cases: Sequence[RetrievalCase], k: int) -> RetrievalMetrics:
    """Macro relevant-chunk Recall@k and MRR@k.

    Cases without gold evidence are excluded; if no evaluable case remains, both
    metrics are 0.0 and ``evaluated_queries`` records the zero denominator.
    """

    if k < 1:
        raise ValueError("k must be positive")
    evaluable = [case for case in cases if case.expected_chunk_ids]
    if not evaluable:
        return RetrievalMetrics(0.0, 0.0, 0)
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    for case in evaluable:
        top_k = case.retrieved_chunk_ids[:k]
        hits = case.expected_chunk_ids.intersection(top_k)
        recalls.append(len(hits) / len(case.expected_chunk_ids))
        first_rank = next(
            (
                rank
                for rank, chunk_id in enumerate(top_k, start=1)
                if chunk_id in case.expected_chunk_ids
            ),
            None,
        )
        reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
    return RetrievalMetrics(
        recall_at_k=sum(recalls) / len(recalls),
        mrr_at_k=sum(reciprocal_ranks) / len(reciprocal_ranks),
        evaluated_queries=len(evaluable),
    )


@dataclass(frozen=True, slots=True)
class CitationCase:
    evidence_chunk_ids: frozenset[str]
    cited_chunk_ids: tuple[str, ...]
    claim_citations: Mapping[str, tuple[str, ...]]
    required_claim_ids: frozenset[str]
    claim_evidence_chunk_ids: Mapping[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class CitationMetrics:
    precision: float
    coverage: float
    citation_pair_count: int
    required_claim_count: int


def citation_metrics(cases: Sequence[CitationCase]) -> CitationMetrics:
    citation_pair_count = 0
    valid_pairs = 0
    for case in cases:
        cited = set(case.cited_chunk_ids)
        assigned: set[str] = set()
        for claim_id, citations in case.claim_citations.items():
            gold = case.claim_evidence_chunk_ids.get(claim_id, frozenset())
            for chunk_id in citations:
                citation_pair_count += 1
                assigned.add(chunk_id)
                if (
                    chunk_id in cited
                    and chunk_id in case.evidence_chunk_ids
                    and chunk_id in gold
                ):
                    valid_pairs += 1
        # A rendered citation without a claim assignment is an invalid pair.
        citation_pair_count += sum(
            chunk_id not in assigned for chunk_id in case.cited_chunk_ids
        )
    required_claim_count = sum(len(case.required_claim_ids) for case in cases)
    covered_claims = 0
    for case in cases:
        for claim_id in case.required_claim_ids:
            citations = case.claim_citations.get(claim_id, ())
            cited = set(case.cited_chunk_ids)
            claim_evidence = case.claim_evidence_chunk_ids.get(claim_id, frozenset())
            if citations and any(
                value in case.evidence_chunk_ids
                and value in claim_evidence
                and value in cited
                for value in citations
            ):
                covered_claims += 1
    # A zero denominator is reported as 0.0, never as a perfect score.
    return CitationMetrics(
        precision=(
            valid_pairs / citation_pair_count if citation_pair_count else 0.0
        ),
        coverage=(
            covered_claims / required_claim_count if required_claim_count else 0.0
        ),
        citation_pair_count=citation_pair_count,
        required_claim_count=required_claim_count,
    )


@dataclass(frozen=True, slots=True)
class AbstentionCase:
    should_abstain: bool
    abstained: bool
    error: bool = False

    def __post_init__(self) -> None:
        for field_name in ("should_abstain", "abstained", "error"):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")


@dataclass(frozen=True, slots=True)
class AbstentionMetrics:
    precision: float
    recall: float
    true_positive: int
    predicted_positive: int
    actual_positive: int
    evaluated_cases: int


def abstention_metrics(cases: Sequence[AbstentionCase]) -> AbstentionMetrics:
    # Pipeline errors are measured separately, not counted as abstentions.
    evaluable = [case for case in cases if not case.error]
    true_positive = sum(case.should_abstain and case.abstained for case in evaluable)
    predicted_positive = sum(case.abstained for case in evaluable)
    actual_positive = sum(case.should_abstain for case in evaluable)
    return AbstentionMetrics(
        precision=true_positive / predicted_positive if predicted_positive else 0.0,
        recall=true_positive / actual_positive if actual_positive else 0.0,
        true_positive=true_positive,
        predicted_positive=predicted_positive,
        actual_positive=actual_positive,
        evaluated_cases=len(evaluable),
    )


@dataclass(frozen=True, slots=True)
class OperationalMetrics:
    p50_latency_ms: float
    p95_latency_ms: float
    error_rate: float
    sample_count: int


def operational_metrics(latencies_ms: Sequence[int], errors: Iterable[bool]) -> OperationalMetrics:
    error_values = tuple(errors)
    if len(latencies_ms) != len(error_values):
        raise ValueError("latencies and errors must have the same length")
    if any(value < 0 for value in latencies_ms):
        raise ValueError("latency must be non-negative")
    if not latencies_ms:
        return OperationalMetrics(0.0, 0.0, 0.0, 0)
    ordered = sorted(latencies_ms)
    p95_index = max(0, ceil(0.95 * len(ordered)) - 1)
    return OperationalMetrics(
        p50_latency_ms=float(median(ordered)),
        p95_latency_ms=float(ordered[p95_index]),
        error_rate=sum(error_values) / len(error_values),
        sample_count=len(ordered),
    )
