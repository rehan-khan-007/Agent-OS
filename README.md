# AgentOS

An evaluation-driven AI agent runtime: a chat agent with real tool use (document retrieval, web search, calculation), backed by a Redis job queue, PostgreSQL/pgvector storage, and a test suite that actually runs against the real infrastructure it claims to cover.

**Live:** [agent-os-weld.vercel.app](https://agent-os-weld.vercel.app)

## What this actually is

A FastAPI backend running a LangGraph-based agent, a Next.js frontend, and a set of real infrastructure pieces that back specific, testable claims:

- **Hybrid retrieval** — BM25 keyword search fused with pgvector semantic search via Reciprocal Rank Fusion, over documents you upload through the app itself
- **Task queue** — Redis-backed job queue with real background workers, chunk-level checkpointing, and automatic recovery if a worker dies mid-job
- **Resilience** — every external network call (LLM completions, embeddings) has retry/backoff on transient failures, verified with real failure-injection tests
- **Rate limiting** — Redis-backed, fails open if Redis itself has an outage, so a protective layer can't take down the whole app
- **Observability** — structured JSON logging, Langfuse tracing
- **A real test suite** — 68 backend tests (pytest) + 20 frontend tests (Vitest), gated in CI on every push

See [`evals/results/BENCHMARKS.md`](evals/results/BENCHMARKS.md) for actual measured numbers (a 200-job queue load test, a 4-model LLM comparison) — including their honest limitations, not just the headline figures.

## Architecture

```
frontend/ Next.js chat UI — dark theme, live tool-call traces, real health check
backend/
app/
agents/ LangGraph agent definition and tool-calling loop
tools/ retrieve, web_search, calculator
retrieval/ chunking, embeddings (batched), hybrid search (BM25 + vector + RRF)
queue/ Redis job queue, workers, checkpointing, stale-job reclaim
ratelimit/ Redis-backed rate limiting (fails open)
cache/ idempotency + response caching (fails open)
llm/ OpenRouter client with retry/backoff
observability/ structured logging
tests/ 68 tests — unit, integration, and failure-injection
scripts/ load_test_queue.py — real queue throughput/reliability test
evals/
datasets/ retrieval eval question set
runners/ retrieval recall benchmark, 4-model LLM benchmark
results/ BENCHMARKS.md — actual measured results 
## Running locally

**Backend:**
```bash
cd backend
pip install -r requirements.txt
# requires .env with OPENROUTER_API_KEY, DATABASE_URL, REDIS_URL
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

Backend tests requiring real Redis are automatically skipped (not failed) if `REDIS_URL` isn't configured for the environment they're run in — this is intentional so CI stays green without needing production credentials, while still running for real wherever Redis is actually available.

## Known, honest gaps

This project doesn't overstate what's proven at scale:

- The document corpus is currently small (a handful of real papers) — the upload feature works for any document a user brings, but hasn't been stress-tested at hundreds of documents
- Grounding evaluation in the LLM benchmark is a vocabulary-overlap heuristic, not an LLM-as-judge score
- No dedicated reranker model — hybrid retrieval uses Reciprocal Rank Fusion instead, which is a real, standard technique but a different mechanism than a separate reranking model

Full details in `evals/results/BENCHMARKS.md`.
