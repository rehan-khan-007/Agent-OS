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
