# AgentOS Engineering Handoff — Part 2: System Components

---

## 4. API Layer (System Components)

**Framework:** FastAPI, running behind Uvicorn on Render.

**Real endpoints, verified against `backend/app/api/`:**

Endpoint: POST /agents/chat
Method: POST
Purpose: Non-streaming chat turn
Input: AgentRequest {message, session_id?, session_token?}
Output: AgentResponse {response, session_id, session_token, messages, cached}
Authentication: Session-ownership token (see Section 13)
Side effects: Writes to conversation_messages, chat_sessions; may trigger tool calls with real external side effects (web search, retrieval)
Dependencies: Redis (cache, rate limit, session tokens), Postgres (memory), OpenRouter (LLM)
Failure modes: 403 on invalid/missing session_token for an existing session; 503 implicitly if Redis/Postgres unreachable (no explicit catch beyond what SQLAlchemy/redis-py raise)
Known limitations: No explicit request size limit on `message` itself (file uploads have a limit, chat messages do not)

Endpoint: POST /agents/chat/stream
Method: POST
Purpose: Streaming chat turn via SSE
Input: Same as /agents/chat
Output: SSE stream: session event (session_id, session_token), tool_call events, chunk events, done event
Authentication: Same as /agents/chat
Side effects: Same as /agents/chat
Dependencies: Same as /agents/chat
Failure modes: Same as /agents/chat; a client disconnecting mid-stream is not explicitly handled (no evidence of cleanup on disconnect)
Known limitations: The word-by-word streaming (`chat_stream`'s `event_stream()`) is simulated after the full response is already generated — it is not true token-by-token streaming from the LLM provider; the full response is generated first, then re-split and paced client-side

Endpoint: POST /documents/upload
Method: POST
Purpose: Upload a document (.txt/.md/.pdf) for ingestion into the retrieval corpus
Input: multipart/form-data file
Output: UploadResponse {upload_id, filename, status}
Authentication: None beyond rate limiting — no session or ownership check on uploads
Side effects: Writes to R2 (or local /tmp fallback), enqueues a real background job
Dependencies: R2 (or local fallback), Redis (queue), rate limiter
Failure modes: 400 on wrong extension/empty/oversized file; 503 if queue unavailable
Known limitations: No authentication on who can upload — anyone can add documents to the shared corpus (see Section 14, threat model)

Endpoint: GET /documents/upload/{upload_id}/status
Method: GET
Purpose: Poll ingestion job status
Input: upload_id (path param)
Output: StatusResponse {upload_id, filename, status, chunks_processed, chunks_total, error}
Authentication: None — any upload_id is queryable by anyone who has or guesses it
Side effects: None (read-only)
Dependencies: Redis (job status)
Failure modes: 404 if unknown upload_id
Known limitations: upload_id is a UUID4 (not brute-forceable), but there is no ownership check the way chat sessions have — this is a real, smaller-scale asymmetry with the session-token work done in Section 13

Endpoint: GET /health, /health/live, /health/ready
Method: GET
Purpose: Liveness/readiness checks
Input: None
Output: See Section 17 (Deployment) for full detail
Authentication: None
Side effects: None (read-only, though /health/ready does execute a real SELECT 1 and a real Redis PING)
Dependencies: Postgres, Redis, worker task state
Failure modes: /health/ready returns 503 if any critical check fails
Known limitations: None significant

**CORS:** configured in `main.py` — allows `localhost:3000` explicitly, plus any `https://agent-os*.vercel.app` origin via regex (the real endpoint of a documented debugging saga in Phase 4 of the timeline, where this exact regex was broken and then fixed across several commits).

**Lifecycle management:** `main.py`'s `lifespan` context manager starts 2 document-ingestion workers plus 1 reclaim-loop task on startup, and gracefully signals them to stop (via `_stop_event`) on shutdown, awaiting their completion before the process exits. Worker task references are held in a small shared module (`app/worker_state.py`) specifically to let `/health/ready` introspect their liveness without a circular import back into `main.py`.

---

## 5. Agent Runtime

**State model:** `AgentState` (TypedDict) — `{messages: list[dict], next: str}`. Minimal by design: no separate "scratchpad," "plan," or multi-step state beyond the message list itself.

**Graph structure** (`backend/app/agents/graph.py`, verified current, 137 lines):

    model (call_model)
       |
    router() checks: did the model request a tool call?
       |
       +-- yes --> tools (call_tool) --> respond (final LLM call) --> END
       |
       +-- no  --> respond (final LLM call) --> END

**This is the single most important structural fact about the current
agent runtime:** there is no edge from `tools` back to `model`. The
model gets exactly one opportunity to decide whether to call a tool,
and if it does, the tool's result goes into one more (final) LLM call
that cannot itself request another tool. This means AgentOS today is
better described as "a model with one-shot tool augmentation," not an
iterative agent loop.

**Request flow** (verified against `backend/app/api/agents.py`'s
`_run_agent()`):

    User message
       |
    Session-ownership check (new session -> issue token;
    existing session -> verify token, fail closed on Redis outage)
       |
    Idempotency cache check (same session_id + message within
    the cache window -> return cached result, skip everything below)
       |
    Load conversation history (Postgres, via app.memory.long_term)
       |
    agent.ainvoke() -- the LangGraph graph above --
    wrapped in one parent Langfuse observation so the whole run
    (model decision, tool execution, final response) traces as
    one connected tree, not disconnected fragments
       |
    Persist new messages to Postgres
       |
    Cache the response (Redis, for idempotency)
       |
    Return to caller

**Termination:** the graph always reaches `END` after at most one
`respond` call — there is no loop, so there is no risk of an
infinite/runaway agent loop today (a real, if incidental, safety
property of the current simplicity).

**Model selection:** delegated entirely to `app/routing/router.py`
inside `chat_completion()` — see Section 12 for the real routing
logic and its measured behavior.

---

## 6. Tool System

| Tool | Read/Write | External dependency | Side effects | Security risk | Tested |
|---|---|---|---|---|---|
| retrieve | Read | Postgres/pgvector | None (pure read) | Low — reads only from the shared corpus, no argument beyond a query string reaches a shell/filesystem | Indirectly, via retrieval benchmarks; no dedicated unit test of the tool wrapper itself |
| web_search | Read | Tavily API | Real external network call, real API cost | Low-medium — the query string is passed to a third-party API verbatim; no evidence of prompt-injection filtering on returned web content before it re-enters the LLM context | Not directly unit-tested |
| calculator | Read (pure computation) | None | None | Low, PROVIDED the implementation does not use unrestricted `eval()` — NEEDS CONFIRMATION: exact expression-evaluation method not re-verified in this pass, see `backend/app/tools/calculator.py` (36 lines) for the real implementation | Not directly unit-tested |

**Tool description as a real, load-bearing behavioral lever:** the
`retrieve` tool's description text is not just documentation — it
directly determines whether the model chooses to call it at all. This
was proven empirically in Phase 11 (Section 2): the original
description ("use this when the user refers to something they
uploaded") caused a real, measured 0/10 failure rate on genuine
domain questions; rewriting it to frame the corpus as a proactively
searchable knowledge base fixed 6 of those 10 with no regression
elsewhere. **This is a genuine, general engineering lesson for this
codebase: tool descriptions are behavior, not documentation, and
changes to them should be treated with the same care as logic
changes** — ideally accompanied by a re-run of `evals/runners/agent_task_eval.py`.

**Security implications for future capability growth:** none of the
3 current tools can write, execute arbitrary code, or take an action
with real-world side effects beyond a search-engine query and a
retrieval read. If AgentOS's tool set grows to include write-capable
tools (sending an email, modifying a document, executing code), the
current architecture has **no tool-level authorization or
confirmation layer** — this is a real, named gap for Section 25
("Do Not Break This") and Section 34 (AI-specific principles).

---

## 7. Memory System

Two genuinely distinct systems exist, not one:

**Short-term (`app/memory/short_term.py`, 34 lines):** an in-process
Python dict (`ShortTermMemory`), explicitly documented in its own
docstring as intentional scaffolding — "resets when the server
restarts... establishes the interface long_term.py will later back
with a real database." **Needs confirmation**: whether this class is
still actually used anywhere in the live request path, or whether it
was fully superseded by `long_term.py` once persistent memory landed
in Phase 3 of the timeline — this should be checked directly before
relying on this section further (a real candidate for either
removal or a clarifying comment, see Technical Debt in a later
installment).

**Long-term / persistent (`app/memory/long_term.py`, 57 lines +
`app/memory/models.py`, 20 lines):** the real, live memory system.
Backed by a single Postgres table, `conversation_messages`
(`id, session_id, role, content, tool_calls, tool_call_id,
created_at`). Storage is keyed by `session_id` only — there is no
`user_id` or tenant column, consistent with the "no multi-tenancy"
finding in Section 1.

**What is stored:** every message in a conversation, including tool
calls (serialized to a `Text` column as JSON) and tool results
(linked back via `tool_call_id`).

**Retrieval:** `get_history(session_id, db)` — a full, unfiltered
fetch of every message for that session_id, ordered chronologically.
**Needs confirmation:** no evidence of pagination or a maximum
history length; a very long conversation could in principle grow the
LLM context window without bound (a real, plausible cost/latency
risk worth checking directly against the exact query implementation).

**Deletion:** **NOT VERIFIED** — no explicit deletion/retention
endpoint or scheduled cleanup job was found in this pass. Data
persists indefinitely by default.

**Failure behavior:** memory reads/writes go through the same
Postgres session as the rest of the request — a database outage
would surface as a real request failure, not a silent degradation
(unlike Redis-backed systems in this codebase, which mostly fail
open).

---

## 8. Retrieval / RAG System

### 8.1 Ingestion

    File upload (PDF/TXT/MD)
       |
    R2 object storage (or local /tmp fallback if R2 unconfigured)
       |
    Background worker downloads from R2 to a fresh local temp file
       |
    Text extraction (app/retrieval/ingestion.py, load_document())
       |
    NUL-byte sanitization (a real fix — see ENGINEERING_LOG.md bug #4)
       |
    Chunking (app/retrieval/chunking.py) — chunk_size=1000, overlap=100
       |
    Batched embedding (up to 100 texts/request — a real optimization,
    see Phase 8 of the timeline)
       |
    Stored to Postgres: DocumentChunk{id, source, chunk_index, text, embedding}
       |
    Checkpointed per-chunk (Redis) -- ONLY after each chunk's DB commit
    succeeds (see ENGINEERING_LOG.md bug #3 for why this ordering matters)
       |
    R2 object deleted (only after full success — the source file is
    no longer needed once its chunks are durably stored)

**Supported formats:** `.pdf`, `.txt`, `.md` (enforced at the API
layer, `ALLOWED_EXTENSIONS` in `documents.py`).

**Chunking strategy:** fixed-size with overlap (1000 chars, 100
overlap) — not semantic/sentence-aware chunking. **NOT VERIFIED**
whether chunk boundaries can split mid-sentence or mid-table; this is
a plausible, unconfirmed source of some retrieval misses.

**Embedding model:** OpenRouter-backed (see `app/retrieval/embeddings.py`,
144 lines) — the exact model string was not re-verified in this pass;
NEEDS CONFIRMATION against the live code before citing a specific
model name.

### 8.2 Retrieval

**Two genuinely separate retrieval code paths exist in this
codebase — a real, previously-undocumented finding:**

1. **`app/retrieval/hybrid.py`** (88 lines) — the real, live, production
   path. BM25 (`bm25_search.py`, 82 lines) + dense vector search
   (`_vector_search_ranked`) fused via Reciprocal Rank Fusion. This is
   what the `retrieve` tool actually calls, and what every benchmark
   in `BENCHMARKS.md` measures.

2. **`app/retrieval/pipeline.py`** (44 lines) — an older, dense-only
   function (`retrieve_relevant_chunks`). **Verified in this pass:
   zero other files import from `pipeline.py` anymore** — this is
   genuinely dead code, superseded by `hybrid.py` but never removed.
   A real, small piece of technical debt worth cleaning up (see the
   Technical Debt section in a later installment) — not urgent, but
   worth knowing this file exists and should NOT be mistaken for a
   currently-used alternate code path.

**BM25 implementation detail, real and load-bearing:** `bm25_search()`
explicitly defers loading the `embedding` column
(`.options(defer(DocumentChunk.embedding))`) — this is not a minor
optimization, it is the direct fix for the real production incident
in ENGINEERING_LOG.md bug #1 (Neon transfer quota exhaustion). This
defer is covered by a dedicated regression test
(`test_bm25_data_transfer.py`) and should never be silently removed
by a future refactor.

**Fusion:** Reciprocal Rank Fusion (RRF), not a learned/trained
reranker. A dedicated cross-encoder reranker is a named, real gap
(Section 1) — not yet built.

**Top-k:** 3, used consistently across the `retrieve` tool and all
retrieval benchmarks.

**Filtering/thresholds:** **NOT IMPLEMENTED** — pgvector always
returns its k-nearest neighbors regardless of true relevance; there
is no similarity-score cutoff or "no relevant result" signal. This is
a real, explicitly named and measured limitation (the negative-control
question in `evals/datasets/retrieval_qa.json` demonstrates it
directly — see Section 8.3).

### 8.3 Retrieval evaluation (full detail already established in `BENCHMARKS.md`)

- **Recall@3 (hybrid, production pipeline): 94.3%** (33/35), 132 docs,
  7,748 chunks, 4 domains, real infrastructure, dated Aug 23, 2026.
- **Ablation:** BM25-only 91.4%, dense-only 94.3%, hybrid 94.3% — hybrid
  tied dense rather than clearly winning on this dataset; reported
  honestly, not spun.
- **LLM-as-judge grounding: 35/35 (100%)**, a second, independent
  confirmation of the earlier vocabulary-overlap heuristic's result.
- Full methodology, costs, and honest caveats for every number above:
  `evals/results/BENCHMARKS.md` — not reproduced in full here to
  avoid an unmaintained duplicate source of truth; this handoff cites
  it as the canonical benchmark record.

---

## 9. Document Pipeline (Object Storage)

**Provider:** Cloudflare R2 (S3-compatible API via `boto3`).

**Key structure:** `uploads/{uuid}.{ext}` — flat, no per-user or
per-tenant prefixing (consistent with the "no multi-tenancy" finding).

**Local fallback:** intentional and explicitly designed, not
accidental — `r2_client.is_configured()` gates the choice; if R2
credentials are absent, uploads fall back to local `/tmp` paths. This
is **development-safe, but NOT production-safe at scale**: the
original motivating bug (ENGINEERING_LOG.md bug covered in Phase 11)
is that local-path storage only works because the API server and the
background worker currently share one process. If Render ever scales
to multiple worker instances without R2 configured, this fallback
would silently reintroduce that exact bug. **This is a real,
specific "do not break" invariant** — R2 credentials must remain
configured in any environment that runs multiple worker processes.

**Upload flow / Download flow / Deletion:** see Section 8.1 — object
lifecycle is upload -> (worker downloads once) -> delete-on-success.
Failed ingestion jobs currently leave their R2 object in place (not
cleaned up) — **NOT VERIFIED** whether this is intentional (allowing
a retry to redownload) or an oversight; given the retry path does
correctly redownload from R2 by design (Section 8.1), this is most
likely intentional, but was not explicitly confirmed against the
code in this pass.

**Metadata:** none stored beyond the object key itself — no content
hash, no explicit content-type validation beyond the upload
endpoint's extension check.

---

## 10. Database (PostgreSQL / pgvector, Neon)

**Extensions:** `vector` (pgvector), explicitly created in the initial
Alembic migration (`0001_initial_schema.py`).

**Real tables, verified:**

Table: document_chunks
Purpose: Stores every ingested document chunk and its embedding
Columns: id (PK), source, chunk_index, text, embedding (vector(1536))
Indexes: document_chunks_embedding_hnsw_idx (HNSW, vector_cosine_ops)
Known problems: No foreign key to a "documents" table — source is a bare filename string, so there is no way to query "all chunks for document X" via a real join, only via a string match on source. No content-hash/version column, so re-ingesting the same file with the same name would create duplicate chunk rows rather than being detected as already-ingested.

Table: conversation_messages
Purpose: Persisted chat history
Columns: id (PK), session_id (indexed), role, content, tool_calls, tool_call_id, created_at
Known problems: No user_id/tenant_id column (see Section 1); no explicit retention/deletion mechanism found

Table: chat_sessions
Purpose: (Introduced in Phase 11) — NEEDS CONFIRMATION of exact schema; session-ownership tokens are Redis-backed per `app/auth/session_tokens.py`, not Postgres-backed — this table name is referenced in the architecture diagram (Section 3) but its exact current existence/schema was not re-verified against the live migration files in this pass and should be confirmed directly before being cited as fact.

**Migrations:** Alembic, baselined against production
(`alembic stamp head` confirmed at revision `0001`, per Phase 11).
Real, working — verified end-to-end in Phase 11 of the timeline. Only
one migration exists so far (`0001_initial_schema.py`); no schema
changes have been made since baselining.

**Connection management:** async SQLAlchemy engine/session
(`app/database.py`), one shared engine per process.

**Transaction boundaries:** the checkpoint-before-commit fix
(ENGINEERING_LOG.md bug #3) is the most important documented
transaction-boundary lesson in this codebase — Redis checkpoint
writes are deliberately sequenced to happen only after a Postgres
commit is confirmed, specifically to prevent Redis from claiming
durability that Postgres does not actually have.

---

## 11. Redis / Queue Architecture (Upstash)

**Role:** job queue, response/idempotency cache, rate limiting,
session-ownership tokens — genuinely multi-purpose, not queue-only.

**Queue job lifecycle** (verified against `redis_queue.py`, 246
lines, and `worker.py`, 279 lines):

    enqueue() -- writes job status FIRST, then job data, then
                 pushes to the queue list (this ordering prevents a
                 race where a worker finishes almost instantly and
                 its "done" status write gets clobbered by a
                 late-arriving "queued" write)
       |
    dequeue() -- ATOMIC via a single Redis Lua script: LPOP + ZADD
                 (mark in-flight) as one server-side operation.
                 This closes a real crash-window race (a worker
                 dying between two separate calls could lose a job
                 with no trace) -- see ENGINEERING_LOG.md bug #2.
       |
    worker processes the job, calling touch_job() periodically on
    long-running jobs to refresh its in-flight timestamp
       |
    mark_job_complete() -- removes it from the in-flight set

**Stale-job recovery:**

    IN_FLIGHT, no progress update for RECLAIM_STALE_AFTER_SECONDS (120s)
       |
    reclaim_loop() (runs every 30s) finds it via the in-flight
    sorted set's timestamp scores
       |
    Re-enqueued using its durably-stored job data (a SEPARATE Redis
    key from the queue list itself -- this is what makes reclaim
    possible even after the original LPOP already removed the job
    from the list)

**Atomicity guarantees, explicit:**
- Dequeue + in-flight marking: atomic (Lua script)
- Enqueue status-then-data-then-push ordering: sequential, not
  atomic, but deliberately ordered to avoid the specific race
  described above
- Checkpoint writes: deliberately NOT atomic with the DB commit they
  depend on — instead sequenced strictly AFTER it, which is the
  correct pattern here (see Section 10)

**Fail-open vs. fail-closed, a real and important distinction in this
codebase:** most Redis usage (cache, rate limiting) fails OPEN — if
Redis is unreachable, the app degrades (no caching, no rate limiting)
rather than breaking. Session-ownership tokens are the deliberate
exception: `session_tokens.py` fails CLOSED — if Redis is
unreachable, session verification cannot succeed, and the request is
correctly rejected rather than silently allowed through. **This
distinction is intentional and load-bearing — a future refactor that
"normalizes" all Redis error handling to one pattern would introduce
a real security regression if it made session verification fail
open.**


---

## 12. LLM Layer

**Provider/gateway:** OpenRouter — a single gateway giving access to
multiple underlying model providers (OpenAI, Anthropic, Google) via
one API, rather than integrating each provider's SDK separately.

**Client:** `app/llm/client.py` (122 lines) — wraps the raw
OpenRouter HTTP call with retry/backoff (tenacity-based) on
transient failures (timeouts, connection errors, 5xx), never retrying
4xx client errors. Verified with a real failure-injection test suite
(23 tests, Phase 6 of the timeline).

**Model routing:** `app/routing/router.py` (25 lines) — genuinely
simple, explicitly documented as such in its own docstring
("intentionally basic to start"). The actual, complete heuristic:

    if tools available AND len(messages) > 2:
        use "strong" tier
    else:
        use "fast" tier

**Empirically justified, not just a heuristic guess:** the choice of
`gpt-4o-mini` as the "fast" tier default is a measured result, not an
assumption — the 4-model benchmark in `BENCHMARKS.md` found it
performed identically (100% success, 100% grounding-heuristic) to
`gpt-4o` at roughly 1/18th the cost, on the same 35-question dataset.
The router itself was separately benchmarked in Phase 11: 12/12 real
scenarios routed exactly as the heuristic predicts, and routing saved
a real, measured 41.5% versus an always-strong baseline.

**Distinguishing empirically justified vs. heuristic vs. historical
choice, as requested by this document's own rules:**
- `gpt-4o-mini` as the fast-tier default: **empirically justified**
  (see above).
- The specific routing RULE (tool presence + message count > 2):
  **heuristic** — explicitly self-described as a starting point, not
  learned or adaptive. A real, named improvement opportunity (a
  complexity-feature-based router, benchmarked against
  always-fast/always-strong/routed) was proposed but not built.
- OpenRouter as the gateway choice: **NOT VERIFIED / historical** —
  no evidence in this pass of a documented comparison against
  calling providers directly; likely a pragmatic early choice
  (Phase 1 of the timeline) that was never revisited once the
  project matured.

**Retry policy:** exponential backoff, up to 3 attempts, only for
genuinely retryable errors (`_is_retryable()` — timeouts, connection
errors, 5xx). The SAME retry policy and retryability logic is shared
between the LLM client and the embeddings client
(`test_is_retryable_matches_llm_client_policy` explicitly asserts
this consistency).

**Cost tracking:** computed from each response's real `usage` field
(actual token counts), not estimated — verified in every cost-bearing
benchmark in `BENCHMARKS.md`. Pricing tables are currently hardcoded
per-model in each eval script that needs them (`llm_benchmark.py`,
`model_router_benchmark.py`) rather than centralized in one shared
module — a small, real piece of duplication worth consolidating (see
Technical Debt, later installment).

**Tracing:** every `chat_completion()` call is wrapped in a Langfuse
`start_as_current_observation` call (`as_type="generation"`). As of
Phase 11, these are correctly nested under one parent "agent_run"
observation per request (started in `_run_agent()`), rather than each
appearing as a disconnected trace — this was a real, fixed bug (see
Section 5, and ENGINEERING_LOG.md's tracing-related entry).
