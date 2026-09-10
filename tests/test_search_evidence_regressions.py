"""Frozen offline behavioral controls for the three approved search fixes."""

from dataclasses import replace
from datetime import date
import json
from pathlib import Path

import pytest

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, RetrievedChunk, SourceType
from rag_design.vector_store import ChromaVectorStore
from rag_chatbot.graph.nodes.document_verification import verify_official_documents
from rag_chatbot.graph.nodes.eligibility_verdict import determine_eligibility
from rag_chatbot.graph.nodes.policy_search import _build_query, search_policies


FIXTURE = Path(__file__).parent / "fixtures" / "documents.jsonl"
AS_OF = date(2026, 9, 9)
REASON = "읍면동 주민센터 또는 온라인에서 신청합니다."


def subsidy_chunks():
    documents = [Document.from_dict(json.loads(line)) for line in
                 FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return list(chunk_document(next(d for d in documents
                                   if d.source_type is SourceType.SUBSIDY)))


def retrieved(chunk):
    return RetrievedChunk(query_id="frozen", chunk=chunk, rank=1, score=0.0,
                          score_type="cosine_distance", retriever_version="fixture",
                          index_name="subsidy")


class OfflineStore:
    """Stub ranking/metadata I/O only; use the real portable store filter."""

    def __init__(self, candidates, full=()):
        self.candidates = candidates
        self.full = full
        self.filter_store = object.__new__(ChromaVectorStore)

    def search(self, source_type, query, *, search_filter=None, **kwargs):
        return [c for c in self.candidates if search_filter is None or
                self.filter_store._matches_filter(c.chunk, {}, source_type, search_filter)]

    def get_chunks_by_metadata(self, source_type, *, metadata_equals, **kwargs):
        return [c for c in self.full if c.source_type is source_type and
                all(c.metadata.get(k) == v for k, v in metadata_equals.items())]


def check(record_property, expected, observed):
    record_property("expected", json.dumps(expected, ensure_ascii=False))
    record_property("observed", json.dumps(observed, ensure_ascii=False))
    assert observed == expected


@pytest.mark.parametrize("case", ["short_valid", "six_char_valid", "interest",
                                     "greeting", "empty", "pii" ])
def test_query(case, record_property):
    questions = {"short_valid": "주거급여", "six_char_valid": "주거급여문의",
                 "interest": "주거급여", "greeting": "안녕", "empty": None,
                 "pii": "주거급여 010-1234-5678 1990년 3월 15일생"}
    query = _build_query({"interests": ["주거급여"]} if case == "interest" else {},
                         questions[case])
    if case == "interest":
        check(record_property, True, "주거급여" in query)
    elif case == "pii":
        check(record_property, {"intent": True, "phone": False, "birth": False},
              {"intent": "주거급여" in query, "phone": "010-1234-5678" in query,
               "birth": "1990년 3월 15일" in query})
    else:
        expected = {"short_valid": "주거급여", "six_char_valid": "주거급여문의",
                    "interest": "주거급여", "greeting": "생활 지원 복지 서비스",
                    "empty": "생활 지원 복지 서비스"}[case]
        check(record_property, expected, query)


@pytest.mark.parametrize("case,basis,lower,upper,subject,expected", [
    ("year_min19", "year", 19, None, "self", True),
    ("actual_min19", "international_age", 19, None, "self", False),
    ("actual_min18", "international_age", 18, None, "self", True),
    ("year_max18", "year", None, 18, "self", False),
    ("missing_age", None, None, None, "self", True),
    ("non_self", "international_age", 19, None, "child", True),
    ("none_basis_legacy", None, 19, None, "self", False),
    ("unknown_basis_legacy", "unknown", 19, None, "self", False),
], ids=lambda value: str(value))
def test_age(case, basis, lower, upper, subject, expected, record_property):
    # "year" is explicitly requested new support, not a verified collector enum.
    chunk = subsidy_chunks()[0]
    chunk = replace(chunk, metadata={**chunk.metadata, "region_scope": "national",
                    "region_names": ["전국"], "age_basis": basis,
                    "age_start": lower, "age_end": upper,
                    "effective_from": None, "effective_to": None})
    state = {"query_id": "frozen", "as_of": AS_OF, "slots": {
        "birth_date": "2007-12-31", "age": 18, "age_year_based": 19,
        "age_subject": subject, "region_names": ["서울특별시"]}}
    result = search_policies(state, OfflineStore([retrieved(chunk)], [chunk]))
    check(record_property, expected, bool(result["subsidy_chunks"]))


@pytest.mark.parametrize("case", ["full_only", "policy_isolation", "partial",
                                     "representative_fallback", "retry", "disabled"])
def test_evidence(case, record_property):
    chunks = subsidy_chunks()
    matching = next(c for c in chunks if REASON in c.text)
    representative = next(c for c in chunks if REASON not in c.text)
    claim = {"claim_id": "frozen", "policy_id": matching.metadata["source_id"],
             "doc_check_required": True, "doc_retry_count": 0,
             "reasons": [REASON], "status": "pending", "evidence_chunk_ids": []}
    state = {"query_id": "frozen", "claim_plan": [claim],
             "subsidy_chunks": [retrieved(representative)],
             "subsidy_full_chunks": [retrieved(c) for c in chunks]}
    expected = {"status": "supported", "ids": [matching.chunk_id]}
    store = None
    if case == "policy_isolation":
        foreign = replace(matching, chunk_id="foreign-evidence", doc_id="foreign-doc",
                          metadata={**matching.metadata, "source_id": "other-policy"})
        state["subsidy_full_chunks"] = [retrieved(foreign)]
        expected = {"status": "unsupported", "ids": []}
    elif case == "partial":
        claim["reasons"] = [REASON, "공개 픽스처 어디에도 없는 조건"]
        state["subsidy_chunks"] = [retrieved(matching)]
        expected["status"] = "partial"
    elif case == "representative_fallback":
        state.pop("subsidy_full_chunks")
        state["subsidy_chunks"] = [retrieved(matching)]
    elif case == "retry":
        state.pop("subsidy_full_chunks")
        claim["doc_retry_count"] = 1
        store = OfflineStore([retrieved(matching)])
    elif case == "disabled":
        claim["doc_check_required"] = False
        expected = {"status": "pending", "ids": []}
    result = verify_official_documents(state, store=store)["claim_plan"][0]
    check(record_property, expected,
          {"status": result["status"], "ids": result["evidence_chunk_ids"]})


@pytest.mark.parametrize("case,basis,expected", [
    ("year_min19", "year", "충족"),
    ("actual_min19", "international_age", "미충족"),
    ("non_self", "international_age", None),
    ("age_only_legacy", "international_age", "미충족"),
    ("none_basis_legacy", None, "미충족"),
    ("unknown_basis_legacy", "unknown", "미충족"),
    ("non_self_unknown_claim", "international_age", "미확인"),
])
def test_n9_age(case, basis, expected, record_property):
    # Additional N9 consistency control frozen before production edits.
    chunk = subsidy_chunks()[0]
    chunk = replace(chunk, metadata={**chunk.metadata, "region_scope": "national",
                    "region_names": ["전국"], "age_basis": basis,
                    "age_start": 19, "age_end": None})
    state = {"query_id": "frozen", "as_of": AS_OF, "slots": {
        "birth_date": "2007-12-31", "age": 18, "age_year_based": 19,
        "age_subject": "self"}, "claim_plan": [{
            "claim_id": "n9-control", "policy_id": chunk.metadata["source_id"],
            "claim_type": "eligibility", "status": "supported", "reasons": []}]}
    if case in ("non_self", "non_self_unknown_claim"):
        state["slots"]["age_subject"] = "child"
    elif case == "age_only_legacy":
        state["slots"] = {"age": 18}
    if case == "non_self_unknown_claim":
        state["claim_plan"][0]["status"] = "unsupported"
    result = determine_eligibility(state, OfflineStore([retrieved(chunk)]))
    verdict = result["eligibility_verdicts"][0]
    if case == "non_self":
        # No claim of actual eligibility: only requester-age misuse is tested.
        check(record_property, {"requester_age_violation": False, "age_checked": False},
              {"requester_age_violation": any("연령 조건 미충족" in r
                                               for r in verdict.get("reasons", [])),
               "age_checked": "연령" in verdict.get("checked", [])})
    else:
        check(record_property, expected, verdict["verdict"])
