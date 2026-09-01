"""Chroma 벡터스토어 준비 + 샘플 문서 색인.

``get_store`` 는 ``st.cache_resource`` 로 캐시되므로 세션·rerun 을 넘어
한 번만 실행된다. 활성 스냅샷이 이미 있으면 색인을 건너뛴다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from rag_design.chunking import chunk_document
from rag_design.contracts import Document, SourceType
from rag_design.embeddings import (
    HashEmbeddingProvider,
    SentenceTransformerKoreanProvider,
)
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorStoreConfig,
)

from .constants import LAW_SAMPLE, RUNTIME_DIR, SUBSIDY_SAMPLE


def _make_provider(embedding_choice: str):
    if embedding_choice == "korean":
        # 최초 1회 모델 다운로드가 필요할 수 있다.
        return SentenceTransformerKoreanProvider("intfloat/multilingual-e5-base")
    return HashEmbeddingProvider(128)


def _load_documents(path: Path) -> list[Document]:
    docs: list[Document] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            docs.append(Document.from_dict(json.loads(line)))
    return docs


def _ingest_samples(store: ChromaVectorStore) -> dict[str, Any]:
    """샘플 문서를 청킹해 스냅샷으로 색인한다. 소스별로 독립 처리한다."""

    report: dict[str, Any] = {"subsidy": None, "law": None}
    for src_type, path, key in (
        (SourceType.SUBSIDY, SUBSIDY_SAMPLE, "subsidy"),
        (SourceType.LAW, LAW_SAMPLE, "law"),
    ):
        try:
            documents = _load_documents(path)
            chunks: list = []
            for document in documents:
                chunks.extend(chunk_document(document))
            store.sync_snapshot(src_type, chunks, snapshot_id=f"samples-{key}-v1")
            report[key] = {"documents": len(documents), "chunks": len(chunks)}
        except Exception as exc:  # noqa: BLE001 - 소스 하나 실패가 전체를 막지 않게
            report[key] = {"error": f"{type(exc).__name__}: {exc}"}
    return report


@st.cache_resource(show_spinner="Vector store를 준비하는 중…")
def get_store(embedding_choice: str) -> tuple[ChromaVectorStore, dict]:
    persist_dir = RUNTIME_DIR / embedding_choice

    provider = _make_provider(embedding_choice)
    store = ChromaVectorStore(
        provider,
        VectorStoreConfig(
            persist_directory=persist_dir,
            collection_prefix=f"bokji_{embedding_choice}",
        ),
    )

    # 활성 스냅샷이 있으면 색인을 건너뛴다.
    try:
        store.search(SourceType.SUBSIDY, "warmup", query_id="warmup", top_k=1)
        return store, {"ingested": False}
    except CollectionNotFoundError:
        pass

    report = _ingest_samples(store)
    return store, {"ingested": True, "report": report}
