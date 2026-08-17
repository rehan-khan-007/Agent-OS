# Agent-OS

Evaluation-Driven AI Agent Runtime.

A stateful agent system with LLM planning, tool orchestration, RAG, distributed execution, and LLM evaluation.

## Quickstart

```bash
git clone git@github.com:rehan-khan-007/Agent-OS.git
cd Agent-OS
make dev
```

## Architecture

```
Agent-OS/
├── backend/       — FastAPI + agent runtime + RAG + evaluation
│   └── app/
│       ├── api/       — REST endpoints (/health)
│       ├── agents/    — Agent runtime (LangGraph)
│       ├── tools/     — Tool interfaces
│       ├── retrieval/ — RAG pipeline
│       └── memory/    — Agent memory
├── workers/       — Redis-backed async task workers
├── frontend/      — Next.js execution dashboard (WIP)
├── evals/         — Benchmark datasets, metrics, runners
└── docs/          — Architecture decisions and benchmarks
```

## Tech Stack

- **Runtime:** Python, LangGraph, FastAPI
- **Infrastructure:** PostgreSQL, Redis, Docker
- **Observability:** OpenTelemetry, Langfuse
- **Frontend:** TypeScript, Next.js, React

## Status

🚧 Active development.