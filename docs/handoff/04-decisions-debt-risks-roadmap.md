# AgentOS Engineering Handoff — Part 4: Decisions, Debt, Risks, Roadmap

## 21. Architectural Decisions (ADR-style)

Every decision below reflects a real, actual choice made in this
project's history (Section 2), not a hypothetical alternative
invented for this document.

### ADR-1: PostgreSQL + pgvector for both relational and vector data
**Context:** needed both structured storage (conversations, chunks)
and semantic vector search.
**Options considered:** a dedicated vector database (Pinecone,
Weaviate, Qdrant) alongside a separate relational database; or
pgvector inside the already-necessary relational database.
**Chosen:** pgvector inside Postgres.
**Why:** avoids running/paying for/operating two separate database
systems; real evidence this held up — the HNSW index (Phase 9) gave
real, adequate vector search performance at 7,748 chunks without ever
needing a dedicated vector DB.
**Trade-offs:** a dedicated vector DB might outperform pgvector at
much larger scale (see Section 27, Architectural Ceiling); accepted
as a real, currently-non-binding limitation.
**Current validity:** sound at current scale. **Revisit condition:**
if corpus size grows by roughly 1-2 orders of magnitude and HNSW
query latency becomes a measured bottleneck.

### ADR-2: Redis (Upstash) as a multi-purpose infrastructure layer
**Context:** needed a job queue, a cache, rate limiting, and later,
session-ownership token storage.
**Options considered:** separate purpose-built services for each
(e.g., a dedicated queue service, a dedicated rate-limiting service).
**Chosen:** one Redis instance, serving all four purposes via
different key prefixes/data structures.
**Why:** pragmatic — each individual purpose's data volume is small;
consolidating avoids operational overhead of managing multiple
services for a project at this scale.
**Trade-offs:** a real production incident (BM25 quota exhaustion,
Section 11 bug #1) was actually a *Postgres* transfer issue, not
Redis, but the broader lesson (shared infrastructure needs care about
which subsystem is doing what) applies. The fail-open/fail-closed
split (Section 10) is the direct, correct answer to "does
multi-purposing Redis create risk" — verified real and intentional,
not an oversight.
**Current validity:** sound. **Revisit condition:** if queue volume
or cache volume individually grow enough to threaten each other's
performance (e.g., a very large cache eviction pattern disrupting
queue latency) — not currently observed.

### ADR-3: Cloudflare R2 for object storage, with a local-path fallback
**Context:** document uploads needed to work correctly regardless of
which process/machine handles ingestion (Section 9).
**Options considered:** AWS S3 (real alternative considered and
rejected — Section 9's history: S3's free tier is time-limited to 12
months and charges real egress fees; R2's free tier is permanent with
zero egress fees).
**Chosen:** Cloudflare R2, S3-compatible API.
**Why:** directly, empirically justified — not a guess. The specific
free-tier terms (10GB storage, 1M writes, 10M reads/month, permanent,
zero egress) were verified via real research before choosing, and
this project's real usage (occasional document uploads) is nowhere
near those limits.
**Trade-offs:** no hard spending cap exists on Cloudflare (confirmed
via real research — only an informational budget alert), a real,
accepted residual risk given the codebase's naturally bounded R2
usage pattern (rate-limited uploads, one download/delete per
ingestion job — not an unbounded-scaling risk the way the original
BM25 bug was).
**Current validity:** sound. **Revisit condition:** none currently
identified.

### ADR-4: LangGraph for the agent runtime
**Context:** needed a framework for the model-decides-then-optionally-
calls-a-tool loop.
**Options considered:** **NOT VERIFIED / historical** — no evidence
in this pass of a documented comparison against a hand-rolled
state machine or an alternative framework; this was very likely a
Phase 1 (earliest) choice never revisited.
**Chosen:** LangGraph.
**Why:** **INFERRED** — provides a structured way to define
model/tool/respond nodes and conditional edges, which the actual
current graph structure (Section 5) directly uses.
**Trade-offs:** the current graph's single biggest limitation (no
loop back from tool to model, Section 5) is a design choice made
WITHIN LangGraph's own capabilities, not a limitation of LangGraph
itself — LangGraph supports cyclic graphs; AgentOS's current graph
simply does not use one yet. This is worth being precise about: the
framework is not the blocker for iterative tool use; the graph
definition is.
**Current validity:** sound. **Revisit condition:** none — the
framework is not what needs to change to add iterative tool use.

### ADR-5: OpenRouter as the LLM gateway
**Context:** needed access to multiple model providers (OpenAI,
Anthropic, Google) for the 4-model benchmark and routing.
**Chosen:** OpenRouter, a single gateway.
**Why:** **INFERRED** — one API/one key instead of three separate
provider SDKs and credential sets; directly enabled the 4-model
benchmark and routing work without needing three sets of provider
credentials.
**Trade-offs:** an intermediary layer between AgentOS and each real
provider — **NOT VERIFIED** whether this adds meaningful latency
overhead versus calling providers directly (not measured in any
benchmark to date).
**Current validity:** sound, real evidence of value (routing,
multi-model benchmarking both depend on it). **Revisit condition:**
if OpenRouter-specific latency overhead is ever measured and found
significant.

### ADR-6: Heuristic model routing (tool presence + message count)
**Context:** wanted real cost savings without the complexity of a
learned/adaptive router.
**Chosen:** the simple rule in Section 12.
**Why:** empirically validated AFTER the fact, not before — the
router was built first as a reasonable guess, then genuinely
benchmarked (Section 16, item 8): 41.5% real savings, 12/12 correct
routing decisions.
**Trade-offs:** explicitly self-documented in the code as "a
starting point" — does not adapt to actual task complexity beyond
the two simple signals it checks.
**Current validity:** sound, empirically justified for current use.
**Revisit condition:** if request patterns diversify beyond what the
two-signal heuristic can distinguish (e.g., long single-message
requests that need the strong model but don't trigger the current
rule).

### ADR-7: Hybrid retrieval (BM25 + dense, RRF-fused) over dense-only
**Context:** the original retrieval implementation (Phase 2) was
dense-vector-only.
**Chosen:** added BM25 and RRF fusion (Phase 7).
**Why:** the retrieval ablation (Section 16, item 6) is the real,
honest justification — and honestly, it complicates the simple
narrative: BM25 alone measurably trails (91.4% vs 94.3%), but hybrid
TIED dense-only rather than clearly beating it on this dataset. The
real justification for keeping hybrid is not "it's proven better" —
it's that BM25 and dense retrieval demonstrably miss DIFFERENT
questions (a real finding in the ablation's own caveats), suggesting
genuine complementary value not fully captured by the aggregate
recall@3 number on a 35-question sample.
**Trade-offs:** added real complexity (two retrieval systems instead
of one) for a benefit that is real but not as clean as "hybrid wins."
**Current validity:** reasonable given the complementary-failure
evidence, but this is the kind of decision that would benefit from a
larger dataset (Section 27) to resolve more conclusively.
**Revisit condition:** if a larger, more statistically powered
ablation ever shows dense-only performing equivalently with less
system complexity.

### ADR-8: Session-ownership tokens instead of full user accounts
**Context:** a real, named security gap — a leaked `session_id` alone
could be used to hijack a conversation.
**Options considered:** (a) a full user-account system (signup,
login, passwords); (b) a lightweight ownership token alongside the
existing session_id.
**Chosen:** (b), option B — deliberately, explicitly, as a scoped
decision (Section 2, Phase 11).
**Why:** closes the actual, specific, named vulnerability without the
much larger investment of a full account system this project doesn't
yet clearly need.
**Trade-offs:** explicitly does NOT provide per-user data isolation
or multi-tenancy (Section 1's biggest named risk) — this was a
conscious scope decision, not an oversight.
**Current validity:** sound for the problem it was built to solve.
**Revisit condition:** the moment genuine multi-user/multi-tenant use
is required, this decision must be revisited together with a real
accounts system — see Roadmap, Section 32.

### ADR-9: Fail-open caching/rate-limiting, fail-closed session tokens
**Context:** covered fully in Section 10 — a deliberate, real,
security-relevant distinction in this codebase.
**Current validity:** sound and important; explicitly flagged as a
"do not break" invariant (Section 25, in a future installment).

---

## 23. Current Technical Debt

### P0 — Correctness/Security
| Problem | Location | Why it matters | Recommended fix | Risk of NOT fixing |
|---|---|---|---|---|
| Calculator's restricted `eval()` not verified safe against known Python sandbox-escape techniques | `app/tools/calculator.py` | LLM-mediated arbitrary-code-execution risk if bypassable | Replace with a real math-expression parser library, or explicitly test/document the accepted risk | Unknown severity until tested — treat as real until disproven |
| Architecture diagram (Section 3) incorrectly lists a `chat_sessions` Postgres table that does not exist | `ENGINEERING_HANDOFF.md` itself | Misleads a future reader about where session data actually lives | Remove the incorrect line (flagged in Part 3 of this handoff) | Low technical risk, real documentation-accuracy risk |

### P1 — Serious production blockers (if usage grows)
| Problem | Location | Why it matters | Recommended fix | Risk of NOT fixing |
|---|---|---|---|---|
| No multi-tenancy | System-wide | Corpus and rate limits are shared across all users | A real `SystemAdapter`-style scoping (tenant_id columns, per-tenant retrieval filtering) | Fine for a demo; a real problem the moment genuine multi-user usage begins |
| No authentication on document uploads | `app/api/documents.py` | Anyone can add documents to the shared corpus | Require session-ownership token (already exists for chat) on uploads too | Corpus pollution, real content-quality risk |
| Sequential (non-batched) Redis enqueue | `app/queue/redis_queue.py` | Real, documented throughput bottleneck (`BENCHMARKS.md`: 1.2 jobs/sec) | Pipeline/batch enqueue writes | Currently fine at real observed load; would matter at real scale |

### P2 — Important engineering debt
| Problem | Location | Why it matters | Recommended fix | Risk of NOT fixing |
|---|---|---|---|---|
| `app/retrieval/pipeline.py` is dead code | Confirmed via grep in this pass — zero imports | Confusing for a future contributor who might assume it's a live alternate code path | Delete it, or add a clear "DEPRECATED, superseded by hybrid.py" docstring | Low — purely a clarity issue |
| Pricing tables duplicated across eval scripts | `evals/runners/llm_benchmark.py`, `model_router_benchmark.py` | Real, small drift risk if one is updated and the other isn't | Consolidate into one shared pricing module | Low currently, grows with more eval scripts |
| `app/memory/short_term.py`'s current live-usage status is unconfirmed | `app/memory/` | Unclear whether this is dead code (like `pipeline.py`) or still genuinely used | A direct grep/import check (not done in this pass) | Low — a documentation-clarity gap, not a functional risk unless it IS still live and interacting with `long_term.py` in an undocumented way |
| No conversation-history length cap | `app/memory/long_term.py` | A very long conversation could grow the LLM context window unboundedly | An explicit max-messages or max-tokens truncation on history fetch | Currently unobserved in practice; a real latent cost/latency risk |
| No centralized OpenTelemetry despite being a listed dependency | `requirements.txt` vs. actual code (confirmed absent in an earlier pass) | A misleading unused dependency | Either implement it or remove the unused dependency | Low — Langfuse already covers the real observability need |

### P3 — Quality-of-life
| Problem | Location | Why it matters | Recommended fix |
|---|---|---|---|
| No dedicated regression test for the "duplicate agent call" risk fixed in Section 11, bug #12 | `backend/tests/` | The fix is real and correct but currently unguarded against a future regression | A test that simulates a tracing failure AFTER a successful `agent.ainvoke()` and asserts the agent function is called exactly once |
| No document-level dedup on re-ingestion | `app/retrieval/models.py`'s `DocumentChunk` (no content hash) | Re-ingesting the same file under the same name creates duplicate rows | Add a content-hash column and check-before-insert |

---

## 27. Architectural Risks at Scale

| Scale | Database (Neon) | Redis (Upstash) | Queue/Workers | LLM/Retrieval | Cost |
|---|---|---|---|---|---|
| 10 users | Fine, real headroom | Fine | Fine (2 workers is ample) | Fine | Negligible, matches real measured costs in `BENCHMARKS.md` |
| 100 users | Likely fine — real risk only if per-session history grows unbounded (Section 23, P2) | Fine | Fine, though sequential enqueue (Section 23, P1) starts to matter at real concurrent upload volume | Fine; rate limiting protects cost | Low, real routing savings (41.5%) compound favorably |
| 1,000 users | Real risk: shared corpus + no multi-tenancy means retrieval quality/relevance degrades as unrelated users' documents mix in the same corpus — a real correctness problem, not just a scale problem | Possible real contention between cache/queue/rate-limit/session-token traffic sharing one instance | Sequential enqueue becomes a real, measurable bottleneck | Real cost becomes worth monitoring closely, though routing helps | Worth a real Neon/Upstash tier review at this point |
| 10,000 users | Multi-tenancy becomes a hard requirement, not an optimization | A single Redis instance may need to split by purpose (separate queue vs. cache instances) | Needs real horizontal worker scaling — current single-Render-process (`WEB_CONCURRENCY=1`) model needs to change; this is exactly where the R2 fallback safety (Section 9) becomes load-bearing rather than theoretical | Retrieval-quality risk from #1,000 compounds; a real reranker (currently absent) starts to matter more | Real, meaningful cost — this is the point where the model-routing savings become operationally important, not just a nice benchmark number |
| 100,000 users | Requires a genuine redesign, not incremental fixes: real multi-tenancy, likely a dedicated vector DB reconsideration (ADR-1's revisit condition), horizontally scaled workers, and almost certainly the WOE integration (durable execution) discussed throughout this project's history | Would need a real, purpose-split Redis architecture | Requires the durable-execution work this project has explicitly deferred to WOE integration | Requires real retrieval-quality engineering (reranker, confidence gating — both already named gaps in Section 1) | Requires real, dedicated cost engineering — not a redesign this document should attempt to specify in the abstract |

**Explicitly, per this document's own instructions: no
recommendation for Kubernetes, Kafka, microservices, or Elasticsearch
is made at any of these scales** — the real bottlenecks identified
above (multi-tenancy, worker horizontal scaling, retrieval quality)
do not require those specific technologies to solve, and none of
this project's actual real usage has approached even the 1,000-user
row.

---

## 28. Current Architectural Ceiling

| Subsystem | Current design | Likely ceiling | Scaling problem | Migration trigger |
|---|---|---|---|---|
| Retrieval (pgvector + HNSW) | Single Postgres instance, HNSW index | Real, workable well beyond current 7,748 chunks — HNSW is designed for this; NOT VERIFIED at what exact chunk count query latency becomes a real problem | Index rebuild time, memory pressure on the Neon instance at very large corpus sizes | A real, measured latency regression as corpus grows — not yet observed |
| Queue (Redis, single instance) | Sequential enqueue, atomic Lua-script dequeue | Fine well beyond current real usage; sequential enqueue is the first real bottleneck | Enqueue throughput (measured: 1.2 jobs/sec) | Sustained real upload volume exceeding that rate |
| Workers (single Render process) | 2 ingestion workers + 1 reclaim loop, in-process | Bounded by Render's single-process CPU/memory | Concurrent ingestion jobs compete for the same process's resources | Real observed ingestion queue backlog |
| Agent runtime (single-turn, one tool call) | LangGraph, no loop | Not a "scale" ceiling in the traditional sense — a capability ceiling: cannot handle tasks genuinely requiring multiple sequential tool calls regardless of user count | N/A (capability, not throughput) | A real task requirement for multi-step tool use — see Roadmap |
| Database (single Neon instance) | Shared corpus, no tenant isolation | Real ceiling is NOT storage/compute — Neon's Launch plan (already active) has real headroom — the ceiling is CORRECTNESS: unrelated users' data mixing in one corpus | Retrieval relevance degradation as unrelated content grows | Any real requirement for more than one distinct "corpus" of documents |

---

## 32. Roadmap (dependency-aware, not a feature wishlist)

### Phase 0 — Correctness & Security (no dependencies, do first)
- Fix the Section 3 `chat_sessions` diagram error (trivial, already
  identified)
- Resolve the calculator `eval()` risk (test-and-accept, or replace)
- Confirm `short_term.py`'s live-usage status; remove or clarify
- Add authentication to the document-upload endpoint

**Depends on:** nothing. **Blocks:** nothing downstream, but should
close before any of the below to avoid building on top of an
unresolved security question.

### Phase 1 — Production Foundation
- Conversation-history length cap
- Consolidate duplicated pricing tables into one shared module
- Document-level content-hash dedup on ingestion
- Batch/pipeline Redis enqueue (Section 23, P1)

**Depends on:** Phase 0 (clean baseline). **Blocks:** nothing
critical, but reduces real risk before scale-sensitive work below.

### Phase 2 — Agent Runtime (the single biggest capability gap)
- Add a real loop-back edge from `tools` to `model` in the LangGraph
  graph, enabling genuine multi-step tool use within one turn
- Re-run `evals/runners/agent_task_eval.py` against the new graph to
  measure any change in tool-selection behavior (a real, necessary
  verification step, not optional)

**Depends on:** Phase 0/1 stability. **Blocks:** any future agent-
trajectory evaluation work (a named gap, not yet built).

### Phase 3 — Retrieval & Memory
- Retrieval confidence/abstention gate (directly motivated by the
  negative-control finding already proven real in this project's own
  evaluation data)
- A real cross-encoder reranker (a genuinely free, CPU-based model
  such as `cross-encoder/ms-marco-MiniLM-L-6-v2` was the specific
  recommendation discussed in this project's own history, avoiding a
  new paid API dependency)
- A larger, more statistically powered retrieval ablation to more
  conclusively resolve ADR-7's "hybrid tied dense" open question

**Depends on:** Phase 2 not required, but logically related (both
improve answer quality).

### Phase 4 — Multi-tenancy
- Real per-user/tenant data isolation across corpus, conversation
  history, and rate limiting
- MUST be designed together with cross-tenant retrieval filtering
  (Section 14's RAG threat model row) — implementing tenancy without
  this in the same change would be a real, serious regression, not an
  improvement

**Depends on:** a real decision on whether AgentOS ever needs genuine
multi-user production use (currently a demo/portfolio project) —
this phase should not be started speculatively.

### Phase 5 — Durable Execution (WOE Integration)
- Integrate the separate Workflow Orchestration Engine project
  (currently ~90% complete) as AgentOS's durable execution backbone
- This is the real prerequisite for: durable/resumable multi-step
  agent runs surviving a worker crash mid-execution, time-travel/
  branch debugging, and genuine human-in-the-loop approval workflows
- Deliberately NOT built as a duplicate, smaller version of WOE
  within AgentOS itself — this was an explicit, real decision made
  during this project's history, not an oversight

**Depends on:** WOE reaching its own completion (tracked in WOE's own
repository, not this one). **Blocks:** genuine durable execution,
time-travel debugging, HITL — all currently named, deferred gaps.

### Phase 6 — Scale
- Horizontal worker scaling beyond Render's single-process model
- Real load testing beyond the current small-scale concurrent-request
  test (Section 16, item 5)
- Revisit ADR-1 (pgvector) only if a real, measured latency ceiling is
  actually hit — not preemptively

**Depends on:** Phase 4 (multi-tenancy) if the trigger is genuine
multi-user growth; otherwise deferred indefinitely.

---

## 33. Dependency-Aware PR Sequence (Phase 0 example, illustrative)

**PR-001: Fix Section 3 architecture diagram**
Files: `ENGINEERING_HANDOFF.md`. Migration: none. Tests: none
(documentation-only). Risk: none. Dependencies: none.

**PR-002: Resolve calculator eval() risk**
Files: `app/tools/calculator.py`, possibly a new dependency (a math-
expression parser library) if replacement is chosen over acceptance.
Migration: none. Tests: a new test file exercising both legitimate
expressions and known sandbox-escape attempt patterns. Risk: low if
replacing with a well-established parser library; changes tool
behavior, so should be verified against `evals/runners/agent_task_eval.py`'s
calculator-category tasks afterward. Dependencies: none.

**PR-003: Confirm and resolve short_term.py status**
Files: `app/memory/short_term.py` and whatever imports it (or
doesn't). Migration: none. Tests: none if removed; existing tests
should still pass either way. Risk: very low. Dependencies: none.

**PR-004: Require session-ownership token on document upload**
Files: `app/api/documents.py`. Migration: none (reuses existing
session_tokens infrastructure). Tests: a new test asserting upload
fails without a valid token. Risk: low-medium — this is a real
behavior change for any existing client code calling the upload
endpoint without a token; should be paired with a frontend update in
the same PR or a closely-following one. Dependencies: none (reuses
Phase 11's existing session-token work).

This handoff does not extend the PR sequence further than Phase 0,
consistent with this document's own instruction to "not propose 30
unrelated PRs" — Phases 1 through 6's PR breakdowns should be
produced at the point each phase is actually started, informed by
whatever has changed in the codebase by then.
