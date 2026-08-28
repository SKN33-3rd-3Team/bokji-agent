# Persistent Vector Store

The runtime index uses Chroma with caller-supplied embeddings. It consumes the
existing `Chunk` contract and returns `RetrievedChunk`; it does not collect data,
call an LLM, or provide a UI.

## Legal metadata boundary

The team decided in a meeting to exclude statute, administrative-rule, and local-
ordinance body/article text from collection, storage, embedding, and indexing
because the legal corpus is too large. Legal chunks therefore contain list API
metadata only. Missing body text or article/paragraph/item locators is intentional
for `content_level=metadata_only`; it is not a parsing defect, handoff failure, or
merge blocker.

The law collection supports discovery claims about a record's name, subtype,
organization, document kind, issue/effective dates, and revision type. It does
not support article-level claims, eligibility or exclusion decisions, statutory
interpretation, or any assertion that requires full text. Callers must abstain
from those claims and direct the user to the official detail page.

`Document.content` is the exact LF-joined output of
`render_legal_metadata_summary(metadata)` in this fixed six-line order:

```text
법령명: {law_name}
법령유형: {document_kind}
소관기관: {organization}
제개정구분: {revision_type}
공포·발령일: {issued_date}
시행일: {effective_date}
```

The single `기본정보/basic_info` section must equal that content. Arbitrary body
or article text cannot be wrapped in this renderer shape or marked `basic_info`
to make it acceptable as metadata-only input.

The executable evidence capabilities are `legal_metadata`,
`legal_article_body`, and `legal_interpretation`.
`supported_legal_evidence_aspects(chunks)` exposes only `legal_metadata` for
validated metadata-only legal chunks. Requiring either other capability must
produce a `NO_EVIDENCE` abstention through `EvidenceState` and
`decide_abstention`.

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
- Incoming chunks are rejected before embedding if their content hash, deterministic
  ID, required source/chunk metadata, section-part coverage, or JSON contract is
  invalid. Source URLs must be canonical HTTPS URLs on approved official domains,
  with no credential query or configured API-key value. The CLI uses the same
  validation path and checks `DATA_GO_KR_API_KEY` and `OPENLAW_API_KEY` without
  storing them. Legal chunks must carry `content_level=metadata_only`,
  `law_type`, `law_name`, `source_sequence`, `organization`, `document_kind`,
  `issued_date`, `effective_date`, and `revision_type`. Their URL must match the
  subtype-specific public detail URL derived from `source_sequence`; only statutes
  also include the effective date in the URL.
- Legal `source_id` is the stable numeric entity ID. Numeric-string
  `source_sequence` identifies the official source revision/version and is part
  of both the legal document ID and direct URL; neither value substitutes for
  the other.
- Collection metadata binds the contract schema, storage layout, distance type,
  embedding provider and dimension, and chunking version into fingerprints. A
  reopen with incompatible settings or an unexpected fingerprint is rejected.
- Chunks are stored as validated JSON. The implementation never loads pickle or
  another executable index serialization.

Search supports source isolation, snapshot and scalar metadata equality, a
half-open effective-date filter, exact subsidy `region_names` intersection, and
top-k conversion to ranked `RetrievedChunk` values. A national
`region_scope` with `["전국"]` is a wildcard. An unknown scope with `[]`
fails closed only when a region filter is present. Date and region checks are
reapplied to decoded chunks so missing or malformed metadata fails closed.

The name-based region contract and subsidy region text prefix require
`structure-v2` chunks and `chroma-vector-store-v3`. Existing code-based or
v1 subsidy indexes must be rebuilt; schema version remains `1.0` because this is
a pre-freeze correction made before a shared index was established. Callers must
pass canonical names. Alias normalization and ambiguous bare names such as
`중구` are deliberately left to an upstream resolver with an authoritative
registry.

The legal metadata profile is `legal-metadata-v1`. It is included only in law
collection fingerprints, so every prior full-text/article-based law registry,
chunk snapshot, and Vector DB generation is incompatible and must be rebuilt
from metadata-only documents. The storage version remains
`chroma-vector-store-v3`, the layout remains
`source-separated-atomic-generations-v1`, and subsidy collection fingerprints
remain compatible.

Legal public citations are direct, credential-free official detail URLs:

- `law`: `https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=<source_sequence>&efYd=<effective_date: YYYYMMDD>`
- `admrul`: `https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=<source_sequence>`
- `ordin`: `https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=<source_sequence>`

Contract values `issued_date`, `effective_date`, and `effective_from` use
canonical `YYYY-MM-DD`. Only the statute URL query `efYd` uses `YYYYMMDD`,
derived by removing hyphens after validating the ISO date. Administrative-rule
and ordinance direct URLs do not add a date query.

These links identify the list record and provide a route to official full text;
they do not make the indexed metadata an article citation. Full generated JSONL,
raw API responses, body text, embeddings, and runtime Vector DB files are not
tracked in Git. Only public representative samples, manifests, and Document Cards
belong in the repository.

## CLI

Global storage/embedding options come before the command:

```powershell
python -m rag_design.vector_cli --embedding hash index `
  --source subsidy --snapshot-id snapshot-001 --chunks chunks.jsonl

python -m rag_design.vector_cli --embedding hash search `
  --source subsidy --query-id q-001 --query "유아학비 지원" --top-k 5 `
  --region-name "서울특별시" --metadata organization='"교육부"'
```

Use `--embedding korean --model-name intfloat/multilingual-e5-base` for the real
provider. Add `--local-files-only` when runtime downloads are prohibited. The
`fingerprint` command prints the active collection fingerprint, and
`delete-snapshot` deletes one stored snapshot ID.
