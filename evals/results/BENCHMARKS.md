# AgentOS Benchmark Results

Real numbers from running the actual production code against real
infrastructure — not estimates. Each result below states exactly
what was measured, how, and what its limitations are.

## At a glance

| Metric | Result |
|---|---|
| Task queue reliability (200 jobs) | 100% terminal completion, 98% success excl. injected failures |
| Retrieval recall@3 (132 docs, 4 domains) | **94.3%** |
| Retrieval ablation | BM25 91.4% / Dense 94.3% / Hybrid 94.3% |
| Cheapest model vs strongest (same task) | gpt-4o-mini **18x cheaper** than gpt-4o, near-identical latency |
| LLM-judge grounding | **100%** (35/35), independently confirms the heuristic score |
| Model router real savings | **41.5%** vs. an always-strong baseline, 12/12 routing decisions correct |
| Agent tool-selection accuracy | 86.7% (26/30) — includes a real bug found and fixed live (0/10 → 6/10 on one category) |
| Live concurrent-request safety | 0 cross-request contamination across two real test runs |
| Total real spend across every benchmark below | **under $0.30** |

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

## LLM-as-judge grounding evaluation

**What was measured:** a genuine LLM-as-judge assessment of grounding
— replacing the vocabulary-overlap heuristic used in the 4-model
benchmark above with a real, separate model call that judges whether
each generated answer is actually supported by its retrieved context,
with structured reasoning, not a word-overlap guess.

**How:** `evals/runners/grounding_judge_eval.py`. For each of the 35
real questions: retrieve context via the real `hybrid_search`
pipeline, generate an answer (gpt-4o-mini — the model this project's
own 4-model benchmark showed has the best cost/performance ratio),
then have a separate gpt-4o-mini call judge whether that answer is
genuinely grounded, returning structured JSON with reasoning.

**Result (Aug 23, 2026): 35/35 — 100.0% judged grounded**

**Total cost for this run: $0.0119** (70 completions + 35 embedding calls)

**Honest caveats:**
- This confirms, rather than merely repeats, the heuristic's earlier
  finding — a real judge model reasoning about each answer
  independently reached the same conclusion the vocabulary-overlap
  heuristic did, which is meaningful corroboration, not just a second
  measurement of the same thing.
- Worth noting honestly: several answers were judged grounded
  specifically *because* they correctly said the context didn't
  contain enough detail to answer more specifically, rather than
  fabricating an answer — a real, positive signal about the system
  under uncertainty, not just accuracy when a clear answer exists.
- A single judge model (not a panel/ensemble) — a real limitation of
  LLM-as-judge methodology generally, not specific to this project.
  The judge could in principle share blind spots with the answering
  model, since both are the same model here.

---

## Live concurrent-request test

**What was measured:** whether the live, deployed backend (a single
Render worker process, `WEB_CONCURRENCY=1`) correctly handles
multiple real, simultaneous requests — specifically checking for
cross-request state contamination, not just "does it survive load."
Each of N concurrent requests asks for a unique number and expects a
never-repeated session ID back; any crossover between concurrent
requests would indicate a real state-leak bug in the async code, not
just a performance limitation.

**How:** `backend/scripts/concurrent_load_test.py`, firing real
simultaneous HTTP requests at the live `/agents/chat` endpoint — real
LLM calls, real network round-trips to the actual deployed instance,
not a local or mocked test.

**Result (Aug 24, 2026) — two runs, both genuinely informative:**

*Run 1 (15 requests):* 15/15 succeeded, but all completed within a
tight 28.09–28.28s cluster — a strong signal this was actually
measuring Render's free-tier cold-start wake-up (the instance had
been idle), not real concurrent-processing latency.

*Run 2 (15 requests, ~1 minute later):* 5/15 succeeded in a genuinely
fast, tight 1.47–1.62s — real warm-instance concurrent latency. The
other 10/15 were correctly rejected with `429 Rate limit exceeded`.

**Cross-contamination check: PASSED in both runs** — every successful
response had a unique session ID and the correct answer for its own
request, with zero crossover, even in the mixed
success/failure batch.

**Honest read of what actually happened:**
- Run 1's uniform 28s cluster wasn't a bug — it was Render's cold
  start being (accidentally) load-tested for the first time all
  session, and it held up fine: all 15 requests eventually completed
  correctly once the instance woke up.
- Run 2's 10 rejections weren't a bug either — the two runs
  landed in the same 5-minute rate-limit window (20 requests/5min
  per IP), so the limiter built and tested earlier this session
  caught real, unplanned overlapping traffic in the wild and did
  exactly what it was designed to do.
- Genuinely useful, unplanned confirmation: this wasn't a scripted
  test of the rate limiter — it tripped for real, on its own, during
  a test aimed at something else entirely, and behaved correctly.

---

## Retrieval ablation (dense vs BM25 vs hybrid)

**What was measured:** source-level recall@3 for three retrieval
methods run in isolation — BM25-only, dense (pgvector cosine)-only,
and the production hybrid (RRF-fused) pipeline — against the same
35-question dataset used elsewhere in this document. Directly closes
a real, named gap: the earlier 94.3% hybrid result had no baseline
to compare against.

**How:** `evals/runners/retrieval_ablation.py`, calling the actual
production retrieval functions (`_vector_search_ranked`, `bm25_search`,
`hybrid_search`) in isolation rather than reimplementing retrieval
logic for the comparison.

**Result (Aug 24, 2026):**

| Method | Recall@3 |
|---|---|
| BM25-only | 32/35 (91.4%) |
| Dense-only | 33/35 (94.3%) |
| Hybrid (RRF) | 33/35 (94.3%) |

**Honest read of this result, not spun toward the expected answer:**
- BM25 alone clearly trails both vector-based methods — a real,
  meaningful ~3-point gap.
- Hybrid did **not** outperform dense-only on this dataset — they
  tied numerically. This is reported as-is rather than framed as an
  unambiguous win for hybrid, since it wasn't one here.
- A real nuance worth naming: despite the tied count, hybrid and
  dense-only missed *different* questions (hybrid missed a
  comparative question dense-only got right, and vice versa for a
  different question) — the two methods aren't behaviorally
  identical, just numerically tied at this sample size. With only 35
  questions, one question is worth ~2.9 percentage points, so a tie
  here reflects real small-sample variance, not proof the methods
  are equivalent in general.

---

## Reliability fixes verified since the initial benchmarks

These aren't new benchmark numbers — they're real correctness/
reliability bugs found via architecture review and fixed in the same
systems the results above measure, each verified against real
infrastructure (not just unit-tested in isolation) before being
counted as done.

- **CI frontend test gate**: the README claimed 20 frontend tests
  were CI-gated, but CI only ran backend tests. Fixed and confirmed
  passing in a real GitHub Actions run (both `test` and
  `frontend-test` jobs green).
- **Atomic queue dequeue**: `dequeue()` used to LPOP a job and mark it
  in-flight as two separate Redis calls — if a worker crashed between
  them, the job was gone from the queue but never recorded as
  in-flight, so it could never be reclaimed. Fixed via a single
  atomic Lua script (LPOP + ZADD in one server-side operation).
  Verified against real production Upstash: 21/21 queue tests passing.
- **Checkpoint-before-commit ordering**: `mark_chunk_done()` (Redis)
  used to run *inside* the same loop that adds chunks to the DB
  session, while the actual `session.commit()` only happened once at
  the end. If that commit failed for any reason (a dropped
  connection, a bad row — the exact NUL-byte bug hit earlier ingesting
  this project's own corpus), Redis would permanently believe every
  chunk was durably stored while Postgres held none — a retry would
  then skip them all forever. Fixed by only checkpointing chunks
  after a confirmed successful commit. Verified with a dedicated
  regression test that simulates a failing commit and asserts no
  chunk gets falsely checkpointed: 70/70 backend tests passing.
- **`/health/live` and `/health/ready` endpoints**: the only health
  check previously returned a static `{"status": "ok"}` regardless of
  whether the database, Redis, required config, or background workers
  were actually working. `/health/ready` now checks all four for
  real. Verified live against the deployed production instance:
  `{"status":"ready","checks":{"database":"ok","redis":"ok","openrouter_api_key":"configured","workers":"3/3 running"}}`.

---

## Agent tool-selection accuracy

**What was measured:** given a real user message, does the actual
production agent graph (`app/agents/graph.py`, invoked exactly as the
real `/agents/chat` endpoint does) correctly choose which of its 3
real tools to call (`retrieve`, `calculator`, `web_search`) — or
correctly choose to call none at all. This is a genuinely different
question from the retrieval-recall benchmarks elsewhere in this
document: those measure whether retrieval finds the right chunk; this
measures whether the agent decides to use the right tool in the first
place.

**How:** `evals/runners/agent_task_eval.py` against
`evals/datasets/agent_task_eval.json` — 30 real tasks: 10 needing
`retrieve`, 10 needing `calculator`, 5 needing `web_search`, and 5
needing no tool at all (tests that the agent doesn't over-trigger
tools unnecessarily — a real, distinct failure mode from under-use).

**Real finding: a genuine bug, found and fixed live.** The first run
scored 0/10 on the `retrieve` category — every one landed on "none"
instead. Root cause, verified by reading the actual tool code, not
guessed: the `retrieve` tool's description told the model to use it
only when the user "refers to something they uploaded, attached, or
added" — it never described the standing 132-document corpus this
project built as something worth proactively searching for ordinary
domain questions. The model was working correctly per its own
instructions; it just answered from pretrained knowledge instead.

**Fix:** rewrote `app/tools/retrieve.py`'s description to frame it as
AgentOS's persistent knowledge base, proactively usable for
domain-specific questions — while explicitly telling the model NOT to
call it for general conversation or things the corpus clearly
wouldn't cover, guarding against overcorrecting into "retrieve
everything." Also added an explicit freshness caveat: stored
documents have a fixed publication date and may be outdated.

**Result — before vs. after, same unchanged 30 tasks, same scoring:**

| Category | Before | After |
|---|---|---|
| retrieve | 0/10 | 6/10 (60.0%) |
| calculator | 10/10 | 10/10 (100.0%) |
| web_search | 5/5 | 5/5 (100.0%) |
| none | 5/5 | 5/5 (100.0%) |
| **Overall** | **20/30** | **26/30 (86.7%)** |

**Honest caveats:**
- No regression on the other 3 categories — the guardrail against
  over-triggering worked; the fix specifically targeted the actual
  broken behavior without collateral damage.
- 4 remaining misses show a real, understood pattern, not noise: all
  3 finance misses ("What is an ETF?", mutual funds, corporate bonds)
  are general financial-literacy questions the model likely already
  has decent pretrained knowledge about — unlike niche topics like
  GRAPE or radiative cooling for lightsails, where retrieval reliably
  wins. The model's own confidence competes with the instruction to
  retrieve. Pushing the description more aggressively risks
  reintroducing the over-triggering problem this fix just avoided —
  86.7% with a clearly understood failure pattern is treated as a
  legitimate stopping point, not a number left to chase further.

---

## Model router cost savings

**What was measured:** real dollar cost of the actual production
routing logic (`app/routing/router.py`'s `route_model()`) versus an
always-strong baseline (forcing every request to gpt-4o regardless),
across 12 realistic scenarios spanning all 4 combinations of the
router's real decision boundary: short/no-tools, short/with-tools,
long/with-tools, long/no-tools. The documented rule: escalate to
gpt-4o only if BOTH tools are available AND conversation history
exceeds 2 messages.

**How:** `evals/runners/model_router_benchmark.py`. Each scenario run
twice — once through `chat_completion()` with no model override (the
real router decides), once forced to gpt-4o explicitly. Cost computed
from each response's real `usage` field, using the same verified
per-token pricing already established in the 4-model benchmark above.

**Result:**

| Category | Router chose | Matches documented logic? |
|---|---|---|
| short, no tools (3) | gpt-4o-mini | ✓ |
| short, with tools (3) | gpt-4o-mini | ✓ (len=1 doesn't exceed 2) |
| long, with tools (3) | gpt-4o | ✓ (both conditions met) |
| long, no tools (3) | gpt-4o-mini | ✓ (no tools = never escalates) |

**Total cost — routed: $0.00714 | always-strong: $0.01220 | real savings: 41.5%**

**Honest caveats:**
- 12/12 routing decisions matched the documented heuristic exactly —
  real confirmation the router works as designed, not assumed.
- One scenario (long+tools) showed the *routed* path costing more
  than the always-strong path for that same line — both actually used
  gpt-4o there (correctly escalated), so this isn't a routing error.
  It's natural response-length variance between two independent
  generations of the same model, reported as-is rather than smoothed
  over.
- Small sample (12 scenarios) — a real, directionally meaningful
  result, not a claim of precision to the decimal point.

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
- grounding specifically is now backed by two independent methods
  (a vocabulary-overlap heuristic and a real LLM-as-judge call) that
  agree with each other, not just one unverified metric
- the live deployed backend handles real concurrent requests
  correctly with no cross-request state leakage, and the rate
  limiter has now been confirmed working under real, unplanned
  traffic overlap, not just a synthetic test
- hybrid retrieval's recall advantage over BM25 alone is real and
  measured (not assumed); its advantage over dense-only specifically
  was not demonstrated on this dataset — reported honestly rather
  than omitted
- the agent correctly selects the right tool for a task 86.7% of the
  time, with a real, understood failure pattern rather than an
  unexplained gap — and the evaluation process itself caught and fixed
  a genuine production bug (0/10 → 6/10 on one category) rather than
  just producing a passing number
- model routing delivers a real, measured 41.5% cost reduction versus
  always using the strongest model, with 100% of routing decisions
  confirmed to match the documented logic

They do **not** by themselves demonstrate performance at the scale
of hundreds of concurrent users or long-running multi-step agent
workflows — those remain open, named gaps rather than assumed
strengths.
