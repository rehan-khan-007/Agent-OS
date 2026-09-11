# 5. Product, Technical Debt & Roadmap

## 5.1 Frontend
* Next.js/TypeScript, genuinely single-page:
  `frontend/app/page.tsx` (393 lines) IS the entire UI. No
  `components/` dir, no separate routes. Supporting logic:
  `frontend/app/lib/` (`sse.ts`, `format.ts`), each with real tests.
* **State:** local React state only (`useState`) — session ID/token,
  messages, upload status, connection status, theme. No external
  state library.
* **API integration:** direct `fetch()` calls, no generated client,
  no shared types with backend — a real (minor) contract-drift risk.
* **Session persistence:** `sessionId`/`sessionToken` held in React
  state ONLY — explicitly NOT persisted to localStorage. A page
  refresh loses the session entirely from the UI's perspective (the
  backend still has the history durably stored under the old
  session_id). NOT VERIFIED whether intentional or an oversight.
* **Real architectural debt, already named:** the single-file
  `page.tsx` structure should split into real components (`Chat`,
  `Message`, `ToolTrace`, `UploadStatus`) before it roughly doubles
  in size. Not urgent at 393 lines.

## 5.2 Codebase Map
```text
backend/app/
  agents/       LangGraph graph (model -> tool -> respond)
  api/          /agents (chat, streaming), /documents (upload), /health
  auth/         session-ownership tokens (Redis, fails closed)
  cache/        idempotency + response caching (fails open)
  llm/          OpenRouter client, retry/backoff, cost tracking
  memory/       persisted conversation history (Postgres);
                short_term.py's live status NOT VERIFIED
  observability/  structured logging, Langfuse tracing
  queue/        Redis job queue, workers, atomic dequeue, reclaim
  ratelimit/    Redis-backed rate limiting (fails open)
  retrieval/    chunking, embeddings, hybrid search;
                pipeline.py CONFIRMED DEAD CODE
  routing/      cost-aware model tier routing
  storage/      Cloudflare R2 client, local-path fallback
  tools/        retrieve, web_search, calculator (eval()-based,
                real P0 item)
alembic/        migrations, baselined (only 1 migration exists)
scripts/        ingestion, HNSW setup, load tests
tests/          13 files, 75 test functions

frontend/app/
  page.tsx      the entire UI (393 lines)
  lib/          sse.ts, format.ts + tests

evals/
  datasets/     real question/task sets
  runners/      6 independent evaluation scripts
  results/BENCHMARKS.md   canonical source of truth for every result
```

## 5.3 Technical Debt

**P0 — Correctness/Security:**
* Calculator's restricted `eval()` — not verified safe against known
  Python sandbox-escape techniques. Fix: replace with a real
  math-expression parser, or explicitly test/accept the risk.

**P1 — Real production blockers if usage grows:**
* No multi-tenancy — shared corpus/rate limits across all users.
* No upload authentication — anyone can pollute the shared corpus.
* Sequential (non-batched) Redis enqueue — measured 1.2 jobs/sec
  bottleneck.

**P2 — Real engineering debt:**
* `pipeline.py` dead code — delete or clearly mark deprecated.
* Pricing tables duplicated across eval scripts — consolidate.
* `short_term.py` live-usage status unconfirmed.
* No conversation-history length cap — unbounded context-window
  growth risk, currently unobserved in practice.
* OpenTelemetry listed as a dependency but never implemented.

**P3 — Quality-of-life:**
* No regression test for the "duplicate agent call" risk (already
  fixed, but unguarded against a future regression) — a tracing
  failure AFTER a successful `agent.ainvoke()` could, in an earlier
  draft, have caused the real call to run twice; fixed via a
  `result is None` exactly-once guard, never shipped to production,
  but no dedicated test protects this fix going forward.
* No document-level content-hash dedup on re-ingestion.

## 5.4 Architectural Risks at Scale
| Scale | Real risk |
|---|---|
| 10-100 users | Fine — matches real measured costs/throughput in BENCHMARKS.md |
| 1,000 users | Shared corpus + no multi-tenancy = retrieval quality degrades as unrelated users' documents mix — a CORRECTNESS problem, not just scale. Sequential enqueue becomes a real bottleneck. |
| 10,000 users | Multi-tenancy becomes a hard requirement. Single Redis instance may need purpose-splitting. Single-Render-process model needs real horizontal worker scaling — this is where the R2 local-fallback safety (5.1/3.6) becomes load-bearing, not theoretical. |
| 100,000 users | Requires genuine redesign: real multi-tenancy, dedicated vector DB reconsideration, horizontally scaled workers, almost certainly the WOE integration for durable execution. |

**Explicitly: no Kubernetes/Kafka/microservices/Elasticsearch
recommendation at any of these scales** — none of this project's real
usage has approached even the 1,000-user row, and the real
bottlenecks identified don't require those technologies to solve.

## 5.5 Production Readiness Checklist
| Category | Item | Status |
|---|---|---|
| Security | Authentication | PARTIAL (session-ownership only) |
| Security | Authorization/tenant isolation | FAIL |
| Security | Tool security | PARTIAL (calculator eval() unverified) |
| Reliability | Queue recovery | PASS (real, tested) |
| Reliability | Agent-run crash recovery | FAIL (no durable execution) |
| Data | Migrations | PASS (Alembic, baselined) |
| Data | Deletion/retention | FAIL (no mechanism found) |
| Performance | Load tests | PARTIAL (small-scale, real, not production-scale) |
| Observability | Traces/logs | PASS |
| Observability | Alerts | PARTIAL (billing alerts only) |
| Operations | Health checks | PASS (real `/health/live`, `/health/ready`) |
| Operations | Rollback/incident response | UNKNOWN (no documented runbook) |

## 5.6 Roadmap (dependency-aware, not a wishlist)
* **Phase 0 (no dependencies, do first):** fix calculator eval()
  risk; confirm short_term.py status; add upload authentication.
* **Phase 1 (Production Foundation):** conversation-history cap;
  consolidate pricing tables; content-hash dedup; batch queue
  enqueue.
* **Phase 2 (Agent Runtime — biggest capability gap):** add a real
  loop-back edge `tools -> model` in the LangGraph graph for genuine
  multi-step tool use; re-run `agent_task_eval.py` afterward
  (mandatory, not optional).
* **Phase 3 (Retrieval & Memory):** confidence/abstention gate; a
  real cross-encoder reranker (free, CPU-based option already
  identified); a larger, more statistically powered ablation to
  conclusively resolve the "hybrid tied dense" open question.
* **Phase 4 (Multi-tenancy):** MUST be designed together with
  cross-tenant retrieval filtering in the SAME change — implementing
  tenancy without this would be a real regression, not an
  improvement. Don't start speculatively — only on a real multi-user
  requirement.
* **Phase 5 (Durable Execution / WOE Integration):** the real
  prerequisite for durable/resumable agent runs, time-travel
  debugging, genuine HITL. Deliberately not duplicated smaller inside
  AgentOS. Depends on WOE's own completion (tracked separately).
* **Phase 6 (Scale):** horizontal worker scaling; real load testing
  beyond current small-scale tests; revisit pgvector only if a real,
  measured latency ceiling is actually hit.

## 5.7 Remaining Real Invariants (not covered in files 01-03)
* Tool descriptions ARE behavior — any edit requires re-running
  `agent_task_eval.py` (see file 01).
* Retrieved document/web content must never be treated as trusted
  instructions — currently true by the absence of write-capable
  tools; becomes actively security-critical the moment one is added.
* A tracing/observability failure must never cause a real operation
  (an LLM call, an agent run) to execute more than once as a side
  effect of its own error-handling.

## 5.8 Final System Snapshot
```text
Date:              Aug 28, 2026 (this doc set's writing date)
Repo:               github.com/rehan-khan-007/Agent-OS, branch main
Backend:            FastAPI, Python, Render
Frontend:           Next.js/TypeScript, Vercel, single-page
Database:           PostgreSQL + pgvector (Neon, Launch plan)
Cache/Queue:        Redis (Upstash, free tier)
Storage:            Cloudflare R2 (free tier), local fallback
LLM:                OpenRouter, heuristic fast/strong routing
Retrieval:          Hybrid (BM25+pgvector, RRF), 94.3% recall@3
Auth:               Session-ownership tokens; no user accounts
Testing:            75 backend + 20 frontend tests, CI-gated
Known critical:     Calculator eval() unverified (P0)
Known high-pri:     No multi-tenancy; no upload auth; sequential
                    queue enqueue
Next milestone:     Phase 0 (5.6) before any further capability work
```
