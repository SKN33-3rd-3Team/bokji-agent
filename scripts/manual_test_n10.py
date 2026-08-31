"""N10(지원금 계산 노드)를 실제 vectorDB(data/vector_db)로 직접 돌려보는
수동 테스트 스크립트. "입력값" 구역만 원하는 값으로 바꿔서 실행하세요.

사용법 (레포 루트에서):
    python scripts/manual_test_n10.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 레포 루트를 import 경로에 추가 (레포 루트에서 실행한다고 가정)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_design.contracts import EvidenceStatus
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from src.rag_chatbot.graph.nodes.benefit_calculator import calculate_benefit_amount

# ── 입력값 (여기만 수정하세요) ──────────────────────────────────────
# data/processed/subsidy_documents.filtered.jsonl 안에 있는 실제 policy_id.
POLICY_ID = "subsidy:000000465790:2026-01-29"  # 유아학비(누리과정) 지원

# N9가 이미 "충족"으로 판정했다고 가정하는 값 (N10은 이 verdict 목록에서
# "충족"인 정책만 골라서 금액을 계산합니다).
ELIGIBILITY_VERDICTS = [
    {"policy_id": POLICY_ID, "verdict": "충족", "reasons": []},
]

# claim_plan은 원래 이전 노드가 넘겨주는 값인데, N10만 단독으로 테스트하는
# 거라 직접 채웁니다.
CLAIM_PLAN = [
    {
        "claim_id": f"{POLICY_ID}-amount",
        "policy_id": POLICY_ID,
        "claim_type": "amount",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["직접 입력한 테스트 근거"],
    },
]
# ── 여기까지 ────────────────────────────────────────────────────────

store = ChromaVectorStore(
    HashEmbeddingProvider(128),
    VectorStoreConfig(persist_directory=Path("data/vector_db"), collection_prefix="bokji_rag"),
)

state = {
    "query_id": "manual-test",
    "eligibility_verdicts": ELIGIBILITY_VERDICTS,
    "claim_plan": CLAIM_PLAN,
}

result = calculate_benefit_amount(state, store)
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
