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


---

## Full Documentation

This is the 5-minute-read summary. For real depth on any subsystem,
see the linked documents below — each is a self-contained deep dive,
not a continuation you need to read in order unless you want the
full picture.

- [`docs/handoff/01-timeline-and-architecture.md`](docs/handoff/01-timeline-and-architecture.md)
  — Full 12-phase project timeline (grounded in the real 75-commit
  git history) and the current system architecture diagram.
- [`docs/handoff/02-components.md`](docs/handoff/02-components.md)
  — Component-by-component deep dive: API layer, agent runtime, tool
  system, memory, retrieval/RAG, document storage, database, Redis/
  queue, and the LLM layer.
- [`docs/handoff/03-failures-security-testing.md`](docs/handoff/03-failures-security-testing.md)
  — The full failure history (all 12 real bugs found and fixed, with
  extracted engineering lessons), the security threat model, testing
  strategy, and a summary of every real benchmark.
- [`docs/handoff/04-decisions-debt-risks-roadmap.md`](docs/handoff/04-decisions-debt-risks-roadmap.md)
  — 9 real architectural decisions (ADR-style), the technical debt
  inventory, risk analysis at scale, the architectural ceiling, and a
  dependency-aware roadmap.
- [`docs/handoff/05-deployment-frontend-principles.md`](docs/handoff/05-deployment-frontend-principles.md)
  — Deployment/infrastructure, CI/CD, observability, frontend
  architecture, the full codebase map, "Do Not Break This" invariants,
  the production-readiness checklist, contributor instructions, and
  the final system snapshot.

Also see [`ENGINEERING_LOG.md`](ENGINEERING_LOG.md) (bug-focused,
interview-oriented) and [`evals/results/BENCHMARKS.md`](evals/results/BENCHMARKS.md)
(the canonical source of truth for every measured number).
