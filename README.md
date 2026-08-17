# Agent-OS

Evaluation-Driven AI Agent Runtime.

A stateful agent system with LLM planning, tool orchestration, RAG, distributed execution, and LLM evaluation.

## Architecture

```
Agent-OS/
├── backend/       — FastAPI + agent runtime + RAG + evaluation
├── workers/       — Redis-backed async task workers
├── frontend/      — Next.js execution dashboard
├── evals/         — Benchmark datasets, metrics, and runners
└── docs/          — Architecture decisions and benchmarks
```

## Tech Stack

- **Runtime:** Python, LangGraph, FastAPI
- **Infrastructure:** PostgreSQL, Redis, Docker
- **Observability:** OpenTelemetry, Langfuse
- **Frontend:** TypeScript, Next.js, React

## Status

🚧 Active development.