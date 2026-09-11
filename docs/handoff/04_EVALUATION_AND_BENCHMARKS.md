# 4. Evaluation & Benchmarks

## 4.1 Real Testing Categories Present
* **Unit:** chunking, hybrid-fusion logic, rate-limit IP extraction,
  retryability classification.
* **Integration, real infrastructure:** BM25 data-transfer regression
  (real DB), full queue/checkpoint suite (real Upstash), session
  tokens (real Upstash).
* **Failure-injection:** LLM/embedding retry under simulated
  timeouts/connection errors/5xx; worker survival under unexpected
  exceptions; checkpoint-ordering under a simulated commit failure.
* **Load/concurrency:** `scripts/load_test_queue.py` (200 jobs, stub
  handler, zero LLM cost); `scripts/concurrent_load_test.py` (real
  simultaneous HTTP requests against the LIVE deployment, checking
  specifically for cross-request state contamination).
* **Real counts:** 75 backend test functions across 13 files, 20
  frontend test functions across 3 files. Both suites CI-gated.

## 4.2 Real Testing Categories Absent (named gaps, not oversights)
* No security test suite — e.g., nothing directly tests whether the
  calculator's `eval()` sandbox is actually exploitable.
* No true end-to-end browser-automation test against the live
  deployed site (frontend tests are component-level, Vitest+RTL).
* No database/Redis RESTART-recovery test. Worker-crash recovery IS
  proven (with Postgres/Redis staying available); Postgres/Redis
  THEMSELVES restarting mid-operation is NOT proven.

## 4.3 Evaluation Runners (`evals/runners/`, 6 independent scripts)
| Runner | Measures |
|---|---|
| `benchmark.py` | Hybrid retrieval recall@3 across the full corpus |
| `retrieval_ablation.py` | BM25-only vs dense-only vs hybrid, isolated |
| `llm_benchmark.py` | Success/grounding/latency/cost across 4 real models |
| `grounding_judge_eval.py` | LLM-as-judge grounding, independent of the heuristic |
| `agent_task_eval.py` | Does the agent pick the right tool for a real task |
| `model_router_benchmark.py` | Real cost savings from routing vs. always-strong |

## 4.4 Every Real, Dated Result (canonical source: `evals/results/BENCHMARKS.md`)

**1. Task queue load test** (Aug 23, 2026) — 200 jobs, 4 deliberately
injected failures, 4 workers, real Upstash.
* 100% terminal completion, 98% success excluding injected failures.
* Enqueue: 1.2 jobs/sec (sequential — real, named bottleneck).
* Processing: 3.5 jobs/sec.

**2. Hybrid retrieval recall** (Aug 23, 2026) — 132 docs/7,748 chunks/
4 domains, 35 real questions + 1 negative control.
* **94.3% recall@3** (33/35). Real misses: a comparative question
  (GRAPE efficiency) and a skill-level-specific SEBI question — both
  genuinely hard cases, not random noise.
* Negative control correctly NOT scored as hit/miss — pgvector always
  returns nearest neighbors regardless of true relevance (a real,
  unaddressed limitation, not fixed by this result).

**3. 4-model LLM benchmark** (Aug 23, 2026) — same 35 questions,
same retrieved context, cost from real `usage` fields.
| Model | Success | Grounded* | Avg Latency | Cost |
|---|---|---|---|---|
| gpt-4o-mini | 100% | 100% | 2.34s | $0.00597 |
| gpt-4o | 100% | 100% | 2.39s | $0.10712 |
| claude-haiku-4.5 | 100% | 100% | 4.49s | $0.07688 |
| gemini-3.7-flash | 100% | 100% | 5.47s | $0.02688 |
* Total cost: $0.2168. **gpt-4o ~18x more expensive than gpt-4o-mini
  for near-identical latency** — the real empirical basis for
  gpt-4o-mini as the routing default.
* *Grounding here is a vocabulary-overlap heuristic, not LLM-judge.

**4. LLM-as-judge grounding eval** (Aug 23, 2026) — separate model
call judging each answer, structured reasoning, gpt-4o-mini.
* **35/35 (100%) judged grounded.** Independently CONFIRMS (not
  merely repeats) the heuristic's finding. Total cost: $0.0119.
* Real, positive finding: several answers were judged grounded
  BECAUSE they correctly said the context lacked detail, rather than
  fabricating an answer.
* Known limitation: judge shares model family with the generation
  model in this benchmark.

**5. Live concurrent-request test** (Aug 24, 2026) — real
simultaneous HTTP requests against the LIVE deployed backend.
* Run 1 (15 req): 15/15 succeeded but clustered at 28.09-28.28s — a
  Render cold-start artifact, not real concurrent latency.
* Run 2 (15 req, ~1 min later): 5/15 succeeded at a real 1.47-1.62s
  (genuine warm latency); other 10/15 correctly rejected with 429 —
  the two runs landed in the same 5-min rate-limit window, and the
  limiter caught real, UNPLANNED overlapping traffic correctly.
* **Cross-contamination check PASSED both runs** — every response had
  a unique session ID and correct answer, zero crossover.

**6. Retrieval ablation** (Aug 24, 2026) — same 35 questions, 3
methods in isolation.
* BM25-only: 32/35 (91.4%). Dense-only: 33/35 (94.3%). Hybrid:
  33/35 (94.3%).
* **Hybrid TIED dense — reported honestly, not spun as a win.** Real
  nuance: hybrid and dense missed DIFFERENT questions despite the
  tied count — complementary behavior, not equivalence, at this
  sample size (35 questions = ~2.9 points/question).

**7. Agent tool-selection accuracy** (Aug 24, 2026) — 30 real tasks
(10 retrieve, 10 calculator, 5 web_search, 5 none), before/after a
real bug fix.
| Category | Before | After |
|---|---|---|
| retrieve | 0/10 | 6/10 (60%) |
| calculator | 10/10 | 10/10 |
| web_search | 5/5 | 5/5 |
| none | 5/5 | 5/5 |
| **Overall** | **20/30** | **26/30 (86.7%)** |
* Root cause of the 0/10: `retrieve`'s description framed it as
  "only for explicit uploads," never mentioning the standing corpus.
  Fix: rewrote the description. Re-ran the SAME unchanged 30 tasks —
  no regression on the other 3 categories (the over-triggering
  guardrail worked).
* 4 remaining misses show a real, understood pattern (general
  financial-literacy questions the model already has pretrained
  confidence about) — not chased further; pushing harder risks
  reintroducing over-triggering.

**8. Model router cost savings** (Aug 24, 2026) — 12 scenarios
spanning all 4 combinations of the router's real decision boundary.
* **12/12 routing decisions matched the documented logic exactly.**
* Total cost — routed: $0.00714 | always-strong: $0.01220 |
  **real savings: 41.5%.**
* One scenario showed routed costing MORE than always-strong for that
  specific line — both used gpt-4o there (correctly escalated);
  natural response-length variance between two generations of the
  same model, reported as-is.

## 4.5 What These Results Do NOT Prove (self-documented, not omitted)
* Performance at hundreds-of-concurrent-users scale.
* Long-running multi-step agent workflow behavior (none currently
  exist to benchmark — the agent graph supports one tool call/turn).
* Global grounding-judge validity beyond this specific
  same-model-family setup.

## 4.6 Total Real Spend Across Every Benchmark
Under $0.30 combined, across all evaluation runs listed above.
