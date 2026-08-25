# AgentOS

An evaluation-driven AI agent runtime: a chat agent with real tool use
(document retrieval, web search, calculation), backed by a Redis job
queue, PostgreSQL/pgvector storage, Cloudflare R2 object storage, and a
test suite that runs against the real infrastructure it claims to
cover — not mocks.

**Live:** [agent-os-weld.vercel.app](https://agent-os-weld.vercel.app)
**Backend:** [agent-os-backend-v2.onrender.com](https://agent-os-backend-v2.onrender.com)

This README is the map. For the numbers, see
[`evals/results/BENCHMARKS.md`](evals/results/BENCHMARKS.md). For the
bugs found and fixed along the way, see
[`ENGINEERING_LOG.md`](ENGINEERING_LOG.md).

---

## What this actually is

A FastAPI backend running a LangGraph-based agent, a Next.js frontend,
and a set of real infrastructure pieces that back specific, testable
claims — every one of them verified against live production
infrastructure, not just written and assumed to work.

- **Hybrid retrieval** — BM25 keyword search fused with pgvector
  semantic search via Reciprocal Rank Fusion, over a real 132-document,
  4-domain corpus (quantum control, entrepreneurship, thermal
  engineering, personal finance) plus anything a user uploads through
  the app itself
- **Real tool use** — the agent can retrieve from its knowledge base,
  search the web, or run a calculator, deciding for itself which (if
  any) a question needs
- **Task queue** — Redis-backed job queue with real background
  workers, chunk-level checkpointing, atomic (Lua-script) dequeue, and
  automatic recovery if a worker dies mid-job
- **Object storage** — document uploads live in Cloudflare R2, not a
  single server's local disk, so ingestion works correctly even if the
  API server and a worker end up on different machines
- **Session-ownership tokens** — a leaked `session_id` alone can no
  longer be used to read or continue someone else's conversation
- **Resilience** — every external network call (LLM completions,
  embeddings) has retry/backoff on transient failures, verified with
  real failure-injection tests
- **Rate limiting** — Redis-backed, fails open if Redis itself has an
  outage, so a protective layer can't take the whole app down with it
- **Observability** — structured JSON logging and Langfuse tracing,
  connected into one full trace tree per agent run (model decision →
  tool execution → final response), not disconnected fragments
- **Schema migrations** — Alembic, baselined against the real
  production database
- **A real test suite** — 75 backend tests (pytest) + 20 frontend
  tests (Vitest), gated in CI on every push, run against real Redis
  and real Postgres wherever those are available to the runner
- **A real evaluation suite** — six independent evaluation runners
  (retrieval recall, retrieval ablation, 4-model comparison,
  LLM-as-judge grounding, agent tool-selection, model-router cost) —
  see the benchmarks doc for what each one actually found

## Architecture  \
frontend/ Next.js chat UI — dark/light theme, live tool-call
traces, session-token handling, upload status
backend/
app/
agents/ LangGraph agent graph (model -> tool -> respond)
api/ /agents (chat, streaming), /documents (upload), /health
auth/ session-ownership tokens (Redis-backed, fails closed)
cache/ idempotency + response caching (fails open)
database/ (database.py) async SQLAlchemy engine/session setup
llm/ OpenRouter client with retry/backoff, cost tracking
memory/ persisted conversation history (Postgres)
observability/ structured logging, Langfuse tracing
queue/ Redis job queue, workers, checkpointing,
atomic (Lua) dequeue, stale-job reclaim
ratelimit/ Redis-backed rate limiting (fails open)
retrieval/ chunking, embeddings (batched), hybrid search
(BM25 + vector + RRF), document loading
routing/ cost-aware model tier routing (fast vs strong)
storage/ Cloudflare R2 client (S3-compatible), with a
local-path fallback for environments without it
tools/ retrieve, web_search, calculator
alembic/ schema migrations, baselined against production
scripts/ corpus ingestion, HNSW index setup, load tests
tests/ 75 tests — unit, integration, failure-injection
evals/
datasets/ real question/task sets for each evaluation
runners/ 6 independent evaluation scripts
results/BENCHMARKS.md every measured result, with honest caveats\

## Running locally

**Backend:**
```bash
cd backend
pip install -r requirements.txt
# requires .env with OPENROUTER_API_KEY, DATABASE_URL, REDIS_URL
# optional: R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
# R2_BUCKET_NAME — uploads fall back to local paths if these aren't set
uvicorn app.main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

**Tests:**
```bash
cd backend && python3 -m pytest tests/ -v
cd frontend && npm test
```

Backend tests requiring real Redis or Postgres are automatically
skipped (not failed) if those aren't configured for the environment
they're run in — this keeps CI green without needing production
credentials, while still running for real wherever the infrastructure
is actually available.

**Database migrations:**
```bash
cd backend
alembic upgrade head       # fresh database
alembic revision --autogenerate -m "description"   # after a model change
```

## Evaluation suite

Each of these makes real, small, billed API calls against real
infrastructure — full methodology, exact numbers, and honest
limitations for every one are in
[`evals/results/BENCHMARKS.md`](evals/results/BENCHMARKS.md):

| Runner | What it measures |
|---|---|
| `benchmark.py` | Hybrid retrieval recall@3 across the full corpus |
| `retrieval_ablation.py` | BM25-only vs dense-only vs hybrid, isolated |
| `llm_benchmark.py` | Success/grounding/latency/cost across 4 real models |
| `grounding_judge_eval.py` | LLM-as-judge grounding, independent of the heuristic |
| `agent_task_eval.py` | Does the agent pick the right tool for a task |
| `model_router_benchmark.py` | Real cost savings from routing vs. always-strong |

## Known, honest gaps

This project doesn't overstate what's proven at scale:

- No dedicated cross-encoder reranker — hybrid retrieval uses
  Reciprocal Rank Fusion instead, a real, standard technique but a
  different mechanism than a separate reranking model
- The agent graph supports one tool-selection opportunity per turn
  (model → tool → respond), not iterative multi-hop tool use within a
  single turn
- Retrieval has no confidence/relevance gate — pgvector always returns
  its nearest neighbors, even when nothing in the corpus is actually
  relevant to the question (the eval dataset's negative control
  demonstrates this directly)
- No full user-account system — session-ownership tokens prevent a
  leaked `session_id` from being hijacked, but there's no login,
  signup, or per-user data isolation
- Grounding evaluation's LLM judge shares a model family with the
  answer-generation model in that benchmark — a real, named limitation
  of LLM-as-judge methodology generally, not specific to this project

Full details and reasoning for each are in `evals/results/BENCHMARKS.md`.

## Related projects

AgentOS is designed to eventually sit alongside two companion
projects, each with a distinct responsibility:

- **Workflow Orchestration Engine** — a separate, general-purpose
  distributed workflow engine (DAG scheduling, durable state, worker
  leases, crash recovery) intended to eventually become AgentOS's
  execution backbone for durable, resumable agent runs — deliberately
  not yet integrated, so as not to duplicate work already well underway
  there
- **EvalOS** — a planned, separate evaluation and benchmarking
  framework; several of its intended techniques (LLM-as-judge
  grounding, retrieval ablation) are already implemented directly in
  this repo's `evals/` directory
