"""vectorDB를 한국어 의미 임베딩으로 재색인한다.

왜 필요한가
-----------
현재 ``data/vector_db``는 ``HashEmbeddingProvider``(local-hash-v1:128)로
색인돼 있다. 이 provider는 자기 docstring이 밝히듯 "테스트와 오프라인 스모크
체크 전용"으로, 문자 n-gram 해시일 뿐 **의미를 담지 않는다**. 그래서
"혼자 사는데 월세가 부담돼요"로 검색하면 유기질비료·입양축하금 같은 무관한
정책이 올라온다(2026-08-31 실측). 검색 품질 문제의 근본 원인이다.

이 스크립트는 ``SentenceTransformerKoreanProvider``
(intfloat/multilingual-e5-base, 768차원)로 전체를 다시 색인한다.

주의사항
--------
- **첫 실행 때 임베딩 모델을 내려받는다(약 1GB).** 인터넷이 필요하다.
- **오래 걸린다.** subsidy 10,963건(약 4.5만 청크) + law 1,466건을 CPU로
  임베딩하면 수십 분이 걸릴 수 있다. GPU가 있으면 ``--device cuda``.
- 기존 해시 색인은 **지우지 않는다.** 임베딩 provider가 다르면 컬렉션이
  따로 생기므로, 문제가 생기면 ``.env``의 ``EMBEDDING_PROVIDER``만 되돌리면
  원래대로 돌아간다. 대신 디스크를 그만큼 더 쓴다.
- 색인과 검색은 **반드시 같은 provider**여야 한다. 다르면
  ChromaVectorStore가 fingerprint로 막아준다(조용히 틀리지는 않는다).

실행 (레포 루트에서, PowerShell 기준)
------------------------------------
    pip install sentence-transformers
    $env:PYTHONPATH = ".;src"
    python scripts/reindex_korean.py

    # 오래 걸리니 먼저 law(1,466건)만 돌려서 확인해보고 싶다면
    python scripts/reindex_korean.py --only law

    # 색인 후 검색이 실제로 좋아졌는지만 다시 보고 싶다면
    python scripts/reindex_korean.py --smoke-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rag_design.chunking import ChunkingConfig, chunk_document
from rag_design.contracts import Document, SourceType
from rag_design.embeddings import (
    EmbeddingProviderError,
    SentenceTransformerKoreanProvider,
)
from rag_design.vector_store import (
    ChromaVectorStore,
    VectorSearchFilter,
    VectorStoreConfig,
)

_VECTOR_DB = _REPO_ROOT / "data" / "vector_db"
_COLLECTION_PREFIX = "bokji_rag"
_MODEL_NAME = "intfloat/multilingual-e5-base"
_DIMENSION = 768

# (source_type, 원천 문서 파일). data/processed에 수집기가 만들어 둔 것.
_SOURCES = {
    "subsidy": (SourceType.SUBSIDY, _REPO_ROOT / "data" / "processed" / "subsidy_documents.jsonl"),
    "law": (SourceType.LAW, _REPO_ROOT / "data" / "processed" / "law_documents.jsonl"),
}

# 색인 후 "의미 검색이 실제로 되는지" 눈으로 확인하는 질의.
# 해시 임베딩에서는 전부 무관한 정책이 나왔던 것들이다.
_SMOKE_QUERIES = (
    "혼자 사는데 월세가 부담돼요",
    "아이 키우는데 받을 수 있는 지원",
    "일자리를 찾고 있는 청년입니다",
)


def _secret_values() -> tuple[str, ...]:
    """색인 텍스트에 섞여 들어가면 안 되는 값(API 키). vector_cli와 동일."""

    import os

    return tuple(
        value
        for value in (
            os.environ.get("DATA_GO_KR_API_KEY"),
            os.environ.get("OPENLAW_API_KEY"),
        )
        if value
    )


def _build_provider(device: str) -> SentenceTransformerKoreanProvider:
    return SentenceTransformerKoreanProvider(
        _MODEL_NAME, dimension=_DIMENSION, device=device
    )


def _open_store(device: str) -> ChromaVectorStore:
    return ChromaVectorStore(
        _build_provider(device),
        VectorStoreConfig(
            persist_directory=_VECTOR_DB, collection_prefix=_COLLECTION_PREFIX
        ),
    )


def _load_documents(path: Path) -> list[Document]:
    documents: list[Document] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            documents.append(Document.from_dict(json.loads(line)))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path.name} {line_number}번째 줄을 읽지 못함") from exc
    return documents


def _check_model_available(device: str) -> bool:
    """모델을 실제로 한 번 불러본다.

    45,413개를 임베딩하기 시작한 뒤에 모델이 없다는 걸 알게 되면 시간만
    버린다. 짧은 문장 하나로 먼저 확인한다(첫 실행이면 여기서 다운로드가
    일어난다).
    """

    print(f"[1/3] 임베딩 모델 확인 - {_MODEL_NAME} (device={device})")
    print("      첫 실행이면 약 1GB를 내려받습니다. 시간이 걸릴 수 있어요...")
    started = time.monotonic()
    try:
        vector = _build_provider(device).embed_documents(["연결 확인용 문장"])[0]
    except EmbeddingProviderError as exc:
        print(f"      [실패] {exc}")
        print("      -> pip install sentence-transformers 로 설치했는지 확인하세요.")
        return False
    except Exception as exc:  # noqa: BLE001 - 원인을 그대로 보여주는 게 목적
        print(f"      [실패] 모델을 불러오지 못했습니다: {type(exc).__name__}: {exc}")
        print("      -> 인터넷 연결(모델 다운로드)과 디스크 여유를 확인하세요.")
        return False

    elapsed = time.monotonic() - started
    print(f"      [OK] {len(vector)}차원 벡터 생성 확인 ({elapsed:.1f}초)")
    return True


def _reindex(name: str, snapshot_id: str, device: str, chunk_config: ChunkingConfig) -> bool:
    source_type, documents_path = _SOURCES[name]
    if not documents_path.exists():
        print(f"      [건너뜀] {documents_path}가 없습니다.")
        return False

    print(f"\n      [{name}] 원천 문서 읽는 중: {documents_path.name}")
    documents = _load_documents(documents_path)
    print(f"      [{name}] 문서 {len(documents):,}건 -> 청크 만드는 중...")

    chunks: list = []
    for document in documents:
        # source_type이 섞인 파일을 여기서 걸러낸다. sync_snapshot도 검사하지만
        # 어느 문서가 문제인지는 알려주지 않는다(vector_cli와 같은 방식).
        if document.source_type is not source_type:
            print(
                f"      [실패] {document.doc_id}의 source_type이 "
                f"{document.source_type.value}입니다 (기대: {source_type.value})."
            )
            return False
        chunks.extend(chunk_document(document, chunk_config))
    print(f"      [{name}] 청크 {len(chunks):,}개. 이제 임베딩+색인을 시작합니다.")
    print(f"      [{name}] 이 단계가 가장 오래 걸립니다 (CPU면 수십 분).")

    started = time.monotonic()
    store = _open_store(device)
    result = store.sync_snapshot(
        source_type,
        tuple(chunks),
        snapshot_id=snapshot_id,
        secret_values=_secret_values(),
    )
    elapsed = time.monotonic() - started

    print(
        f"      [{name}] 완료 ({elapsed / 60:.1f}분) - "
        f"색인 {result.total_count:,}건 / 컬렉션 {result.collection_name}"
    )
    return True


def _smoke_search(device: str) -> None:
    """의미 검색이 실제로 되는지 눈으로 확인한다."""

    print("\n[3/3] 검색 스모크 테스트 - 질문과 결과가 말이 되는지 봐주세요")
    store = _open_store(device)
    for query in _SMOKE_QUERIES:
        print(f"\n  질문: {query!r}")
        try:
            hits = store.search(
                SourceType.SUBSIDY,
                query,
                query_id=f"smoke-{abs(hash(query)) % 10000}",
                top_k=5,
                search_filter=VectorSearchFilter(),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    [검색 실패] {type(exc).__name__}: {exc}")
            continue
        if not hits:
            print("    (결과 없음)")
            continue
        for rank, hit in enumerate(hits, 1):
            title = hit.chunk.text.split("\n", 1)[0].strip()
            print(f"    {rank}. {title[:60]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="한국어 의미 임베딩으로 재색인")
    parser.add_argument(
        "--only",
        choices=sorted(_SOURCES),
        help="한쪽만 재색인. 생략하면 subsidy와 law 모두. law(1,466건)가 훨씬 빨라서 먼저 시험해보기 좋다.",
    )
    parser.add_argument(
        "--device", default="cpu", help="임베딩 장치. GPU가 있으면 cuda (기본 cpu)."
    )
    parser.add_argument(
        "--snapshot-id",
        default=f"korean-{date.today().isoformat()}",
        help="이번 색인의 스냅샷 ID (기본: korean-오늘날짜).",
    )
    parser.add_argument("--max-chars", type=int, default=800)
    parser.add_argument("--overlap-chars", type=int, default=100)
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="재색인 없이 검색 스모크 테스트만 실행(이미 재색인을 끝낸 뒤 확인용).",
    )
    args = parser.parse_args()

    print("=" * 68)
    print("한국어 의미 임베딩으로 vectorDB 재색인")
    print("=" * 68)

    if not _VECTOR_DB.exists():
        print(f"[실패] {_VECTOR_DB}가 없습니다. 레포 루트에서 실행했는지 확인하세요.")
        return 1

    if not _check_model_available(args.device):
        return 1

    if args.smoke_only:
        _smoke_search(args.device)
        return 0

    targets = [args.only] if args.only else ["law", "subsidy"]  # 빠른 쪽부터
    print(f"\n[2/3] 재색인 대상: {', '.join(targets)} / snapshot_id={args.snapshot_id}")
    chunk_config = ChunkingConfig(args.max_chars, args.overlap_chars)

    for name in targets:
        if not _reindex(name, args.snapshot_id, args.device, chunk_config):
            return 1

    _smoke_search(args.device)

    print("\n" + "=" * 68)
    print("재색인 완료. 마지막으로 .env에 아래 한 줄을 넣어야 챗봇이 이 색인을 씁니다:")
    print()
    print("    EMBEDDING_PROVIDER=korean")
    print()
    print("넣지 않으면 예전 해시 색인을 계속 씁니다(되돌리고 싶을 때도 이 값만 지우면 됩니다).")
    print("그다음 확인:  python scripts/manual_test_service.py")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
