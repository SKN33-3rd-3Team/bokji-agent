# Persistent Vector Store

The runtime index uses Chroma with caller-supplied embeddings. It consumes the
existing `Chunk` contract and returns `RetrievedChunk`; it does not collect data,
call an LLM, or provide a UI.

## Install

```powershell
python -m pip install -r requirements-vector.txt
```

`HashEmbeddingProvider` is deterministic and intended only for tests and offline
smoke checks. For a production Korean/multilingual connection point, install
`requirements-embedding.txt` and use `SentenceTransformerKoreanProvider`. The
model is loaded lazily with `trust_remote_code=False`; missing packages, missing
local model files, load failures, and dimension mismatches raise an explicit
`EmbeddingProviderError`.

## Storage contract

- The default persistent path is `.runtime/vector_db/`, which is ignored by Git.
- Subsidy and law chunks use separate logical collections. A mixed-source batch
  is rejected, and every decoded result is checked against its collection source.
- `sync_snapshot` treats its chunk list as the complete snapshot for one source.
  It writes and verifies a separate immutable generation first, then atomically
  changes a small registry pointer. Search therefore observes either the previous
  complete generation or the new complete generation, never a partially written
  batch. Repeating identical input performs no embedding/upsert.
- `delete_snapshot` atomically promotes an empty generation for the matching
  active snapshot. Prior generations are not query-visible; retaining immutable
  generations also keeps searches that began before a promotion safe.
- Incoming chunks are rejected before embedding if their body hash, deterministic
  ID, required source/chunk metadata, section-part coverage, or JSON contract is
  invalid. Source URLs must be canonical HTTPS URLs on approved official domains,
  with no credential query or configured API-key value. The CLI uses the same
  validation path and checks `DATA_GO_KR_API_KEY` and `OPENLAW_API_KEY` without
  storing them. Law URLs must also match the chunk's `lsi_seq` and effective date.
- Collection metadata binds the contract schema, storage layout, distance type,
  embedding provider and dimension, and chunking version into fingerprints. A
  reopen with incompatible settings or an unexpected fingerprint is rejected.
- Chunks are stored as validated JSON. The implementation never loads pickle or
  another executable index serialization.

Search supports source isolation, snapshot and scalar metadata equality, a
half-open effective-date filter, subsidy region matching (including `ALL`), and
top-k conversion to ranked `RetrievedChunk` values. Date and region checks are
reapplied to decoded chunks so missing or malformed law dates fail closed.

## CLI

Global storage/embedding options come before the command:

```powershell
python -m rag_design.vector_cli --embedding hash index `
  --source subsidy --snapshot-id snapshot-001 --chunks chunks.jsonl

python -m rag_design.vector_cli --embedding hash search `
  --source subsidy --query-id q-001 --query "유아학비 지원" --top-k 5 `
  --region 1100000000 --metadata organization='"교육부"'
```

Use `--embedding korean --model-name intfloat/multilingual-e5-base` for the real
provider. Add `--local-files-only` when runtime downloads are prohibited. The
`fingerprint` command prints the active collection fingerprint, and
`delete-snapshot` deletes one stored snapshot ID.
