"""N9(자격 판정 노드)를 직접 돌려보는 수동 테스트 스크립트. pytest가 아니라
그냥 실행 파일이라, 값 바꿔가며 눈으로 결과를 확인하는 용도.

사용법 (레포 루트에서):
    python scripts/manual_test_n9.py

"입력값" 구역만 원하는 값으로 바꾸면 됩니다.

INJECT_AGE_CONDITION 옵션:
지금 실제로 색인된 vectorDB(data/vector_db)에는 아직 나이 조건 같은 구조화
metadata(age_start/age_end)가 없어서, 그대로 쓰면 "미충족"이 절대 안 나오고
항상 "충족"만 나옵니다. INJECT_AGE_CONDITION = True로 켜면, 실제 정책
문서(제목/본문은 진짜)를 가져와서 나이 조건 metadata만 테스트용으로 주입한
뒤 임시 vectorDB에 색인해서 씁니다 (원본 data/vector_db는 건드리지 않음).
이렇게 하면 "미충족"이 실제로 뜨는 것도 확인할 수 있습니다.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

# 레포 루트를 import 경로에 추가 (레포 루트에서 실행한다고 가정)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, EvidenceStatus, SourceType
from rag_design.embeddings import HashEmbeddingProvider
from rag_design.vector_store import ChromaVectorStore, VectorStoreConfig

from src.rag_chatbot.graph.nodes import determine_eligibility

# ── 입력값 (여기만 수정하세요) ──────────────────────────────────────
# data/processed/subsidy_documents.filtered.jsonl 안에 있는 실제 policy_id.
# 예시 몇 개:
#   subsidy:000000465790:2026-01-29        (유아학비(누리과정) 지원)
#   subsidy:105100000001:2026-04-30T09:39:00+09:00  (근로·자녀장려금)
POLICY_ID = "subsidy:000000465790:2026-01-29"

# 사용자 정보 (state.slots에 해당). 실제로 챗봇이 대화에서 받아올 값.
USER_SLOTS = {"age": 4}

# claim_plan은 원래 이전 노드(N7/N8)가 만들어 넘겨주는 값인데, 여기서는
# N9만 단독으로 테스트하는 거라 직접 채웁니다. status는 "이전 노드가 이
# 근거로 이 정책을 얼마나 확신했는지"를 나타냅니다.
CLAIM_PLAN = [
    {
        "claim_id": f"{POLICY_ID}-eligibility",
        "policy_id": POLICY_ID,
        "claim_type": "eligibility",
        "doc_check_required": True,
        "law_check_required": False,
        "evidence_chunk_ids": [],
        "status": EvidenceStatus.SUPPORTED,
        "reasons": ["직접 입력한 테스트 근거"],
    },
]

# True로 켜면 아래 AGE_START/AGE_END를 실제 정책 문서 metadata에 테스트용
# 으로 주입한 임시 vectorDB를 만들어서 씁니다 (원본 data/vector_db는 안 건드림).
# 이 조건과 USER_SLOTS["age"]를 서로 안 맞게 넣으면 "미충족"을 볼 수 있습니다.
INJECT_AGE_CONDITION = False
AGE_START = 65  # 예: 65세부터 지원
AGE_END = None  # 예: 상한 없음
# ── 여기까지 ────────────────────────────────────────────────────────


def _build_real_store_with_injected_age(policy_id: str, age_start, age_end) -> ChromaVectorStore:
    """실제 정책 원문(제목/본문)은 그대로 쓰되, 나이 조건 metadata만 테스트용
    으로 주입해 임시 디렉터리에 색인한 store를 만든다. 원본 data/vector_db는
    건드리지 않는다.
    """
    documents_path = Path("data/processed/subsidy_documents.filtered.jsonl")
    document = None
    with documents_path.open(encoding="utf-8") as f:
        for line in f:
            candidate = Document.from_dict(json.loads(line))
            if candidate.doc_id == policy_id:
                document = candidate
                break
    if document is None:
        raise SystemExit(f"policy_id를 못 찾음: {policy_id} ({documents_path} 안에 있는지 확인)")

    chunks = chunk_document(document)
    injected_chunks = tuple(
        replace(chunk, metadata={**chunk.metadata, "age_start": age_start, "age_end": age_end})
        for chunk in chunks
    )

    temporary_directory = tempfile.mkdtemp(prefix="n9_manual_test_")
    store = ChromaVectorStore(
        HashEmbeddingProvider(128),
        VectorStoreConfig(persist_directory=Path(temporary_directory), collection_prefix="manual_test"),
    )
    store.sync_snapshot(SourceType.SUBSIDY, injected_chunks, snapshot_id="manual-test-snap")
    return store


if INJECT_AGE_CONDITION:
    print(
        f"[안내] {POLICY_ID} 문서에 age_start={AGE_START}, age_end={AGE_END} 조건을 "
        "테스트용으로 주입한 임시 vectorDB를 사용합니다 (data/vector_db는 안 건드림).\n"
    )
    store = _build_real_store_with_injected_age(POLICY_ID, AGE_START, AGE_END)
else:
    store = ChromaVectorStore(
        HashEmbeddingProvider(128),
        VectorStoreConfig(persist_directory=Path("data/vector_db"), collection_prefix="bokji_rag"),
    )

state = {
    "query_id": "manual-test",
    "slots": USER_SLOTS,
    "claim_plan": CLAIM_PLAN,
}

result = determine_eligibility(state, store)
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
