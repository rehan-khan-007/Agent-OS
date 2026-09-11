# 2. Retrieval, Data & Storage

## 2.1 Corpus
* **132 real documents / 7,748 chunks / 4 domains:** quantum control
  (arXiv), entrepreneurship (arXiv), thermal engineering (arXiv),
  personal finance (real SEBI investor-education PDFs). Not
  synthetic.
* Real fetch scripts: `scripts/fetch_arxiv_papers.py` (17 queries
  across domains), `scripts/fetch_sebi_papers.py` (32 PDFs).
* **Real production incident during ingestion:** a NUL byte
  (`0x00`) in one real PDF crashed the entire batch — Postgres text
  columns reject NUL bytes. Fixed: `text.replace("\x00", "")`
  sanitization + per-document `try/except` isolation so one bad
  document no longer takes down the whole run.

## 2.2 Ingestion Pipeline
```text
File upload (.pdf/.txt/.md)
   |
R2 object storage (or local /tmp fallback if R2 unconfigured)
   |
Background worker downloads from R2 to a FRESH local temp file
   |
Text extraction (app/retrieval/ingestion.py) + NUL-byte sanitization
   |
Chunking (chunk_size=1000, overlap=100) — fixed-size, NOT
semantic/sentence-aware. Chunk-boundary mid-sentence splits: NOT
VERIFIED as a real cause of retrieval misses.
   |
Batched embedding (up to 100 texts/request — real fix, replaced
one-at-a-time calls; makes large-corpus ingestion practical)
   |
Stored: DocumentChunk{id, source, chunk_index, text, embedding}
   |
Checkpointed per-chunk (Redis) — ONLY after each chunk's DB commit
confirms success (see 2.4, Invariant #1)
   |
R2 object deleted (only after FULL success — the retry path
correctly re-downloads from R2 if a job is reclaimed)
```
* **Supported formats:** `.txt`, `.md`, `.pdf` (enforced at API layer).
* **Embedding model:** OpenRouter-backed. Exact model string: NOT
  RE-VERIFIED in the last pass — confirm against live
  `app/retrieval/embeddings.py` (144 lines) before citing.

## 2.3 Retrieval — Two Code Paths Exist, Only One Is Live
* **`app/retrieval/hybrid.py`** (88 lines) — THE REAL, LIVE PATH.
  BM25 (`bm25_search.py`, 82 lines) + dense vector search
  (`_vector_search_ranked`), fused via Reciprocal Rank Fusion. This
  is what `retrieve` calls and what every real benchmark measures.
* **`app/retrieval/pipeline.py`** (44 lines) — DEAD CODE. Dense-only,
  older function (`retrieve_relevant_chunks`). **Verified: zero
  other files import from it.** Do not resurrect without confirming
  intent first.
* **Fusion:** RRF, NOT a learned/trained reranker. No dedicated
  cross-encoder reranker exists — named gap. Recommended future
  addition (avoids new paid API): `cross-encoder/ms-marco-MiniLM-L-6-v2`,
  CPU-runnable, free.
* **Top-k:** 3, consistent across the tool and all benchmarks.
* **No confidence/relevance gate.** pgvector always returns k-nearest
  neighbors regardless of true relevance — no similarity-cutoff or
  "no relevant result" signal. Directly demonstrated by the eval
  dataset's negative control.

## 2.4 Real Retrieval Numbers (full methodology in BENCHMARKS.md)
* Recall@3 (hybrid, production pipeline): **94.3%** (33/35), 132
  docs / 7,748 chunks / 4 domains.
* Ablation: BM25-only 91.4% / dense-only 94.3% / hybrid 94.3% —
  **hybrid TIED dense, did not clearly win.** Real justification for
  keeping hybrid: BM25 and dense demonstrably miss DIFFERENT
  questions (complementary failure pattern), not "hybrid is proven
  better."
* LLM-judge grounding: **35/35 (100%)** — independently confirms the
  vocabulary-overlap heuristic, same model family as generation (a
  named, general LLM-as-judge limitation).

## 2.5 Database (PostgreSQL + pgvector, Neon)
* **Extension:** `vector` (pgvector), created in initial Alembic
  migration.
* **Real tables:**
  * `document_chunks` — `id, source, chunk_index, text,
    embedding(vector(1536))`. HNSW index
    (`document_chunks_embedding_hnsw_idx`, `vector_cosine_ops`). **No
    FK to a documents table** — `source` is a bare filename string,
    no real join possible for "all chunks for doc X." **No
    content-hash column** — re-ingesting the same filename creates
    duplicate rows, not a detected re-ingestion.
  * `conversation_messages` — `id, session_id (indexed), role,
    content, tool_calls, tool_call_id, created_at`. No
    `user_id`/`tenant_id`.
  * `chat_sessions` — **CONFIRMED DOES NOT EXIST.** Session tokens
    are entirely Redis-backed (`app/auth/session_tokens.py`), no
    Postgres component. (An earlier version of this handoff
    incorrectly listed this table — corrected here.)
* **Migrations:** Alembic, baselined against production
  (`alembic stamp head` confirmed at revision `0001`). Only ONE
  migration exists so far — no schema changes since baselining.
* **Connection management:** async SQLAlchemy engine/session, one
  shared engine per process.

## 2.6 Object Storage (Cloudflare R2)
* **Chosen over AWS S3** — real, researched reason: S3's free tier
  is time-limited to 12 months + real egress fees; R2's free tier is
  **permanent, zero egress fees** (10GB storage, 1M writes, 10M
  reads/month, verified via real research before choosing).
* **Key structure:** `uploads/{uuid}.{ext}` — flat, no per-tenant
  prefixing.
* **Local-path fallback is INTENTIONAL, not accidental** — gated on
  `r2_client.is_configured()`. Dev-safe, NOT multi-process-safe: the
  original bug this closed was uploads only working because the API
  server and worker share one process. **Invariant: R2 credentials
  must stay configured in any environment running >1 worker
  process.**
* **No hard spending cap on Cloudflare** (confirmed via real
  research — informational budget alert only). Real, accepted
  residual risk given naturally bounded usage (rate-limited uploads,
  one download/delete per ingestion job).
* Failed ingestion jobs' R2 objects: retry path correctly
  re-downloads (by design) — likely intentional they aren't
  pre-emptively cleaned up, NOT explicitly confirmed.

## 2.7 Real Invariants (Data/Retrieval)
1. **BM25 must never load the `embedding` column** —
   `.options(defer(DocumentChunk.embedding))` in `bm25_search()`.
   Removing this reintroduces a REAL PAST PRODUCTION INCIDENT: Neon's
   5GB/month transfer quota exhausted in ~4 days (7,748 rows ×
   ~22KB/row, embedding was the unused majority of that size).
   Guarded by `test_bm25_data_transfer.py` — do not remove without
   updating that test's assertion.
2. **Checkpoint writes only after confirmed DB commit**, never
   before/concurrent. Real fix for a real crash-consistency bug:
   `mark_chunk_done()` used to run inside the same loop as
   `session.add()`, before the single `session.commit()` — a failed
   commit meant Redis permanently believed chunks were stored when
   Postgres held none. Guarded by a dedicated test that simulates a
   failing commit.
3. **R2 credentials required for multi-process deployments** (2.6).
