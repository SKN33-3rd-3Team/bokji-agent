"""노드 함수를 한 곳에서 re-export한다."""

from .evidence_gate import evaluate_evidence
from .targeted_law_search import search_targeted_laws


__all__ = ["evaluate_evidence", "search_targeted_laws"]
