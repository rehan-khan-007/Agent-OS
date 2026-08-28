# AgentOS — Engineering Handoff

## 0. Executive Summary

AgentOS is a FastAPI + LangGraph AI agent runtime with real tool use
(a persistent document-retrieval knowledge base, web search, and
calculation), backed by PostgreSQL/pgvector, Redis, and Cloudflare
R2, deployed live on Render (backend) and Vercel (frontend).

**What works, verified against real production infrastructure:** hybrid
retrieval (BM25 + pgvector, RRF-fused) over a real 132-document,
4-domain corpus at 94.3% recall@3; a Redis-backed job queue with
atomic dequeue and crash recovery; session-ownership tokens preventing
session hijacking; object storage with a safe local-dev fallback;
model-tier routing with a measured 41.5% real cost saving; connected
Langfuse tracing across full agent runs; 75 backend tests + 20
frontend tests, CI-gated on every push.

**What has failed and been fixed, with real evidence:** 12 distinct
real bugs are documented in `ENGINEERING_LOG.md`, including a
production incident (BM25 accidentally exhausting Neon's transfer
quota in ~4 days) and a real, live tool-selection bug found via a
dedicated evaluation (a `retrieve` tool that scored 0/10 until its
description was rewritten, verified via an unchanged before/after
benchmark).

**What is explicitly NOT yet built:** durable/resumable multi-step
agent execution (the graph supports one tool-selection opportunity
per turn, not an iterative loop), a retrieval confidence/abstention
gate, full user accounts (session tokens prevent hijacking but there
is no login/signup), and a dedicated cross-encoder reranker (RRF
fusion is the current, real substitute).

**Biggest current risk, not yet mitigated:** no multi-tenancy —
the corpus and rate limits are shared across all users; this is fine
for a public demo, not for genuine multi-user production use.

**Most important next step:** either (a) close the remaining named
gaps above within AgentOS directly, or (b) integrate the separate
Workflow Orchestration Engine (WOE) project — currently ~90% complete
in its own repository — as AgentOS's durable execution backbone,
which is the real prerequisite for durable/resumable agent execution
and time-travel debugging. This decision has been deliberately
deferred rather than duplicating WOE's in-progress work.

---

## 1. Project Identity

### 1.1 What AgentOS is

AgentOS solves the problem of building an AI agent that can reliably
answer questions using both its own knowledge and a real, standing
document corpus — while being honest about what it actually knows,
observable in production, and resilient to the real infrastructure
failures (network blips, quota limits, crashed workers) that any
system touching multiple third-party services will eventually hit.

The current implementation supports: a single-agent LangGraph loop
that decides whether a user's message needs a tool call (retrieve,
web_search, or calculator) or a direct answer; document upload and
ingestion into the retrieval corpus; persisted, session-scoped
conversation memory; and a real evaluation suite proving specific,
measured claims about the system rather than assumed ones.

It does NOT yet support: multi-step/iterative tool use within a
single turn, multi-agent orchestration, user accounts, or durable
execution that survives a worker crash mid-agent-run (as opposed to
mid-*ingestion*-job, which IS durable — see Section 11).

### 1.2 Current maturity

**Classification: production-oriented prototype.**

Reasoning: the system is genuinely deployed and reachable at a real
public URL, backed by real managed infrastructure (Neon Postgres,
Upstash Redis, Cloudflare R2), with real automated tests gating every
push and real benchmarks measuring actual behavior — this is
substantially past "prototype." But it lacks multi-tenancy, has a
shared/public corpus with no access control beyond session-ownership
tokens, and has not been load-tested beyond a small number of
concurrent requests (see Section 16) — this is why it is not
classified as a production system or platform.

### 1.3 Current capability matrix

| Capability | Status | Evidence | Limitations |
|---|---|---|---|
| Agent execution | Implemented | `backend/app/agents/graph.py` | Single tool-call opportunity per turn — no loop back from tool result to another model decision |
| Tool calling | Implemented | `backend/app/tools/` (retrieve, web_search, calculator) | No tool-argument validation beyond what the LLM itself provides |
| Web search | Implemented | `backend/app/tools/web_search.py`, Tavily-backed | Not evaluated for currency/reliability the way retrieval was |
| Retrieval | Implemented, measured | `backend/app/retrieval/hybrid.py`; 94.3% recall@3, `evals/results/BENCHMARKS.md` | No confidence/abstention gate — always returns nearest neighbors even if irrelevant |
| Memory | Implemented | `backend/app/memory/` — Postgres-backed, session-scoped | No cross-session/user memory; no explicit deletion/retention policy found |
| Document ingestion | Implemented, measured | `backend/app/queue/worker.py`, `scripts/ingest_papers.py`; 132 docs/7,748 chunks | Checkpointed and crash-recoverable (Section 11), but ingestion itself is not currently resumable mid-parse for a single huge document |
| LLM routing | Implemented, measured | `backend/app/routing/router.py`; 41.5% real cost saving, 12/12 correct, `BENCHMARKS.md` | Heuristic (tool presence + message count), not learned/adaptive |
| Queue | Implemented, tested | `backend/app/queue/redis_queue.py`; atomic Lua dequeue, 21/21 tests vs real Upstash | Enqueue is sequential, not batched — see Section 27 |
| Authentication | Partial | `backend/app/auth/session_tokens.py` | Session-ownership only — no accounts, no passwords |
| Authorization | Not implemented | — | No per-user/tenant data isolation |
| Streaming | Implemented | `backend/app/api/agents.py` `/chat/stream`, SSE | — |
| Observability | Implemented | Langfuse tracing (full run trees), structured JSON logging | No OpenTelemetry despite being a listed dependency (confirmed absent from code) |
| Evaluation | Implemented, extensive | `evals/` — 6 independent runners | Grounding judge shares model family with generation model (a named, general LLM-as-judge limitation) |
| Rate limiting | Implemented, tested | `backend/app/ratelimit/`; fails open on Redis outage | Per-IP only, not per-user/tenant |
| Caching | Implemented | `backend/app/cache/`; idempotency + response caching, fails open | — |
| Deployment | Implemented | Render (backend) + Vercel (frontend), live | Single Render worker process (`WEB_CONCURRENCY=1`) |
| Multi-tenancy | Not implemented | — | Named as the biggest current risk (Section 0) |

---

## 2. Project Timeline

Reconstructed from the full, real git history (75 commits, `05df94f`
through `f55ea27`) — every phase below is grounded in actual commits,
not inferred motivation.

### Phase 1 — Foundational scaffold (`05df94f` → `42e15be`)
Initial project scaffold, FastAPI entry point, config/database setup,
API router structure, then the first real agent runtime: LangGraph +
a calculator tool + an OpenRouter-backed LLM client with a real
reasoning loop. This is the earliest point at which AgentOS was a
functioning (if minimal) agent.

### Phase 2 — First RAG pipeline (`2990a4a` → `9fcd40e`)
Document ingestion and chunking, pgvector storage with OpenRouter
embeddings, a retrieval pipeline using pgvector similarity search,
and wiring retrieval into the agent as a real tool. This is the
origin of AgentOS's core RAG capability, initially dense-vector-only
(hybrid BM25+RRF fusion came much later, Phase 7).

### Phase 3 — Memory, evaluation, observability, routing (`ca0ae6d` → `413d199`)
Session-based short-term memory, then persistent Postgres-backed
conversation memory; the first retrieval evaluation harness; Langfuse
tracing for LLM calls; and the first version of model routing based
on request complexity. This phase established the pattern of pairing
new features with real measurement — continued throughout the
project's life.

### Phase 4 — Containerization and deployment (`e2aebb5` → `f93c618`)
Docker containerization, GitHub Actions CI, a Next.js frontend,
binding to Render's dynamic PORT, and a real, messy CORS debugging
saga (`fix: broken CORS allow_origins string`, `fix: correct CORS
syntax and match all agent-os Vercel URLs via regex`, a merge to
resolve a CORS conflict) — the project's first real experience with
production deployment friction, not a clean first attempt.

### Phase 5 — Real-time UX and tool expansion (`7f146f7` → `f8344cd`)
A real bug fix (tool_calls not preserved in memory, causing redundant
LLM calls), a second tool (web search via Tavily), SSE word-by-word
streaming, Vercel Analytics/Speed Insights, and the first Redis usage
in the project: an idempotency cache plus a PDF ingestion pipeline.

### Phase 6 — Real infrastructure hardening begins (`675c518` → `0455fde`)
The first quantified retrieval benchmark (100% recall@1/@3 — but
against only a 3-document corpus, later explicitly superseded as
"trivial to get right," see `BENCHMARKS.md`), a real document upload
endpoint with background processing, structured JSON logging, the
first real pytest suite (17 tests) gated in CI, and retry/timeout
policies with a real failure-injection test suite (23 tests) for both
LLM calls and embedding calls.

### Phase 7 — Real task queue and reliability (`3c4fcf9` → `dd606e6`)
A real Redis-backed task queue replacing FastAPI's in-process
`BackgroundTasks` (fixing a real status-write race condition found
during testing), a worker loop hardened to survive unexpected
exceptions (fixing jobs that got permanently stuck "queued"), a real
production fix (`BLPOP` reliably timing out against Upstash's
serverless proxy, replaced with non-blocking `LPOP` + polling),
Redis-backed rate limiting, chunk-level ingestion checkpointing with
automatic stale-job reclaim, and — critically — hybrid retrieval
(BM25 + vector search fused via Reciprocal Rank Fusion), replacing
the earlier dense-only approach.

### Phase 8 — Frontend redesign, first real benchmarks, testing (`6887e19` → `c091dd0`)
A full frontend redesign (dark instrument-panel theme, live tool-call
traces, light/dark toggle), the project's first real benchmark
document (queue load test + 4-model LLM comparison, with methodology
and honest caveats — establishing the pattern later expanded into
`BENCHMARKS.md`), batched embedding calls (up to 100 texts/request,
replacing one-at-a-time calls) to make large-corpus ingestion
practical, and a real frontend test suite (20 tests, Vitest + RTL).

### Phase 9 — Corpus expansion to real scale (`e64beb6` → `cd896d1`)
Expansion from a 3-document corpus to 132 documents / 7,748 chunks
across 4 domains (quantum control, entrepreneurship, thermal
engineering, personal finance), a pgvector HNSW index, expansion of
the evaluation dataset to 35 questions, a fix to the benchmark script
itself (it had been testing an older retrieval function, not the
actual production `hybrid_search` pipeline), and a real production
bug fix — a NUL byte in one real PDF was crashing the entire
ingestion batch, fixed with text sanitization and per-document error
isolation.

### Phase 10 — LLM-as-judge and concurrency verification (`1ae4b20` → `93f5970`)
A genuine LLM-as-judge grounding evaluation (independent of the
earlier vocabulary-overlap heuristic, 35/35 = 100%, confirming the
heuristic's result rather than merely repeating it), and a real
concurrent-request test against the live deployed backend — which
incidentally, and un-scripted, also confirmed the rate limiter
correctly rejecting real overlapping traffic in the wild.

### Phase 11 — Architecture-review-driven hardening (`1e1cf88` → `5bf224a`)
The largest, most concentrated phase: prompted by an external
architecture review, this phase closed CI's frontend-test gap (tests
existed but had never actually run in CI) and a related Node-version
incompatibility; made queue dequeue atomic via a Lua script (closing
a real crash-window job-loss race); added `/health/live` and
`/health/ready` (previously a static, meaningless health check);
fixed a checkpoint-before-commit ordering bug that could make Redis
permanently lie about what was actually persisted; added a retrieval
ablation (BM25 vs dense vs hybrid, honestly reporting that hybrid
tied dense rather than clearly winning); ran a real agent-task
evaluation that found and fixed a genuine 0/10 tool-selection bug;
measured a real 41.5% cost saving from model routing; wired up
Alembic migrations (previously a listed but unused dependency);
connected Langfuse tracing into full agent-run trees (previously
disconnected per-call fragments); added Cloudflare R2 object storage
(closing a real single-process coupling bug); added session-ownership
tokens (closing a real session-hijacking gap); and finished with a
master documentation rewrite (README, `ENGINEERING_LOG.md`,
`BENCHMARKS.md`) reflecting the project's actual current state.

### Phase 12 — Verification (`f55ea27`)
A trivial, deliberate test commit confirming SSH push access still
worked, made while debugging an unrelated SSH authentication issue
on a separate project (EvalOS) sharing the same development machine.

---


## 3. Current Architecture

    NextNext.js UI (Vercel)
           |
      HTTPS / SSE
           |
    FastAPI API (Render)
    auth / rate-limit / sessions / cache
           |
     -----------------------------
     |            |              |
     LangGraph    Redis          PostgreSQL
     Agent      (Upstash)       + pgvector (Neon)
     model ->    queue, cache,   conversation history,
     tool ->     rate-limit,     document chunks,
     respond     session tokens  chat_sessions
     |
     -----------------------------
     |            |              |
     retrieve    web_search     calculator
     |
     (hybrid: BM25 + pgvector, RRF-fused)
     |
     Background Workers  <-->  Cloudflare R2
     (ingestion,                (uploads, local
     checkpointed)              fallback)
     
     Cross-cutting: Langfuse tracing (full agent-run trees), structured JSON logging, Alembic migrations

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

# AgentOS Engineering Handoff — Part 3: Failure History, Security, Testing, Benchmarks

## CORRECTION to Section 3 (Current Architecture, already committed)

The architecture diagram lists "chat_sessions" under PostgreSQL. This
is INCORRECT and should be fixed. Verified in this pass: there is no
`chat_sessions` table or reference anywhere in the codebase.
Session-ownership tokens are entirely Redis-backed
(`app/auth/session_tokens.py`) with no Postgres component at all.
**Recommended fix:** remove "chat_sessions" from the PostgreSQL box in
Section 3's diagram; the Redis box already correctly lists "session
tokens."

---

## 11. Failure History

Full causal writeups (symptom, cause, fix, verification) for each of
these already exist in `ENGINEERING_LOG.md` — this table is a
structured index into that document, not a duplicate of it.

| # | Incident/Bug | Root Cause | Impact | Discovery | Fix | Regression Test | Status |
|---|---|---|---|---|---|---|---|
| 1 | BM25 exhausting Neon's monthly transfer quota | `select(DocumentChunk)` fetched the unused 1536-dim embedding column on every BM25 call | Real production incident — quota exhausted in ~4 days | Neon dashboard alert | `.options(defer(DocumentChunk.embedding))` | `test_bm25_data_transfer.py` | Fixed, verified |
| 2 | Crash-window job loss in queue dequeue | `LPOP` + `ZADD` were two separate Redis calls | Architecture-review finding; a worker dying between the two calls could silently lose a job forever | Architecture review, not a live incident | Atomic Lua script (LPOP+ZADD as one op) | Full queue suite, 21/21 vs real Upstash | Fixed, verified |
| 3 | Checkpoint-before-commit ordering | Redis checkpoint written inside the loop, before the single `session.commit()` at the end | Could make Redis permanently claim a chunk was stored when Postgres never committed it | Architecture review, motivated by bug #4 below | Checkpoint only written after confirmed commit | Dedicated test simulating a failing commit | Fixed, verified |
| 4 | NUL byte in a real PDF crashing the whole ingestion batch | Extracted PDF text contained `0x00`, which Postgres text columns reject | One bad document could take down an entire ingestion run, including already-succeeded documents | Live ingestion of the real 132-doc corpus | Text sanitization + per-document error isolation | Implicit (ingestion of the full corpus now succeeds) | Fixed, verified |
| 5 | `MaxConnectionsError` checking 200 job statuses | Unbounded concurrent status-check calls during a load test | Load test itself failed to complete | Running `load_test_queue.py` | `asyncio.Semaphore`-capped batches | Implicit (the load test now completes cleanly) | Fixed, verified |
| 6 | Worker task dying silently on unexpected exception | No broad exception handler around the main dequeue loop | Jobs would stop being processed with zero visible symptom | Code review during worker-loop development | Broad exception handler, log-and-continue | `test_worker_survives_unexpected_dequeue_exception` | Fixed, verified |
| 7 | CI never actually running the 20 frontend tests | CI workflow only had a backend `pytest` job | README claimed CI-gated frontend tests; false | Architecture review | Added a real `frontend-test` CI job | The CI job itself, now green | Fixed, verified |
| 8 | Frontend CI failing on Node/jsdom mismatch | `jsdom`'s `undici` dependency needs a newer Node API than GitHub's pinned Node 20 | Immediately surfaced once bug #7 was fixed | The new CI job's first real run | Bumped CI Node version 20 -> 22 | The CI job itself, now green | Fixed, verified |
| 9 | `retrieve` tool scoring 0/10 on genuine domain questions | Tool description framed retrieval as only for explicit user uploads, not the standing corpus | Real, measured agent behavior failure | Dedicated agent-task evaluation (`agent_task_eval.py`) | Rewrote the tool description | Same 30-task eval re-run unchanged: 0/10 -> 6/10, no regression elsewhere | Fixed, partially — see Section 16, 4 remaining honest misses |
| 10 | Async/sync mismatch breaking a test's own mock | `embed_chunks()` is called synchronously in production code; the test's replacement mock was `async def` | Test failure only, no production impact | Running the new checkpoint-ordering regression test | Made the mock a plain (non-async) function | The corrected test itself | Fixed, verified |
| 11 | Module-level Redis client breaking across pytest's per-test event loops | `redis_queue.get_client()` caches a singleton client bound to whichever event loop created it; pytest-asyncio gives each test its own loop | Intermittent test failures ("Future attached to a different loop"), test-infrastructure only | Running new session-token tests | `autouse` fixture resetting the singleton before/after each test | The corrected tests themselves | Fixed, verified |
| 12 | Tracing failure risking a duplicate real agent call | First draft's tracing error-handling fallback re-called `agent.ainvoke()` unconditionally, which could double-execute a call that had already succeeded | Real correctness risk, caught in code review before ever reaching production | Self-review during Langfuse-tracing implementation | `result is None` guard ensures the real call executes exactly once regardless of tracing outcome | Implicit (no test directly targets this failure mode — see Technical Debt) | Fixed, never reached production |

**Engineering lesson extraction, as requested by this document's own
rules:**

- **Bugs #1, #2, #3 share one lesson:** in a system spanning two
  different storage layers with different durability/consistency
  guarantees (Redis vs. Postgres), the ORDER in which you write to
  each matters as much as the individual writes themselves. A future
  contributor adding a third storage layer to this codebase should
  explicitly think through commit ordering before writing any code.
- **Bugs #7, #8 share one lesson:** a claim in documentation (README)
  is not evidence that the underlying system actually does what it
  says — this codebase's own CI configuration silently drifted from
  its documented behavior with nobody noticing until an explicit
  architecture review checked.
- **Bugs #10, #11 share one lesson:** test-only bugs are still real
  bugs, and are sometimes MORE dangerous than application bugs
  precisely because a flaky/wrong test can mask a real regression by
  failing (or passing) for the wrong reason.
- **Bug #12's lesson:** any error-handling fallback that "retries" or
  "falls back to redoing the work" must be checked for whether the
  original work might have already partially or fully succeeded —
  the safe default is to track completion explicitly (a `result is
  None` check, a completion flag), never to assume failure implies
  nothing happened.

---

## 13. Authentication & Security

| Control | Status |
|---|---|
| Session-ownership tokens | IMPLEMENTED — `app/auth/session_tokens.py`, Redis-backed, fails closed |
| User accounts / login | NOT IMPLEMENTED — deliberate scope decision (see Section 2, Phase 11) |
| Rate limiting | IMPLEMENTED — per-IP, fails open on Redis outage |
| CORS | IMPLEMENTED — explicit localhost origin + regex-matched Vercel origins |
| Secrets management | IMPLEMENTED via environment variables (Render/Vercel dashboards); NOT VERIFIED whether any secret has ever been accidentally committed to git history — worth a dedicated check (e.g. `git log -p | grep` for known key prefixes) before treating this repository as safe to make public, if it is not already |
| File upload security | PARTIALLY IMPLEMENTED — extension allowlist and size limit exist; NOT IMPLEMENTED: no authentication on who can upload, no antivirus/content scanning |
| Prompt injection defense | NOT IMPLEMENTED — retrieved document content and web search results are not filtered or sandboxed before re-entering the LLM's context; a malicious or compromised document in the corpus, or a malicious web page returned by search, could in principle attempt to inject instructions into the model's context |
| Tool security | PARTIALLY IMPLEMENTED — see calculator's `eval()`-based implementation below; retrieve and web_search have no argument validation beyond what the LLM itself provides |
| Telemetry privacy | PARTIALLY IMPLEMENTED — Langfuse traces include real user messages and model outputs; NOT VERIFIED whether any PII-scrubbing or redaction exists before data reaches Langfuse's servers |

**Real, specific finding — the calculator tool's `eval()` use:**
verified directly in this pass (`app/tools/calculator.py`):

```
allowed = {"x": 1}
allowed.update({k: v for k, v in math.__dict__.items() if not k.startswith("_")})
result = eval(expression, {"__builtins__": {}}, allowed)
```

This is a **restricted `eval()`** — `__builtins__` is emptied and the
allowed namespace is limited to `math` module functions. This is a
real, deliberate attempt at sandboxing, not an unguarded `eval()`.
However, restricted-`eval()` sandboxing in Python is a **well-known,
historically bypassable category** (object introspection via
attributes like `__class__.__bases__` or `__subclasses__()`, reachable
even from objects inside a restricted namespace, has been used in
publicly documented Python sandbox escapes). **This was NOT tested for
exploitability in this pass** — flagging it here as a real, named risk
requiring a deliberate decision (either a documented acceptance of the
risk given the tool's LLM-mediated exposure, or a replacement with a
genuinely safe expression evaluator such as a proper math-expression
parser library), not a claim that it has been exploited or is
definitely exploitable as currently deployed.

---

## 14. Current Security Threat Model

### Identity
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Session token theft (XSS, network interception) | Low-Medium | High (full conversation access) | HTTPS in transit; token never logged | No token rotation; no expiry shorter than the session itself was verified | Medium |
| session_id-only impersonation | Low (fixed by Section 13) | — | Session-ownership tokens | None remaining — this was the original, now-closed gap | Closed |

### API
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Chat endpoint abuse (cost exhaustion via scripted requests) | Medium | Real dollar cost | Per-IP rate limiting (20/5min on chat) | Fails open on Redis outage — a Redis outage removes this protection at exactly the moment it might matter most | Medium |
| Upload endpoint abuse | Medium | Corpus pollution, storage cost | Tighter rate limit (5/10min), size/extension checks | No authentication on who can upload at all | Medium |

### RAG
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Cross-tenant retrieval leakage | N/A currently | N/A — no tenants exist yet | — | Real risk the MOMENT multi-tenancy is added without also adding per-tenant retrieval filtering | High, if multi-tenancy is ever added without this in the same change |
| Document poisoning (a malicious upload skewing retrieval/answers) | Medium (anyone can upload) | Medium — could cause the agent to state false information as if grounded | None | No content vetting on upload | Medium |
| Indirect prompt injection via retrieved content | Low-Medium | Medium-High depending on future tool capabilities | None | Retrieved text and web results are not filtered before re-entering the LLM context | Medium, HIGH if write-capable tools are ever added |

### Tools
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Calculator sandbox escape | Unknown (not tested) | Potentially high (arbitrary code execution) if exploitable | Restricted `__builtins__`/namespace | Not verified safe against known Python eval-sandbox bypass techniques | High — recommend explicit testing or replacement |
| Excessive agency (future write-capable tools) | N/A currently | N/A currently | 3 current tools are all read-only/side-effect-free | No tool-level authorization/confirmation layer exists for when this changes | High, if write-capable tools are ever added without this |

### LLM
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Prompt injection / jailbreak | Medium | Low-Medium given current read-only tool set | None specific | No input/output filtering | Low currently, rises with tool capability |
| Unbounded token/cost consumption | Low | Medium (cost) | Rate limiting | No explicit per-request token budget/cap found | Medium |

### Infrastructure
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Secrets exposure (env vars, git history) | Low | High | Env vars not hardcoded in source (verified throughout this codebase's real files) | Git history itself not audited in this pass | Medium — recommend a dedicated audit |
| Third-party service compromise (Neon/Upstash/R2/OpenRouter) | Low | High | Real, working spending alerts on Neon and Cloudflare (see project history) | No equivalent alert confirmed for OpenRouter or Upstash | Low-Medium |

---

## 15. Testing Strategy

**Real counts, verified this session:** 13 backend test files
(`backend/tests/`), 3 frontend test files (`frontend/app/`).
`README.md` and `ENGINEERING_LOG.md` state 75 backend + 20 frontend
tests as the cumulative test count across all files — this reflects
number of individual test functions, not file count, and is
consistent with what was directly observed passing in full-suite runs
throughout this project's history.

**Categories genuinely present:**
- **Unit** — chunking, hybrid-retrieval fusion logic, rate-limit IP
  extraction, retryability classification
- **Integration, real infrastructure** — BM25 data-transfer regression
  (real DB), full queue/checkpoint suite (real Upstash), session
  tokens (real Upstash)
- **Failure-injection** — LLM/embedding retry behavior under simulated
  timeouts/connection errors/5xx; worker survival under unexpected
  exceptions; checkpoint-ordering under a simulated commit failure
- **Load/concurrency** — `scripts/load_test_queue.py` (200 jobs,
  stub handler, zero LLM cost),
  `scripts/concurrent_load_test.py` (real simultaneous HTTP requests
  against the live deployment, checking specifically for
  cross-request state contamination)
- **Evaluation** (`evals/`) — a genuinely separate category from
  `pytest`-based testing; see Section 16

**Categories NOT present / explicitly named as gaps:**
- **Security testing** — no dedicated test suite for the threats named
  in Section 14 (e.g., no test attempting to exploit the calculator's
  `eval()` sandbox)
- **True end-to-end (browser-driven) testing** — frontend tests are
  component-level (Vitest + RTL), not a real browser automation suite
  against the live deployed site
- **Database/Redis restart-recovery testing** — the project has proven
  recovery when a WORKER process crashes and the queue/database
  remain available; it has NOT been proven that the system recovers
  correctly if Postgres or Redis themselves restart mid-operation

---

## 16. Benchmarks & Empirical Evidence

`evals/results/BENCHMARKS.md` is the canonical, single source of
truth for every benchmark this project has produced — this section
summarizes what exists there rather than duplicating exact numbers
that could drift out of sync if updated in only one place.

**Real, measured, and dated results currently in `BENCHMARKS.md`:**
1. Task queue load test (200 jobs, 100% terminal completion, 98%
   success excluding deliberately injected failures)
2. Hybrid retrieval recall@3 (94.3%, 132 docs / 7,748 chunks / 4
   domains)
3. 4-model LLM benchmark (cost/latency/grounding-heuristic across
   gpt-4o-mini, gpt-4o, claude-haiku-4.5, gemini-3.7-flash)
4. LLM-as-judge grounding evaluation (35/35, independently confirming
   the heuristic)
5. Live concurrent-request test (real simultaneous requests against
   the deployed backend; zero cross-request contamination; the rate
   limiter caught real, unplanned overlapping traffic correctly)
6. Retrieval ablation (BM25 91.4% / dense 94.3% / hybrid 94.3% — a
   genuine tie honestly reported, not spun toward hybrid)
7. Agent tool-selection accuracy (86.7% overall; includes the real
   0/10 -> 6/10 bug fix documented as bug #9 in Section 11)
8. Model router cost savings (41.5% real savings vs. always-strong,
   12/12 routing decisions matched the documented logic)

**Explicitly distinguishing measured / estimated / claimed, per this
document's own rules:** every number above is MEASURED — computed
from real API responses' actual `usage` fields, real test-suite pass
counts, or real timed executions against live infrastructure. None of
the headline numbers in `BENCHMARKS.md` are estimated or merely
claimed without a corresponding real run. The one important caveat,
already self-documented in `BENCHMARKS.md` itself: the grounding
LLM-judge (item 4) shares a model family with the answer-generation
model in the same benchmark — a real, general limitation of
LLM-as-judge methodology, not a data-quality problem with this
specific result.

**What these benchmarks do NOT prove**, also already stated in
`BENCHMARKS.md`'s own closing section: performance at the scale of
hundreds of concurrent users, or the behavior of genuinely long-running
multi-step agent workflows (since none currently exist to benchmark —
see Section 5's finding that no multi-hop tool loop exists yet).

# AgentOS Engineering Handoff — Part 4: Decisions, Debt, Risks, Roadmap

## 21. Architectural Decisions (ADR-style)

Every decision below reflects a real, actual choice made in this
project's history (Section 2), not a hypothetical alternative
invented for this document.

### ADR-1: PostgreSQL + pgvector for both relational and vector data
**Context:** needed both structured storage (conversations, chunks)
and semantic vector search.
**Options considered:** a dedicated vector database (Pinecone,
Weaviate, Qdrant) alongside a separate relational database; or
pgvector inside the already-necessary relational database.
**Chosen:** pgvector inside Postgres.
**Why:** avoids running/paying for/operating two separate database
systems; real evidence this held up — the HNSW index (Phase 9) gave
real, adequate vector search performance at 7,748 chunks without ever
needing a dedicated vector DB.
**Trade-offs:** a dedicated vector DB might outperform pgvector at
much larger scale (see Section 27, Architectural Ceiling); accepted
as a real, currently-non-binding limitation.
**Current validity:** sound at current scale. **Revisit condition:**
if corpus size grows by roughly 1-2 orders of magnitude and HNSW
query latency becomes a measured bottleneck.

### ADR-2: Redis (Upstash) as a multi-purpose infrastructure layer
**Context:** needed a job queue, a cache, rate limiting, and later,
session-ownership token storage.
**Options considered:** separate purpose-built services for each
(e.g., a dedicated queue service, a dedicated rate-limiting service).
**Chosen:** one Redis instance, serving all four purposes via
different key prefixes/data structures.
**Why:** pragmatic — each individual purpose's data volume is small;
consolidating avoids operational overhead of managing multiple
services for a project at this scale.
**Trade-offs:** a real production incident (BM25 quota exhaustion,
Section 11 bug #1) was actually a *Postgres* transfer issue, not
Redis, but the broader lesson (shared infrastructure needs care about
which subsystem is doing what) applies. The fail-open/fail-closed
split (Section 10) is the direct, correct answer to "does
multi-purposing Redis create risk" — verified real and intentional,
not an oversight.
**Current validity:** sound. **Revisit condition:** if queue volume
or cache volume individually grow enough to threaten each other's
performance (e.g., a very large cache eviction pattern disrupting
queue latency) — not currently observed.

### ADR-3: Cloudflare R2 for object storage, with a local-path fallback
**Context:** document uploads needed to work correctly regardless of
which process/machine handles ingestion (Section 9).
**Options considered:** AWS S3 (real alternative considered and
rejected — Section 9's history: S3's free tier is time-limited to 12
months and charges real egress fees; R2's free tier is permanent with
zero egress fees).
**Chosen:** Cloudflare R2, S3-compatible API.
**Why:** directly, empirically justified — not a guess. The specific
free-tier terms (10GB storage, 1M writes, 10M reads/month, permanent,
zero egress) were verified via real research before choosing, and
this project's real usage (occasional document uploads) is nowhere
near those limits.
**Trade-offs:** no hard spending cap exists on Cloudflare (confirmed
via real research — only an informational budget alert), a real,
accepted residual risk given the codebase's naturally bounded R2
usage pattern (rate-limited uploads, one download/delete per
ingestion job — not an unbounded-scaling risk the way the original
BM25 bug was).
**Current validity:** sound. **Revisit condition:** none currently
identified.

### ADR-4: LangGraph for the agent runtime
**Context:** needed a framework for the model-decides-then-optionally-
calls-a-tool loop.
**Options considered:** **NOT VERIFIED / historical** — no evidence
in this pass of a documented comparison against a hand-rolled
state machine or an alternative framework; this was very likely a
Phase 1 (earliest) choice never revisited.
**Chosen:** LangGraph.
**Why:** **INFERRED** — provides a structured way to define
model/tool/respond nodes and conditional edges, which the actual
current graph structure (Section 5) directly uses.
**Trade-offs:** the current graph's single biggest limitation (no
loop back from tool to model, Section 5) is a design choice made
WITHIN LangGraph's own capabilities, not a limitation of LangGraph
itself — LangGraph supports cyclic graphs; AgentOS's current graph
simply does not use one yet. This is worth being precise about: the
framework is not the blocker for iterative tool use; the graph
definition is.
**Current validity:** sound. **Revisit condition:** none — the
framework is not what needs to change to add iterative tool use.

### ADR-5: OpenRouter as the LLM gateway
**Context:** needed access to multiple model providers (OpenAI,
Anthropic, Google) for the 4-model benchmark and routing.
**Chosen:** OpenRouter, a single gateway.
**Why:** **INFERRED** — one API/one key instead of three separate
provider SDKs and credential sets; directly enabled the 4-model
benchmark and routing work without needing three sets of provider
credentials.
**Trade-offs:** an intermediary layer between AgentOS and each real
provider — **NOT VERIFIED** whether this adds meaningful latency
overhead versus calling providers directly (not measured in any
benchmark to date).
**Current validity:** sound, real evidence of value (routing,
multi-model benchmarking both depend on it). **Revisit condition:**
if OpenRouter-specific latency overhead is ever measured and found
significant.

### ADR-6: Heuristic model routing (tool presence + message count)
**Context:** wanted real cost savings without the complexity of a
learned/adaptive router.
**Chosen:** the simple rule in Section 12.
**Why:** empirically validated AFTER the fact, not before — the
router was built first as a reasonable guess, then genuinely
benchmarked (Section 16, item 8): 41.5% real savings, 12/12 correct
routing decisions.
**Trade-offs:** explicitly self-documented in the code as "a
starting point" — does not adapt to actual task complexity beyond
the two simple signals it checks.
**Current validity:** sound, empirically justified for current use.
**Revisit condition:** if request patterns diversify beyond what the
two-signal heuristic can distinguish (e.g., long single-message
requests that need the strong model but don't trigger the current
rule).

### ADR-7: Hybrid retrieval (BM25 + dense, RRF-fused) over dense-only
**Context:** the original retrieval implementation (Phase 2) was
dense-vector-only.
**Chosen:** added BM25 and RRF fusion (Phase 7).
**Why:** the retrieval ablation (Section 16, item 6) is the real,
honest justification — and honestly, it complicates the simple
narrative: BM25 alone measurably trails (91.4% vs 94.3%), but hybrid
TIED dense-only rather than clearly beating it on this dataset. The
real justification for keeping hybrid is not "it's proven better" —
it's that BM25 and dense retrieval demonstrably miss DIFFERENT
questions (a real finding in the ablation's own caveats), suggesting
genuine complementary value not fully captured by the aggregate
recall@3 number on a 35-question sample.
**Trade-offs:** added real complexity (two retrieval systems instead
of one) for a benefit that is real but not as clean as "hybrid wins."
**Current validity:** reasonable given the complementary-failure
evidence, but this is the kind of decision that would benefit from a
larger dataset (Section 27) to resolve more conclusively.
**Revisit condition:** if a larger, more statistically powered
ablation ever shows dense-only performing equivalently with less
system complexity.

### ADR-8: Session-ownership tokens instead of full user accounts
**Context:** a real, named security gap — a leaked `session_id` alone
could be used to hijack a conversation.
**Options considered:** (a) a full user-account system (signup,
login, passwords); (b) a lightweight ownership token alongside the
existing session_id.
**Chosen:** (b), option B — deliberately, explicitly, as a scoped
decision (Section 2, Phase 11).
**Why:** closes the actual, specific, named vulnerability without the
much larger investment of a full account system this project doesn't
yet clearly need.
**Trade-offs:** explicitly does NOT provide per-user data isolation
or multi-tenancy (Section 1's biggest named risk) — this was a
conscious scope decision, not an oversight.
**Current validity:** sound for the problem it was built to solve.
**Revisit condition:** the moment genuine multi-user/multi-tenant use
is required, this decision must be revisited together with a real
accounts system — see Roadmap, Section 32.

### ADR-9: Fail-open caching/rate-limiting, fail-closed session tokens
**Context:** covered fully in Section 10 — a deliberate, real,
security-relevant distinction in this codebase.
**Current validity:** sound and important; explicitly flagged as a
"do not break" invariant (Section 25, in a future installment).

---

## 23. Current Technical Debt

### P0 — Correctness/Security
| Problem | Location | Why it matters | Recommended fix | Risk of NOT fixing |
|---|---|---|---|---|
| Calculator's restricted `eval()` not verified safe against known Python sandbox-escape techniques | `app/tools/calculator.py` | LLM-mediated arbitrary-code-execution risk if bypassable | Replace with a real math-expression parser library, or explicitly test/document the accepted risk | Unknown severity until tested — treat as real until disproven |
| Architecture diagram (Section 3) incorrectly lists a `chat_sessions` Postgres table that does not exist | `ENGINEERING_HANDOFF.md` itself | Misleads a future reader about where session data actually lives | Remove the incorrect line (flagged in Part 3 of this handoff) | Low technical risk, real documentation-accuracy risk |

### P1 — Serious production blockers (if usage grows)
| Problem | Location | Why it matters | Recommended fix | Risk of NOT fixing |
|---|---|---|---|---|
| No multi-tenancy | System-wide | Corpus and rate limits are shared across all users | A real `SystemAdapter`-style scoping (tenant_id columns, per-tenant retrieval filtering) | Fine for a demo; a real problem the moment genuine multi-user usage begins |
| No authentication on document uploads | `app/api/documents.py` | Anyone can add documents to the shared corpus | Require session-ownership token (already exists for chat) on uploads too | Corpus pollution, real content-quality risk |
| Sequential (non-batched) Redis enqueue | `app/queue/redis_queue.py` | Real, documented throughput bottleneck (`BENCHMARKS.md`: 1.2 jobs/sec) | Pipeline/batch enqueue writes | Currently fine at real observed load; would matter at real scale |

### P2 — Important engineering debt
| Problem | Location | Why it matters | Recommended fix | Risk of NOT fixing |
|---|---|---|---|---|
| `app/retrieval/pipeline.py` is dead code | Confirmed via grep in this pass — zero imports | Confusing for a future contributor who might assume it's a live alternate code path | Delete it, or add a clear "DEPRECATED, superseded by hybrid.py" docstring | Low — purely a clarity issue |
| Pricing tables duplicated across eval scripts | `evals/runners/llm_benchmark.py`, `model_router_benchmark.py` | Real, small drift risk if one is updated and the other isn't | Consolidate into one shared pricing module | Low currently, grows with more eval scripts |
| `app/memory/short_term.py`'s current live-usage status is unconfirmed | `app/memory/` | Unclear whether this is dead code (like `pipeline.py`) or still genuinely used | A direct grep/import check (not done in this pass) | Low — a documentation-clarity gap, not a functional risk unless it IS still live and interacting with `long_term.py` in an undocumented way |
| No conversation-history length cap | `app/memory/long_term.py` | A very long conversation could grow the LLM context window unboundedly | An explicit max-messages or max-tokens truncation on history fetch | Currently unobserved in practice; a real latent cost/latency risk |
| No centralized OpenTelemetry despite being a listed dependency | `requirements.txt` vs. actual code (confirmed absent in an earlier pass) | A misleading unused dependency | Either implement it or remove the unused dependency | Low — Langfuse already covers the real observability need |

### P3 — Quality-of-life
| Problem | Location | Why it matters | Recommended fix |
|---|---|---|---|
| No dedicated regression test for the "duplicate agent call" risk fixed in Section 11, bug #12 | `backend/tests/` | The fix is real and correct but currently unguarded against a future regression | A test that simulates a tracing failure AFTER a successful `agent.ainvoke()` and asserts the agent function is called exactly once |
| No document-level dedup on re-ingestion | `app/retrieval/models.py`'s `DocumentChunk` (no content hash) | Re-ingesting the same file under the same name creates duplicate rows | Add a content-hash column and check-before-insert |

---

## 27. Architectural Risks at Scale

| Scale | Database (Neon) | Redis (Upstash) | Queue/Workers | LLM/Retrieval | Cost |
|---|---|---|---|---|---|
| 10 users | Fine, real headroom | Fine | Fine (2 workers is ample) | Fine | Negligible, matches real measured costs in `BENCHMARKS.md` |
| 100 users | Likely fine — real risk only if per-session history grows unbounded (Section 23, P2) | Fine | Fine, though sequential enqueue (Section 23, P1) starts to matter at real concurrent upload volume | Fine; rate limiting protects cost | Low, real routing savings (41.5%) compound favorably |
| 1,000 users | Real risk: shared corpus + no multi-tenancy means retrieval quality/relevance degrades as unrelated users' documents mix in the same corpus — a real correctness problem, not just a scale problem | Possible real contention between cache/queue/rate-limit/session-token traffic sharing one instance | Sequential enqueue becomes a real, measurable bottleneck | Real cost becomes worth monitoring closely, though routing helps | Worth a real Neon/Upstash tier review at this point |
| 10,000 users | Multi-tenancy becomes a hard requirement, not an optimization | A single Redis instance may need to split by purpose (separate queue vs. cache instances) | Needs real horizontal worker scaling — current single-Render-process (`WEB_CONCURRENCY=1`) model needs to change; this is exactly where the R2 fallback safety (Section 9) becomes load-bearing rather than theoretical | Retrieval-quality risk from #1,000 compounds; a real reranker (currently absent) starts to matter more | Real, meaningful cost — this is the point where the model-routing savings become operationally important, not just a nice benchmark number |
| 100,000 users | Requires a genuine redesign, not incremental fixes: real multi-tenancy, likely a dedicated vector DB reconsideration (ADR-1's revisit condition), horizontally scaled workers, and almost certainly the WOE integration (durable execution) discussed throughout this project's history | Would need a real, purpose-split Redis architecture | Requires the durable-execution work this project has explicitly deferred to WOE integration | Requires real retrieval-quality engineering (reranker, confidence gating — both already named gaps in Section 1) | Requires real, dedicated cost engineering — not a redesign this document should attempt to specify in the abstract |

**Explicitly, per this document's own instructions: no
recommendation for Kubernetes, Kafka, microservices, or Elasticsearch
is made at any of these scales** — the real bottlenecks identified
above (multi-tenancy, worker horizontal scaling, retrieval quality)
do not require those specific technologies to solve, and none of
this project's actual real usage has approached even the 1,000-user
row.

---

## 28. Current Architectural Ceiling

| Subsystem | Current design | Likely ceiling | Scaling problem | Migration trigger |
|---|---|---|---|---|
| Retrieval (pgvector + HNSW) | Single Postgres instance, HNSW index | Real, workable well beyond current 7,748 chunks — HNSW is designed for this; NOT VERIFIED at what exact chunk count query latency becomes a real problem | Index rebuild time, memory pressure on the Neon instance at very large corpus sizes | A real, measured latency regression as corpus grows — not yet observed |
| Queue (Redis, single instance) | Sequential enqueue, atomic Lua-script dequeue | Fine well beyond current real usage; sequential enqueue is the first real bottleneck | Enqueue throughput (measured: 1.2 jobs/sec) | Sustained real upload volume exceeding that rate |
| Workers (single Render process) | 2 ingestion workers + 1 reclaim loop, in-process | Bounded by Render's single-process CPU/memory | Concurrent ingestion jobs compete for the same process's resources | Real observed ingestion queue backlog |
| Agent runtime (single-turn, one tool call) | LangGraph, no loop | Not a "scale" ceiling in the traditional sense — a capability ceiling: cannot handle tasks genuinely requiring multiple sequential tool calls regardless of user count | N/A (capability, not throughput) | A real task requirement for multi-step tool use — see Roadmap |
| Database (single Neon instance) | Shared corpus, no tenant isolation | Real ceiling is NOT storage/compute — Neon's Launch plan (already active) has real headroom — the ceiling is CORRECTNESS: unrelated users' data mixing in one corpus | Retrieval relevance degradation as unrelated content grows | Any real requirement for more than one distinct "corpus" of documents |

---

## 32. Roadmap (dependency-aware, not a feature wishlist)

### Phase 0 — Correctness & Security (no dependencies, do first)
- Fix the Section 3 `chat_sessions` diagram error (trivial, already
  identified)
- Resolve the calculator `eval()` risk (test-and-accept, or replace)
- Confirm `short_term.py`'s live-usage status; remove or clarify
- Add authentication to the document-upload endpoint

**Depends on:** nothing. **Blocks:** nothing downstream, but should
close before any of the below to avoid building on top of an
unresolved security question.

### Phase 1 — Production Foundation
- Conversation-history length cap
- Consolidate duplicated pricing tables into one shared module
- Document-level content-hash dedup on ingestion
- Batch/pipeline Redis enqueue (Section 23, P1)

**Depends on:** Phase 0 (clean baseline). **Blocks:** nothing
critical, but reduces real risk before scale-sensitive work below.

### Phase 2 — Agent Runtime (the single biggest capability gap)
- Add a real loop-back edge from `tools` to `model` in the LangGraph
  graph, enabling genuine multi-step tool use within one turn
- Re-run `evals/runners/agent_task_eval.py` against the new graph to
  measure any change in tool-selection behavior (a real, necessary
  verification step, not optional)

**Depends on:** Phase 0/1 stability. **Blocks:** any future agent-
trajectory evaluation work (a named gap, not yet built).

### Phase 3 — Retrieval & Memory
- Retrieval confidence/abstention gate (directly motivated by the
  negative-control finding already proven real in this project's own
  evaluation data)
- A real cross-encoder reranker (a genuinely free, CPU-based model
  such as `cross-encoder/ms-marco-MiniLM-L-6-v2` was the specific
  recommendation discussed in this project's own history, avoiding a
  new paid API dependency)
- A larger, more statistically powered retrieval ablation to more
  conclusively resolve ADR-7's "hybrid tied dense" open question

**Depends on:** Phase 2 not required, but logically related (both
improve answer quality).

### Phase 4 — Multi-tenancy
- Real per-user/tenant data isolation across corpus, conversation
  history, and rate limiting
- MUST be designed together with cross-tenant retrieval filtering
  (Section 14's RAG threat model row) — implementing tenancy without
  this in the same change would be a real, serious regression, not an
  improvement

**Depends on:** a real decision on whether AgentOS ever needs genuine
multi-user production use (currently a demo/portfolio project) —
this phase should not be started speculatively.

### Phase 5 — Durable Execution (WOE Integration)
- Integrate the separate Workflow Orchestration Engine project
  (currently ~90% complete) as AgentOS's durable execution backbone
- This is the real prerequisite for: durable/resumable multi-step
  agent runs surviving a worker crash mid-execution, time-travel/
  branch debugging, and genuine human-in-the-loop approval workflows
- Deliberately NOT built as a duplicate, smaller version of WOE
  within AgentOS itself — this was an explicit, real decision made
  during this project's history, not an oversight

**Depends on:** WOE reaching its own completion (tracked in WOE's own
repository, not this one). **Blocks:** genuine durable execution,
time-travel debugging, HITL — all currently named, deferred gaps.

### Phase 6 — Scale
- Horizontal worker scaling beyond Render's single-process model
- Real load testing beyond the current small-scale concurrent-request
  test (Section 16, item 5)
- Revisit ADR-1 (pgvector) only if a real, measured latency ceiling is
  actually hit — not preemptively

**Depends on:** Phase 4 (multi-tenancy) if the trigger is genuine
multi-user growth; otherwise deferred indefinitely.

---

## 33. Dependency-Aware PR Sequence (Phase 0 example, illustrative)

**PR-001: Fix Section 3 architecture diagram**
Files: `ENGINEERING_HANDOFF.md`. Migration: none. Tests: none
(documentation-only). Risk: none. Dependencies: none.

**PR-002: Resolve calculator eval() risk**
Files: `app/tools/calculator.py`, possibly a new dependency (a math-
expression parser library) if replacement is chosen over acceptance.
Migration: none. Tests: a new test file exercising both legitimate
expressions and known sandbox-escape attempt patterns. Risk: low if
replacing with a well-established parser library; changes tool
behavior, so should be verified against `evals/runners/agent_task_eval.py`'s
calculator-category tasks afterward. Dependencies: none.

**PR-003: Confirm and resolve short_term.py status**
Files: `app/memory/short_term.py` and whatever imports it (or
doesn't). Migration: none. Tests: none if removed; existing tests
should still pass either way. Risk: very low. Dependencies: none.

**PR-004: Require session-ownership token on document upload**
Files: `app/api/documents.py`. Migration: none (reuses existing
session_tokens infrastructure). Tests: a new test asserting upload
fails without a valid token. Risk: low-medium — this is a real
behavior change for any existing client code calling the upload
endpoint without a token; should be paired with a frontend update in
the same PR or a closely-following one. Dependencies: none (reuses
Phase 11's existing session-token work).

This handoff does not extend the PR sequence further than Phase 0,
consistent with this document's own instruction to "not propose 30
unrelated PRs" — Phases 1 through 6's PR breakdowns should be
produced at the point each phase is actually started, informed by
whatever has changed in the codebase by then.

# AgentOS Engineering Handoff — Part 5: Deployment, Frontend, Codebase Map, Principles, Final Snapshot

## 17. Deployment & Infrastructure

**Frontend hosting:** Vercel — `agent-os-weld.vercel.app`.
**Backend hosting:** Render — `agent-os-backend-v2.onrender.com`,
single web-service instance, `WEB_CONCURRENCY=1` (explicitly set by
Render based on available CPU on the instance tier, per real deploy
logs observed during this project's history).
**Database:** Neon (managed Postgres + pgvector), Launch plan (a real
upgrade made during this project's history, following the real BM25
quota incident, Section 11 bug #1).
**Cache/Queue:** Upstash (managed Redis), free tier, real spending
alert configured at $2.
**Object storage:** Cloudflare R2, free tier, real budget alert
configured at $2 (informational only — no hard cap exists on
Cloudflare, a real, accepted, researched limitation, see this
project's own history).
**Environment variables (backend, verified against real Render
config during this project's history):** `DATABASE_URL`,
`REDIS_URL`, `OPENROUTER_API_KEY`, `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `TAVILY_API_KEY`, `R2_ENDPOINT_URL`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` — 10
real variables, all currently in use except as noted (OpenTelemetry
being an unused dependency, Section 23).

**Failure behavior during real infrastructure events:**
- **Deploy:** Docker build (cached layers where content hasn't
  changed), then workers + reclaim loop start in `lifespan`.
- **Restart/crash:** in-flight queue jobs are recovered via
  `reclaim_loop` (Section 11 of Part 2) once the stale-job threshold
  (120s) passes — this is real, tested behavior, not theoretical.
  In-flight *agent conversations* (not queue jobs) have no equivalent
  recovery — a mid-agent-run crash simply loses that turn; this is
  the real, direct consequence of the "no durable agent execution"
  gap named throughout this document (Sections 1, 5, 32).
- **Database outage:** requests fail directly (memory reads/writes go
  through the same Postgres session as the request) — no fail-open
  behavior for the database layer, unlike most Redis usage.
- **Redis outage:** cache and rate-limiting fail open (requests
  proceed without caching/limiting); session-token verification fails
  closed (requests are correctly rejected) — this exact, real,
  intentional split is documented in Part 2, Section 10/11.
- **R2 outage:** uploads using R2 would fail; the local-path fallback
  (Section 9, Part 2) is gated on R2 being *unconfigured*, not on R2
  being *down* — **NOT VERIFIED** whether a live R2 outage (as
  opposed to missing credentials) is handled gracefully or simply
  surfaces as a real upload failure.
- **LLM (OpenRouter) outage:** real retry/backoff (Section 12, Part
  2) handles transient failures; a sustained outage would surface as
  a real chat failure after retries are exhausted — no fallback
  provider exists.

---

## 18. CI/CD

**Workflow:** `.github/workflows/ci.yml` — two real jobs, `test`
(backend, real Postgres service container with pgvector) and
`frontend-test` (Node 22, `npm test`). Both genuinely gate every
push, verified working in Section 11 (Part 3), bugs #7 and #8.

**Real, honest gap already self-corrected once:** this exact CI
configuration is the subject of one of this project's own most
important lessons (Section 11, Part 3) — the README claimed CI-gated
frontend tests for a real period of time before that claim was
actually true. **This document's own instructions require flagging
any current mismatch between documentation and implementation** — as
of this pass, README/CI are consistent (verified: both jobs exist and
both are referenced correctly in `README.md`).

**Secrets in CI:** `OPENROUTER_API_KEY` is referenced as a GitHub
Actions secret in the backend test job (`${{ secrets.OPENROUTER_API_KEY }}`)
— **NOT VERIFIED** in this pass whether backend tests requiring a real
LLM call actually run in CI (most LLM-touching tests use mocks per
the failure-injection test suite design, Section 15) or whether this
secret is genuinely exercised.

**Missing gates, real and explicit:** no security scanning, no
dependency-vulnerability scanning, no linting/type-checking gate
found in the CI workflow for either backend or frontend (frontend
`package.json` has a `lint` script, but **NOT VERIFIED** whether CI
actually runs it — the workflow's two jobs are named `test` and
`frontend-test`, not `lint`).

---

## 19. Observability

**Structured logging:** `app/observability/logging.py` — JSON-formatted
logs with `extra_fields`, used consistently across the queue/worker
system (job IDs, worker IDs, error details) — this is what made
several of the real bugs in Section 11 (Part 3) diagnosable in the
first place (e.g., bug #6, the silently-dying worker, was found via
code review anticipating exactly this kind of silent failure, and the
resulting log-and-continue fix relies on this same logging
infrastructure to actually be useful when it fires in production).

**Tracing:** Langfuse — as of Phase 11 (Section 2), correctly nested:
one parent "agent_run" observation per request, with the model
decision, tool execution (Section 6, Part 2 — tool calls specifically
gained tracing in this same phase, having previously had none at
all), and final response generation all appearing as one connected
trace tree.

**What is NOT captured:** OpenTelemetry (listed dependency, not
implemented — Section 23); no dedicated metrics/dashboard beyond
Langfuse's own UI and the benchmark scripts run manually; no alerting
system beyond the two informational billing alerts (Neon, Cloudflare)
covered in Section 17.

**Privacy-sensitive telemetry, explicitly flagged per this document's
rules:** Langfuse traces include real user chat messages and model
outputs — this is genuinely necessary for the tracing to be useful,
but represents real user data flowing to a third-party service.
**NOT VERIFIED** whether Langfuse's own data-handling terms were
reviewed, or whether any redaction/scrubbing exists before data is
sent.

---

## 20. Performance & Scaling — Measured vs. Expected vs. Unknown

| Metric | Status | Value/Source |
|---|---|---|
| Retrieval latency (real, hybrid search) | Measured (indirectly, via benchmark run durations) | Not isolated as its own metric in `BENCHMARKS.md` — bundled into overall LLM benchmark latency |
| LLM call latency (fast tier) | Measured | 2.34s avg (`gpt-4o-mini`, `BENCHMARKS.md`) |
| LLM call latency (strong tier) | Measured | 2.39s avg (`gpt-4o`, `BENCHMARKS.md`) — notably, only marginally slower than the fast tier, a real finding that reinforces ADR-6's routing justification |
| Queue enqueue throughput | Measured | 1.2 jobs/sec (`BENCHMARKS.md`) — the real, named P1 bottleneck (Section 23) |
| Queue processing throughput | Measured | 3.5 jobs/sec, 4 workers (`BENCHMARKS.md`) |
| Concurrent-request handling (warm) | Measured | 1.47-1.62s for 5 real simultaneous requests (`BENCHMARKS.md`) — small sample, real data |
| Concurrent-request handling (cold start) | Measured, but explicitly identified as a cold-start artifact, not steady-state performance | ~28s uniform cluster (`BENCHMARKS.md`) |
| Frontend performance (page load, bundle size, etc.) | UNKNOWN — not measured in any pass of this project | — |
| Database query performance at scale | UNKNOWN beyond the real HNSW-indexed 7,748-chunk corpus | — |

**Known bottleneck, already identified with a real number:**
sequential queue enqueue (Section 23, P1) is the one performance
characteristic in this codebase that is both measured AND already
identified as a real, specific limitation with a real fix path
(batching/pipelining).

---

## 22. Frontend Architecture

**Framework:** Next.js (App Router), TypeScript, Tailwind.

**Real structure, verified this pass — genuinely a single-page
application:** `frontend/app/page.tsx` (393 lines) is the entire UI —
there is no `components/` directory, no separate routes. Supporting
logic lives in `frontend/app/lib/` (`sse.ts` for SSE parsing,
`format.ts` for tool-call argument formatting), each with its own
test file.

**State management:** local React state only (`useState`) — session
ID, session token, messages, upload status, connection status, theme.
No external state management library; appropriate at this scale, but
worth naming explicitly since Section 27 (Part 4)'s scale analysis
would need to revisit this if the UI grows substantially.

**API integration:** direct `fetch()` calls to the backend's real
endpoints (`/agents/chat/stream`, `/documents/upload`, `/health`) —
no generated API client, no shared types between frontend and
backend (a real, if minor, contract-drift risk: a backend response
shape change would only surface as a frontend runtime bug, not a
compile-time one).

**SSE handling:** `parseSSEChunk()` in `lib/sse.ts`, real unit-tested
logic (7 tests) for parsing the backend's actual SSE event format
(session/tool_call/chunk/done events).

**Session handling:** `sessionId` and `sessionToken` held in React
state only — **explicitly NOT persisted** to localStorage or any
browser storage. This means a page refresh loses the session
entirely, requiring a fresh session/token pair on the next message.
**NOT VERIFIED** whether this is an intentional simplicity choice or
an overlooked gap; worth a deliberate decision either way, since it
directly affects real user experience (a refresh mid-conversation
loses history from the UI's perspective, even though the backend
still has it durably stored under the old session_id).

**Testing:** 3 test files (`sse.test.ts`, `format.test.ts`,
`page.test.tsx`), Vitest + React Testing Library, CI-gated (Section
18). Coverage is at the unit/component level — no end-to-end browser
test exists (a named gap, Section 15, Part 3).

**Architectural debt, real and specific:** the single-file `page.tsx`
structure was already flagged as a real, if not urgent, concern
during this project's own history — as more features accrete
(citations, execution traces, evaluation results), this file will
need to split into real components (`Chat`, `Message`, `ToolTrace`,
`UploadStatus`, etc.) before it becomes unwieldy. Not urgent at 393
lines; worth doing before it roughly doubles.

---

## 24. Codebase Map

    backend/
      app/
        agents/       LangGraph agent graph (model -> tool -> respond)
        api/           /agents (chat, streaming), /documents (upload), /health
        auth/           session-ownership tokens (Redis-backed, fails closed)
        cache/          idempotency + response caching (fails open)
        (database.py)   async SQLAlchemy engine/session setup
        llm/            OpenRouter client, retry/backoff, cost tracking
        memory/         persisted conversation history (Postgres);
                        short_term.py's live status NOT VERIFIED (Section 23)
        observability/  structured logging, Langfuse tracing
        queue/          Redis job queue, workers, checkpointing,
                        atomic (Lua) dequeue, stale-job reclaim
        ratelimit/      Redis-backed rate limiting (fails open)
        retrieval/      chunking, embeddings, hybrid search (BM25+vector+RRF);
                        pipeline.py is CONFIRMED DEAD CODE (Section 23)
        routing/        cost-aware model tier routing
        storage/        Cloudflare R2 client, local-path fallback
        tools/          retrieve, web_search, calculator (calculator's
                        eval() usage is a real, named P0 item, Section 23)
      alembic/          schema migrations, baselined against production
                        (only one migration exists so far, 0001)
      scripts/          corpus ingestion, HNSW index setup, load tests
      tests/            13 test files, real counts vary by function,
                        see Section 15 (Part 3)

    frontend/
      app/
        page.tsx        the entire UI (393 lines) — see Section 22
        lib/            sse.ts, format.ts + their test files
        layout.tsx, globals.css, favicon.ico

    evals/
      datasets/         real question/task sets for each evaluation
      runners/          6 independent evaluation scripts
      results/
        BENCHMARKS.md   canonical source of truth for every measured
                        result — see Section 16 (Part 3)

**Ownership boundaries and what future contributors should be careful
about, real and specific (not generic advice):**
- `app/retrieval/` — two retrieval code paths exist; only `hybrid.py`
  is live. Do not resurrect or import from `pipeline.py` without
  first confirming intent.
- `app/queue/redis_queue.py` — the atomic Lua-script dequeue and the
  checkpoint-after-commit ordering (Section 10-11, Part 2) are both
  real fixes for real, previously-shipped bugs. Any future refactor
  of this file should re-run the full queue test suite against real
  Upstash before being trusted.
- `app/auth/session_tokens.py` — the fail-closed behavior here is
  intentional and different from the rest of this codebase's Redis
  usage. Do not "normalize" its error handling to match the fail-open
  pattern used elsewhere without understanding why it's different.
- `frontend/app/page.tsx` — a single, large file by design so far;
  future growth should trigger the component split named in Section
  22, not indefinite growth of one file.

---

## 25. "Do Not Break This" — Invariants

Every invariant below is grounded in a real, documented bug or
architectural decision elsewhere in this handoff — not a generic
best-practice list.

1. **Checkpoint writes must happen only after a confirmed database
   commit, never before or concurrently.** (Section 11, Part 2;
   Section 11 bug #3, Part 3.)
2. **Queue dequeue (LPOP + in-flight marking) must remain atomic.**
   (Section 11, Part 2; Section 11 bug #2, Part 3.)
3. **Session-ownership token verification must fail CLOSED, never
   open, even if this means normalizing it would be "simpler."**
   (Section 10-11, Part 2; ADR-9, Part 4.)
4. **BM25 search must never load the `embedding` column.** (Section
   8.2, Part 2; Section 11 bug #1, Part 3 — this specific invariant
   already has a permanent regression test, `test_bm25_data_transfer.py`,
   guarding it directly.)
5. **R2 credentials must remain configured in any environment running
   more than one worker process** — the local-path fallback is
   development-safe, not multi-process-safe. (Section 9, Part 2.)
6. **The worker loop's outer exception handler must remain broad
   (catch-log-continue), not narrowed to specific exception types** —
   narrowing it would reintroduce the silent-death failure mode fixed
   in Section 11 bug #6 (Part 3).
7. **Retrieved document/web content must not be treated as trusted
   instructions** — currently true only by the absence of any
   write-capable tools; this invariant becomes actively
   security-critical the moment such a tool is added (Section 14,
   Part 3).
8. **A tracing/observability failure must never cause the underlying
   real operation (an LLM call, an agent run) to execute more than
   once as a side effect of error-handling.** (Section 11 bug #12,
   Part 3 — the exact failure mode this fixed.)

---

## 31. Production Readiness Checklist

| Category | Item | Status |
|---|---|---|
| Security | Authentication | PARTIAL (session-ownership only) |
| Security | Authorization/tenant isolation | FAIL (not implemented) |
| Security | Secrets management | PASS (env vars, not hardcoded) |
| Security | Prompt injection defense | FAIL (not implemented) |
| Security | Tool security | PARTIAL (calculator eval() unverified, Section 23 P0) |
| Security | File upload security | PARTIAL (validated but unauthenticated) |
| Reliability | Retries | PASS (LLM/embedding calls, real failure-injection tests) |
| Reliability | Idempotency | PASS (queue attempt tracking, chat response caching) |
| Reliability | Queue recovery | PASS (real, tested stale-job reclaim) |
| Reliability | Agent-run crash recovery | FAIL (no durable execution — deferred to WOE integration, Section 32) |
| Reliability | Graceful shutdown | PASS (`lifespan` context manager signals and awaits worker stop) |
| Data | Migrations | PASS (Alembic, baselined, though only 1 migration exists) |
| Data | Backups | UNKNOWN (Neon's own backup policy not independently verified in this pass) |
| Data | Deletion/retention | FAIL (no explicit conversation-deletion mechanism found) |
| Data | Consistency | PASS (real, tested commit-then-checkpoint ordering) |
| Performance | Load tests | PARTIAL (small-scale, real, but not at claimed production scale — Section 20) |
| Performance | Database indexes | PASS (real HNSW index, session_id index on conversation_messages) |
| Performance | Connection pools | PASS (SQLAlchemy async engine, standard pooling) |
| Observability | Logs | PASS (structured JSON) |
| Observability | Traces | PASS (Langfuse, connected full-run trees) |
| Observability | Metrics/alerts | PARTIAL (billing alerts only, no application-metric alerting) |
| Operations | Deployment | PASS (real, working Render/Vercel deploys) |
| Operations | Rollback | UNKNOWN (no explicit rollback procedure documented or tested) |
| Operations | Health checks | PASS (`/health/live`, `/health/ready`, real checks) |
| Operations | Incident response | UNKNOWN (no documented runbook) |

---

## 34. AI-Specific Engineering Principles (as applied in this codebase)

**LLMs are probabilistic components, never the final authorization
layer:** currently true by default (no write-capable tools exist),
but this principle should be treated as load-bearing the moment any
tool with real side effects is added — the model's decision to call a
tool should never be the only check before that tool executes.

**Tools are security boundaries:** partially honored — the
calculator's restricted `eval()` (Section 13, Part 3) is a real
attempt at this, of unverified adequacy; `retrieve` and `web_search`
have essentially no argument validation because their current
"blast radius" (a read-only query) is low. This should be revisited
before either tool gains write capability.

**Retrieved content is untrusted:** NOT currently enforced anywhere
in this codebase — a real, named gap (Section 14). Document and web
content flow directly into the LLM's context with no filtering.

**Model output is untrusted input:** partially honored — tool-call
arguments generated by the model are parsed (`json.loads`) but not
schema-validated beyond what the tool's own `to_openai_tool()`
description implies; a malformed or unexpected argument shape would
likely surface as a real runtime error rather than being caught
gracefully.

**Budgets are mandatory:** partially implemented — rate limiting
caps request volume; there is no explicit per-request token budget,
step-count budget (moot currently, given the single-tool-call graph),
or time budget beyond whatever the LLM provider's own timeout
enforces.

**Evaluation must accompany optimization:** genuinely, strongly
honored in this codebase's real history — every retrieval, routing,
and tool-description change of consequence in this project's timeline
was paired with a real before/after evaluation (Section 16, Part 3;
Section 11 bug #9, Part 3 is the clearest example). This is arguably
this codebase's single strongest real engineering habit, worth
explicitly preserving in any future contribution.

---

## 36. Instructions for Future AI Coding Agents

**Before changing code:**
1. Read this handoff in full (all 5 parts).
2. Read `README.md`, `ENGINEERING_LOG.md`, `evals/results/BENCHMARKS.md`.
3. Inspect the specific subsystem's code directly — this handoff
   cites real files and line counts, but code changes after this
   document was written; do not assume it is still accurate without
   checking.
4. Check `git log` for the subsystem's own recent history.
5. Identify which "Do Not Break This" invariants (Section 25) apply.
6. Only then modify code.

**When modifying code:**
- Preserve the invariants in Section 25 explicitly — if a change
  seems to require breaking one, that is a signal to stop and
  reconsider the approach, not a signal to proceed carefully.
- If touching retrieval, routing, or any tool description: re-run the
  relevant `evals/` runner before and after, and update
  `BENCHMARKS.md` if the measured result changes (this matches this
  project's own strongest real habit, named in Section 34).
- Avoid unrelated refactors in the same change as a real fix — this
  codebase's own git history (Section 2) shows a consistent pattern
  of small, focused, individually-tested commits; match that pattern.
- Do not add new infrastructure/dependencies without first checking
  whether existing infrastructure (Postgres, Redis, R2, already
  provisioned) can serve the same purpose — this exact discipline was
  explicitly applied when scoping the separate EvalOS project's own
  tech stack, reusing this project's existing Neon/Upstash/OpenRouter
  credentials rather than provisioning new services.

**Before declaring completion:**
- Real tests run and passing (not assumed).
- A regression test added for any bug fixed.
- Security implications considered, explicitly, not just functional
  correctness.
- `BENCHMARKS.md` updated if measured behavior changed.
- This handoff document updated if the change affects architecture,
  a capability-matrix row, or a named gap's status.

---

## 37. Contributor Checklist

    [ ] I understand the subsystem (read the relevant Part of this handoff).
    [ ] I checked the current code directly, not just this document.
    [ ] I checked git log for recent related changes.
    [ ] I identified which "Do Not Break This" invariants apply.
    [ ] I considered concurrency (event-loop/session issues like bug #11).
    [ ] I considered failure recovery (crash-consistency like bug #3).
    [ ] I considered security (like the calculator eval() question).
    [ ] I added/updated tests, run against REAL infrastructure where
        this codebase's own convention does so (not mocks alone).
    [ ] I re-ran the relevant eval/benchmark if I touched retrieval,
        routing, or a tool description.
    [ ] I updated BENCHMARKS.md if a measured number changed.
    [ ] I did not introduce infrastructure this project doesn't
        already have provisioned, without a real, discussed reason.
    [ ] I updated this handoff document if architecture changed.

---

## 38. Unknown / Unverified Items (consolidated from all 5 parts)

    - Exact embedding model string currently in use (Part 2, Section 8.1)
    - Whether chunk boundaries can split mid-sentence/mid-table (Part 2, Section 8.1)
    - Whether short_term.py is still live code or fully superseded (Part 2/4)
    - chat_sessions table's real prior existence (CONFIRMED absent, Part 3 — resolved, not open)
    - Whether failed ingestion jobs' R2 objects are cleaned up or intentionally left for retry (Part 2, Section 9)
    - Whether git history has ever contained an accidentally-committed secret (Part 3, Section 13)
    - Whether the calculator's eval() sandbox is actually exploitable (Part 3, Section 13/14) — flagged as real risk, not tested
    - Any PII-scrubbing before data reaches Langfuse (Part 5, Section 19)
    - Whether CI actually runs the frontend's lint script (Part 5, Section 18)
    - Neon's own backup/recovery policy and RTO (Part 5, Section 31)
    - Any documented incident-response runbook (Part 5, Section 31) — none found
    - OpenRouter-specific latency overhead versus calling providers directly (Part 4, ADR-5)
    - Exact chunk count at which HNSW query latency becomes a real problem (Part 4, Section 28)
    - Production traffic volume / real user count — this remains a demo/portfolio-stage project; NOT a claim of any specific real production traffic

---

## 39. If You Only Read One Section

**What AgentOS is:** a real, deployed FastAPI + LangGraph agent with
hybrid retrieval, a Redis job queue, session-ownership security, and
an unusually rigorous, honest evaluation practice for a project at
this stage.

**What works:** everything in the capability matrix (Part 1) marked
"Implemented" or "Implemented, measured" — genuinely verified against
real infrastructure, not just written and assumed.

**What has been proven:** 94.3% retrieval recall@3 at real scale
(132 docs), a real 41.5% cost saving from model routing, zero
cross-request contamination under real concurrent load, and a real
agent behavioral bug found and measurably fixed via evaluation
(0/10 -> 6/10).

**What has failed before, and been fixed:** 12 real, distinct bugs
(Part 3, Section 11), spanning a real production incident (database
quota exhaustion), two real crash-consistency bugs, and two genuine
test-infrastructure bugs — each with a documented cause, fix, and
verification.

**Biggest current risks:** no multi-tenancy (the single biggest named
gap); the calculator's unverified `eval()` sandbox (a real, specific
P0 security question); no durable agent-run execution (a mid-run
crash loses that turn, with no recovery — unlike the queue system,
which does recover).

**Biggest architectural strengths:** the fail-open/fail-closed
distinction across Redis usage (Section 10, Part 2) is deliberate and
correct; the evaluation-accompanies-every-optimization habit (Section
34) is this codebase's single most consistently well-executed
engineering practice across its entire real history.

**Most important next steps, in dependency order:** resolve Phase 0's
real security/correctness items (Part 4, Section 32) before anything
else; then either close AgentOS's own remaining capability gaps
(iterative tool use, retrieval confidence gating) or integrate WOE for
durable execution — a decision already deliberately deferred rather
than duplicated.

**What future contributors must not break:** the 8 invariants in
Section 25 (Part 5) — each one maps directly to a real bug this
project has already paid the cost of finding and fixing once.

---

## 40. Final System Snapshot

    AgentOS State
    Date:                 Aug 28, 2026 (this handoff's writing date)
    Repository:           github.com/rehan-khan-007/Agent-OS
    Branch:               main
    Commit:               a1e3e46 (at the start of this handoff's
                           writing; verify current HEAD before relying
                           on any file/line-count citation in this
                           document)
    Architecture:         FastAPI + LangGraph, single-process backend
    Backend:               Python, FastAPI, deployed on Render
    Frontend:               Next.js/TypeScript, deployed on Vercel,
                            genuinely single-page (page.tsx)
    Database:               PostgreSQL + pgvector (Neon, Launch plan)
    Cache/Queue:            Redis (Upstash, free tier)
    Storage:                Cloudflare R2 (free tier), local fallback
    LLM:                    OpenRouter gateway, heuristic-routed
                            (fast/strong tiers)
    Retrieval:               Hybrid (BM25 + pgvector, RRF-fused),
                            94.3% recall@3, HNSW-indexed
    Memory:                  Postgres-backed, session-scoped, no
                            explicit retention policy
    Authentication:          Session-ownership tokens (Redis, fails
                            closed); no user accounts
    Authorization:           Not implemented (no multi-tenancy)
    Observability:           Structured JSON logging + Langfuse
                            (connected full-run traces)
    Testing:                 75 backend tests + 20 frontend tests,
                            CI-gated
    Benchmarks:              6 independent, real, dated evaluations —
                            see evals/results/BENCHMARKS.md
    Deployment:              Live, real, both frontend and backend
    Known critical issues:   Calculator eval() sandbox unverified;
                            Section 3 diagram error (chat_sessions) —
                            both flagged, neither yet resolved as of
                            this snapshot
    Known high-priority
    issues:                  No multi-tenancy; no upload
                            authentication; sequential queue enqueue
    Next milestone:          Phase 0 (Section 32) — correctness and
                            security items — before any further
                            capability work
