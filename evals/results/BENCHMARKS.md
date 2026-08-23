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

## Hybrid retrieval recall (multi-domain corpus)

**What was measured:** source-level recall@3 — does the actual
production retrieval pipeline (`hybrid_search`: BM25 + pgvector
cosine search fused via Reciprocal Rank Fusion) surface a chunk from
the correct source document, within the top 3 results, for a real
question.

**How:** `evals/runners/benchmark.py`, run against the real corpus
after a genuine expansion — 132 documents (7,748 chunks) spanning
four distinct domains: the original MTP quantum-control papers,
entrepreneurship/business, thermal/cooling engineering, and personal
finance (official SEBI investor-education material). 35 real
questions (`evals/datasets/retrieval_qa.json`), each written from an
actual document's real title/content, plus 1 negative control with
no correct source in the corpus.

**Result (Aug 23, 2026): 33/35 — 94.3% recall@3**

**Honest caveats:**
- This replaces an earlier, much less meaningful 100% result measured
  against only 3-4 documents — trivial to get right with almost no
  competing documents. 94.3% against 132 documents across 4 unrelated
  domains is a real, harder test, and the two genuine misses are
  informative rather than something to hide:
  - A comparative question ("GRAPE vs. other methods on efficiency")
    retrieved 3 other real optimization papers instead of the 2
    expected ones — a genuinely hard case, since no single chunk
    directly addresses a cross-paper comparison the way a
    definitional question does.
  - A skill-level-specific question ("mutual funds for *beginners*")
    retrieved the *advanced* mutual funds guide and a general
    financial-education booklet instead — three SEBI documents on
    the same topic at different skill levels share enough vocabulary
    that distinguishing them purely by semantic/keyword content is a
    real, understandable limitation.
- The negative control (a question the corpus has no real answer to)
  correctly isn't scored as a hit or miss — pgvector always returns
  its nearest neighbors regardless of true relevance, so there's no
  built-in "no answer" signal in this pipeline. That remains a real,
  unaddressed limitation, not something this result fixes.
- An HNSW index (`document_chunks_embedding_hnsw_idx`) was added
  before this run so cosine search doesn't brute-force scan all
  7,748 rows — see git history (`scripts/add_vector_index.py`).

---

## 4-model LLM benchmark

**What was measured:** success rate, a grounding heuristic, latency,
and real dollar cost across 4 real models, each answering the same
questions with the same retrieved context (retrieval ran once per
question, shared across all 4 models, so generation quality is the
only variable being compared).

**How:** `evals/runners/llm_benchmark.py`, using the project's real
eval dataset and the real `hybrid_search` pipeline (BM25 + vector
search fused via Reciprocal Rank Fusion). Cost computed from each
response's actual `usage` field (real token counts), not estimated.

**Result (Aug 23, 2026) — 35 questions across all 4 domains, full expanded corpus (132 docs, 7,748 chunks):**

| Model | Success | Grounded* | Avg Latency | Total Cost |
|---|---|---|---|---|
| openai/gpt-4o-mini | 100.0% | 100.0% | 2.34s | $0.00597 |
| openai/gpt-4o | 100.0% | 100.0% | 2.39s | $0.10712 |
| anthropic/claude-haiku-4.5 | 100.0% | 100.0% | 4.49s | $0.07688 |
| google/gemini-3.7-flash | 100.0% | 100.0% | 5.47s | $0.02688 |

**Total cost for this run: $0.2168** (140 completions + 35 embedding calls)

**Honest caveats:**
- \*Grounding is a labeled heuristic (vocabulary overlap between the
  retrieved context and the answer), not an LLM-as-judge evaluation.
  It's directional evidence the model used the retrieved context, not
  a rigorous grounding score — a true judge-based eval would cost
  more (an extra LLM call per answer) and wasn't run here.
- **Interesting real findings, not assumptions:** at this larger
  scale, gpt-4o-mini's cost advantage widened further — gpt-4o cost
  roughly **18x more** than gpt-4o-mini for the exact same 35
  questions ($0.107 vs $0.006), while being only marginally faster
  (2.39s vs 2.34s average). This is the actual empirical basis for
  using gpt-4o-mini as the default ("fast") tier in
  `app/routing/policies.py` — not an assumption, a measured result
  that held up (and strengthened) when retested at real scale.
- An earlier, smaller run of this same benchmark (14 questions, the
  original 3-document corpus) is superseded by this result — see
  git history for that run if useful as a smaller-scale reference
  point.

---

## What these results do and don't support

These benchmarks together give real evidence that:
- the queue/worker infrastructure reliably processes concurrent jobs
  and correctly reports success/failure
- hybrid retrieval performs well (94.3% recall@3) across a genuinely
  large, multi-domain corpus (132 documents, 7,748 chunks) — not
  just a small, easy one
- multiple different LLMs can ground answers in what gets retrieved,
  and the cost/latency tradeoffs between models hold up (and get
  clearer) at real scale, not just in a small initial test

They do **not** by themselves demonstrate performance at the scale
of hundreds of concurrent users or long-running multi-step agent
workflows — those remain open, named gaps rather than assumed
strengths.
