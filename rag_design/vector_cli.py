"""Minimal CLI for persistent vector indexing and retrieval."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from .contracts import Chunk, SourceType
from .embeddings import (
    EmbeddingProviderError,
    HashEmbeddingProvider,
    SentenceTransformerKoreanProvider,
)
from .vector_store import (
    ChromaVectorStore,
    VectorSearchFilter,
    VectorStoreConfig,
    VectorStoreError,
)


def _provider(args: argparse.Namespace):
    if args.embedding == "hash":
        return HashEmbeddingProvider(args.dimension or 128)
    return SentenceTransformerKoreanProvider(
        args.model_name,
        dimension=args.dimension or 768,
        device=args.device,
        local_files_only=args.local_files_only,
    )


def _store(args: argparse.Namespace) -> ChromaVectorStore:
    return ChromaVectorStore(
        _provider(args),
        VectorStoreConfig(
            persist_directory=Path(args.persist_directory),
            collection_prefix=args.collection_prefix,
        ),
    )


def _load_chunks(path: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            chunks.append(Chunk.from_dict(json.loads(line)))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid chunk JSONL at line {line_number}") from exc
    return chunks


def _metadata_filters(values: Sequence[str]) -> dict[str, str | int | float | bool]:
    """Parse key=value filters while preserving supported JSON scalar types."""

    result: dict[str, str | int | float | bool] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("metadata filters must use key=value")
        key, raw = value.split("=", 1)
        if not key.strip():
            raise ValueError("metadata filter key must be non-empty")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        if not isinstance(parsed, (str, int, float, bool)):
            raise ValueError("metadata filter value must be scalar")
        result[key] = parsed
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist-directory", default=".runtime/vector_db")
    parser.add_argument("--collection-prefix", default="bokji_rag")
    parser.add_argument("--embedding", choices=("hash", "korean"), default="hash")
    parser.add_argument("--dimension", type=int)
    parser.add_argument("--model-name", default="intfloat/multilingual-e5-base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--local-files-only", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index")
    index.add_argument("--source", choices=("subsidy", "law"), required=True)
    index.add_argument("--snapshot-id", required=True)
    index.add_argument("--chunks", type=Path, required=True)
    index.add_argument("--chunking-version")

    search = subparsers.add_parser("search")
    search.add_argument("--source", choices=("subsidy", "law"), required=True)
    search.add_argument("--query-id", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--as-of", type=date.fromisoformat)
    search.add_argument(
        "--region-name",
        action="append",
        default=[],
        help="canonical region name; repeat for exact-name OR matching",
    )
    search.add_argument("--metadata", action="append", default=[])
    search.add_argument("--snapshot-id")
    search.add_argument("--expected-fingerprint")

    delete = subparsers.add_parser("delete-snapshot")
    delete.add_argument("--source", choices=("subsidy", "law"), required=True)
    delete.add_argument("--snapshot-id", required=True)

    fingerprint = subparsers.add_parser("fingerprint")
    fingerprint.add_argument("--source", choices=("subsidy", "law"), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        store = _store(args)
        source_type = SourceType(args.source)
        if args.command == "index":
            result = store.sync_snapshot(
                source_type,
                _load_chunks(args.chunks),
                snapshot_id=args.snapshot_id,
                chunking_version=args.chunking_version,
                secret_values=tuple(
                    value
                    for value in (
                        os.environ.get("DATA_GO_KR_API_KEY"),
                        os.environ.get("OPENLAW_API_KEY"),
                    )
                    if value
                ),
            )
            payload = asdict(result)
            payload["source_type"] = result.source_type.value
        elif args.command == "search":
            search_filter = VectorSearchFilter(
                as_of=args.as_of,
                region_names=tuple(args.region_name),
                metadata_equals=_metadata_filters(args.metadata),
                snapshot_id=args.snapshot_id,
            )
            payload = [
                item.to_dict()
                for item in store.search(
                    source_type,
                    args.query,
                    query_id=args.query_id,
                    top_k=args.top_k,
                    search_filter=search_filter,
                    expected_collection_fingerprint=args.expected_fingerprint,
                )
            ]
        elif args.command == "delete-snapshot":
            result = store.delete_snapshot(source_type, args.snapshot_id)
            payload = asdict(result)
            payload["source_type"] = result.source_type.value
        else:
            payload = {
                "source_type": source_type.value,
                "collection_fingerprint": store.collection_fingerprint(source_type),
            }
    except (EmbeddingProviderError, VectorStoreError, ValueError, OSError) as exc:
        parser.error(str(exc))
    # Force UTF-8 on stdout: on Windows, print() otherwise encodes with the
    # console codepage (e.g. cp949), corrupting Korean chunk text for anyone
    # or anything reading the output as UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
