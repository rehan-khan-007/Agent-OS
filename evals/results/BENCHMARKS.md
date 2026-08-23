# AgentOS Benchmark Results

Real numbers from running the actual production code against real
infrastructure — not estimates. Each result below states exactly
what was measured, how, and what its limitations are.

---

## Task queue load test

**What was measured:** the real Redis-backed job queue and worker
infrastructure (`app/queue/redis_queue.py`, `app/queue/worker.py`) —
the actual production code, not a mock. The job handler used was a
stub that makes no external API calls, so this measures the
orchestration layer itself (enqueue, dequeue, worker concurrency,
status tracking, failure handling), not answer quality.

**How:** `backend/scripts/load_test_queue.py`, run against the real
production Upstash Redis instance. 200 jobs enqueued, 4 deliberately
set (2%) to fail on purpose so the completion rate is genuinely
earned rather than assumed. 4 concurrent workers processed the queue.

**Result (Aug 23, 2026):**

| Metric | Value |
|---|---|
| Total jobs | 200 |
| Completed (done) | 196 |
| Failed (deliberately injected) | 4 |
| Unresolved / timeout | 0 |
| Completion rate (reached a terminal state) | 100.0% |
| Success rate (excluding deliberate failures) | 98.0% |
| Enqueue throughput | 1.2 jobs/sec (sequential, real network round-trips to Upstash) |
| Processing throughput | 3.5 jobs/sec (4 workers) |

**Honest caveats:**
- Enqueueing is sequential (one job at a time), not batched/pipelined
  — the real bottleneck here is network round-trip latency to
  Upstash, not the queue logic itself. A genuinely high-throughput
  system would batch/pipeline writes; this is a known next
  optimization, not something already done.
- This measures orchestration reliability, not LLM answer quality —
  a separate, real question answered by the benchmark below.
- Found and fixed two real bugs while building this test: a
  `MaxConnectionsError` from checking 200 job statuses concurrently
  without a concurrency cap, and a worker that could die silently on
  an unexpected Redis exception (see git history — both fixed with
  accompanying tests before this number was produced).

---

## 4-model LLM benchmark

**What was measured:** success rate, a grounding heuristic, latency,
and real dollar cost across 4 real models, each answering the same
14 real questions with the same retrieved context (retrieval ran
once per question, shared across all 4 models, so generation quality
is the only variable being compared).

**How:** `evals/runners/llm_benchmark.py`, using the project's
existing 14-question retrieval eval dataset and the real
`hybrid_search` pipeline (BM25 + vector search fused via Reciprocal
Rank Fusion). Cost computed from each response's actual `usage`
field (real token counts), not estimated.

**Result (Aug 23, 2026):**

| Model | Success | Grounded* | Avg Latency | Total Cost |
|---|---|---|---|---|
| openai/gpt-4o-mini | 100.0% | 100.0% | 2.72s | $0.00236 |
| openai/gpt-4o | 100.0% | 100.0% | 2.44s | $0.04265 |
| anthropic/claude-haiku-4.5 | 100.0% | 100.0% | 3.97s | $0.03141 |
| google/gemini-3.7-flash | 100.0% | 100.0% | 5.85s | $0.01136 |

**Total cost for this run: $0.0878** (56 completions + 14 embedding calls)

**Honest caveats:**
- \*Grounding is a labeled heuristic (vocabulary overlap between the
  retrieved context and the answer), not an LLM-as-judge evaluation.
  It's directional evidence the model used the retrieved context, not
  a rigorous grounding score — a true judge-based eval would cost
  more (an extra LLM call per answer) and wasn't run here.
- Small dataset (14 questions, 3-document corpus) — a genuinely
  larger, more diverse question set and corpus would be needed for
  results that generalize beyond this specific paper collection.
- **Interesting real findings, not assumptions:** gpt-4o was the
  fastest model despite being by far the most expensive; gpt-4o-mini
  offered ~18x lower cost than gpt-4o with comparable grounding and
  similar latency, which is the actual empirical basis for using it
  as the default ("fast") tier in `app/routing/policies.py`.

---

## What these results do and don't support

These two benchmarks together give real evidence that:
- the queue/worker infrastructure reliably processes concurrent jobs
  and correctly reports success/failure
- hybrid retrieval produces context that multiple different LLMs can
  ground answers in

They do **not** by themselves demonstrate performance at the scale
of hundreds of concurrent users, a large multi-hundred-document
corpus, or long-running multi-step agent workflows — those remain
open, named gaps rather than assumed strengths.
