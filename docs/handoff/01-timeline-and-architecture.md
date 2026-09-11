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


---

## 3. Current Architecture

    Next.js UI (Vercel)
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

  Cross-cutting: Langfuse tracing (full agent-run trees),
  structured JSON logging, Alembic migrations

This reflects the actual architecture as of commit f55ea27 — see
