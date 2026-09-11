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
