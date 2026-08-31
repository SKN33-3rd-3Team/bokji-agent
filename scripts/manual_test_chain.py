"""N9 → N10 → N11 → N12를 실제로 이어붙여서(체이닝) 돌려보는 수동 테스트
스크립트. 각 노드가 이전 노드의 출력을 실제로 입력받아 동작하는지 한 번에
확인하는 용도입니다.

주의: 이 스크립트는 로컬 전용 브랜치(local/n9-n12-chain-test)에만 있습니다.
N9/N10/N11/N12는 원래 각자 별도 브랜치에서 개발됐는데, 네 노드를 한 번에
연결해서 보려면 네 브랜치의 노드 파일이 한 작업 트리에 동시에 있어야 해서,
main에서 새로 로컬 브랜치를 만들고 네 브랜치를 전부 merge해서 만들었습니다.
(각 노드 브랜치 자체의 커밋/이력은 전혀 건드리지 않았습니다 - 이 브랜치는
그냥 로컬 확인용이고 push하지 않습니다.)

사용법 (레포 루트에서):
    python scripts/manual_test_chain.py

"입력값" 구역만 원하는 값으로 바꾸면 됩니다.

INJECT_METADATA 옵션:
지금 실제로 색인된 vectorDB(data/vector_db)에는 나이 조건/금액/상호배타 같은
구조화 metadata가 아직 없어서, 그대로 쓰면 항상 "충족" + amount=None +
"미확인"만 나옵니다. INJECT_METADATA = True로 켜면, 두 실제 정책 문서
(제목/본문은 진짜)에 테스트용 구조화 metadata를 주입한 뒤 임시 vectorDB에
색인해서 씁니다 (원본 data/vector_db는 건드리지 않음). 기본값으로는 두
정책 다 "충족"이 나오게 해뒀고, 아래 AGE_START_A를 65 같은 값으로 바꾸면
"미충족"도, POLICY_A의 mutually_exclusive_with 덕분에 "불가"도 같이 볼 수
있습니다.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, EvidenceStatus, SourceType
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from src.rag_chatbot.graph.nodes import (
    assemble_result,
    calculate_benefit_amount,
    check_duplicate_benefit,
    determine_eligibility,
)

# ── 입력값 (여기만 수정하세요) ──────────────────────────────────────
POLICY_A = "subsidy:000000465790:2026-01-29"       # 유아학비(누리과정) 지원
POLICY_B = "subsidy:105100000001:2026-04-30T09:39:00+09:00"  # 근로·자녀장려금

USER_SLOTS = {"age": 4}

# claim_plan: 원래 N7/N8이 만들어 넘겨주는 값. 여기서는 두 정책 모두 자격/
# 금액/중복수급 근거를 다 찾았다고 가정하고 직접 채웁니다.
CLAIM_PLAN = [
    {
        "claim_id": f"{POLICY_A}-eligibility",
        "policy_id": POLICY_A,
        "claim_type": "eligibility",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["유아학비 지원 대상 근거"],
    },
    {
        "claim_id": f"{POLICY_A}-amount",
        "policy_id": POLICY_A,
        "claim_type": "amount",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["유아학비 금액 근거"],
    },
    {
        "claim_id": f"{POLICY_A}-duplicate",
        "policy_id": POLICY_A,
        "claim_type": "duplicate",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["중복수급 관련 근거"],
    },
    {
        "claim_id": f"{POLICY_B}-eligibility",
        "policy_id": POLICY_B,
        "claim_type": "eligibility",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["근로·자녀장려금 지원 대상 근거"],
    },
    {
        "claim_id": f"{POLICY_B}-amount",
        "policy_id": POLICY_B,
        "claim_type": "amount",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["근로·자녀장려금 금액 근거"],
    },
    {
        "claim_id": f"{POLICY_B}-duplicate",
        "policy_id": POLICY_B,
        "claim_type": "duplicate",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["중복수급 관련 근거"],
    },
]

# True로 켜면 두 정책의 실제 문서에 아래 값들을 테스트용 metadata로 주입한
# 임시 vectorDB를 만들어서 씁니다 (data/vector_db는 안 건드림).
INJECT_METADATA = True

# 정책 A: 나이 조건 없음(None=제한 없음) → USER_SLOTS와 상관없이 충족.
# 65 같은 값으로 바꾸면 USER_SLOTS["age"]=4와 안 맞아서 "미충족"이 됩니다.
AGE_START_A = None
AGE_END_A = None
AMOUNT_A = 300000
# 정책 B와 중복수급 불가 관계라고 가정 (A쪽 문서에만 이 조항이 있다고 가정).
EXCLUSIONS_A = [POLICY_B]

# 정책 B: 나이 조건 없음. 금액은 있지만, 상호배타 조항은 B쪽 문서에는
# 없다고 가정(EXCLUSIONS_B 비워둠) → A는 "불가"인데 B는 "미확인"이 되는
# 비대칭을 보여줍니다 (실제로 정책마다 조항 기재 여부가 다를 수 있으므로).
AGE_START_B = None
AGE_END_B = None
AMOUNT_B = 150000
EXCLUSIONS_B: list[str] = []
# ── 여기까지 ────────────────────────────────────────────────────────


def _load_document(policy_id: str) -> Document:
    documents_path = Path("data/processed/subsidy_documents.filtered.jsonl")
    with documents_path.open(encoding="utf-8") as f:
        for line in f:
            candidate = Document.from_dict(json.loads(line))
            if candidate.doc_id == policy_id:
                return candidate
    raise SystemExit(f"policy_id를 못 찾음: {policy_id} ({documents_path} 안에 있는지 확인)")


def _inject(chunks, *, age_start, age_end, amount, exclusions):
    extra = {"age_start": age_start, "age_end": age_end, "amount": amount}
    if exclusions:
        extra["mutually_exclusive_with"] = exclusions
    return tuple(replace(chunk, metadata={**chunk.metadata, **extra}) for chunk in chunks)


def _build_injected_store() -> ChromaVectorStore:
    document_a = _load_document(POLICY_A)
    document_b = _load_document(POLICY_B)

    chunks_a = _inject(
        chunk_document(document_a),
        age_start=AGE_START_A, age_end=AGE_END_A, amount=AMOUNT_A, exclusions=EXCLUSIONS_A,
    )
    chunks_b = _inject(
        chunk_document(document_b),
        age_start=AGE_START_B, age_end=AGE_END_B, amount=AMOUNT_B, exclusions=EXCLUSIONS_B,
    )

    temporary_directory = tempfile.mkdtemp(prefix="chain_manual_test_")
    store = ChromaVectorStore(
        HashEmbeddingProvider(128),
        VectorStoreConfig(persist_directory=Path(temporary_directory), collection_prefix="manual_test"),
    )
    store.sync_snapshot(SourceType.SUBSIDY, chunks_a + chunks_b, snapshot_id="manual-chain-test-snap")
    return store


if INJECT_METADATA:
    print(
        "[안내] 두 정책 문서에 테스트용 metadata(나이 조건/금액/상호배타)를 주입한 "
        "임시 vectorDB를 사용합니다 (data/vector_db는 안 건드림).\n"
    )
    store = _build_injected_store()
else:
    store = ChromaVectorStore(
        HashEmbeddingProvider(128),
        VectorStoreConfig(persist_directory=Path("data/vector_db"), collection_prefix="bokji_rag"),
    )

state: dict = {
    "query_id": "manual-chain-test",
    "slots": USER_SLOTS,
    "claim_plan": CLAIM_PLAN,
}


def run_node(label: str, fn, *args) -> None:
    print(f"\n=== {label} 실행 ===")
    update = fn(*args)
    state.update(update)
    print(json.dumps(update, ensure_ascii=False, indent=2, default=str))


run_node("N9 (determine_eligibility)", determine_eligibility, state, store)
run_node("N10 (calculate_benefit_amount)", calculate_benefit_amount, state, store)
run_node("N11 (check_duplicate_benefit)", check_duplicate_benefit, state, store)
run_node("N12 (assemble_result)", assemble_result, state, store)

print("\n=== 최종 state (N13에 넘어갈 값) ===")
print(json.dumps(
    {
        "assembled_result": state.get("assembled_result"),
        "node_trace": state.get("node_trace"),
    },
    ensure_ascii=False,
    indent=2,
    default=str,
))
