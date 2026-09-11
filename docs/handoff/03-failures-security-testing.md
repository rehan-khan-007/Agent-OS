# AgentOS Engineering Handoff — Part 3: Failure History, Security, Testing, Benchmarks

## CORRECTION to Section 3 (Current Architecture, already committed)

The architecture diagram lists "chat_sessions" under PostgreSQL. This
is INCORRECT and should be fixed. Verified in this pass: there is no
`chat_sessions` table or reference anywhere in the codebase.
Session-ownership tokens are entirely Redis-backed
(`app/auth/session_tokens.py`) with no Postgres component at all.
**Recommended fix:** remove "chat_sessions" from the PostgreSQL box in
Section 3's diagram; the Redis box already correctly lists "session
tokens."

---

## 11. Failure History

Full causal writeups (symptom, cause, fix, verification) for each of
these already exist in `ENGINEERING_LOG.md` — this table is a
structured index into that document, not a duplicate of it.

| # | Incident/Bug | Root Cause | Impact | Discovery | Fix | Regression Test | Status |
|---|---|---|---|---|---|---|---|
| 1 | BM25 exhausting Neon's monthly transfer quota | `select(DocumentChunk)` fetched the unused 1536-dim embedding column on every BM25 call | Real production incident — quota exhausted in ~4 days | Neon dashboard alert | `.options(defer(DocumentChunk.embedding))` | `test_bm25_data_transfer.py` | Fixed, verified |
| 2 | Crash-window job loss in queue dequeue | `LPOP` + `ZADD` were two separate Redis calls | Architecture-review finding; a worker dying between the two calls could silently lose a job forever | Architecture review, not a live incident | Atomic Lua script (LPOP+ZADD as one op) | Full queue suite, 21/21 vs real Upstash | Fixed, verified |
| 3 | Checkpoint-before-commit ordering | Redis checkpoint written inside the loop, before the single `session.commit()` at the end | Could make Redis permanently claim a chunk was stored when Postgres never committed it | Architecture review, motivated by bug #4 below | Checkpoint only written after confirmed commit | Dedicated test simulating a failing commit | Fixed, verified |
| 4 | NUL byte in a real PDF crashing the whole ingestion batch | Extracted PDF text contained `0x00`, which Postgres text columns reject | One bad document could take down an entire ingestion run, including already-succeeded documents | Live ingestion of the real 132-doc corpus | Text sanitization + per-document error isolation | Implicit (ingestion of the full corpus now succeeds) | Fixed, verified |
| 5 | `MaxConnectionsError` checking 200 job statuses | Unbounded concurrent status-check calls during a load test | Load test itself failed to complete | Running `load_test_queue.py` | `asyncio.Semaphore`-capped batches | Implicit (the load test now completes cleanly) | Fixed, verified |
| 6 | Worker task dying silently on unexpected exception | No broad exception handler around the main dequeue loop | Jobs would stop being processed with zero visible symptom | Code review during worker-loop development | Broad exception handler, log-and-continue | `test_worker_survives_unexpected_dequeue_exception` | Fixed, verified |
| 7 | CI never actually running the 20 frontend tests | CI workflow only had a backend `pytest` job | README claimed CI-gated frontend tests; false | Architecture review | Added a real `frontend-test` CI job | The CI job itself, now green | Fixed, verified |
| 8 | Frontend CI failing on Node/jsdom mismatch | `jsdom`'s `undici` dependency needs a newer Node API than GitHub's pinned Node 20 | Immediately surfaced once bug #7 was fixed | The new CI job's first real run | Bumped CI Node version 20 -> 22 | The CI job itself, now green | Fixed, verified |
| 9 | `retrieve` tool scoring 0/10 on genuine domain questions | Tool description framed retrieval as only for explicit user uploads, not the standing corpus | Real, measured agent behavior failure | Dedicated agent-task evaluation (`agent_task_eval.py`) | Rewrote the tool description | Same 30-task eval re-run unchanged: 0/10 -> 6/10, no regression elsewhere | Fixed, partially — see Section 16, 4 remaining honest misses |
| 10 | Async/sync mismatch breaking a test's own mock | `embed_chunks()` is called synchronously in production code; the test's replacement mock was `async def` | Test failure only, no production impact | Running the new checkpoint-ordering regression test | Made the mock a plain (non-async) function | The corrected test itself | Fixed, verified |
| 11 | Module-level Redis client breaking across pytest's per-test event loops | `redis_queue.get_client()` caches a singleton client bound to whichever event loop created it; pytest-asyncio gives each test its own loop | Intermittent test failures ("Future attached to a different loop"), test-infrastructure only | Running new session-token tests | `autouse` fixture resetting the singleton before/after each test | The corrected tests themselves | Fixed, verified |
| 12 | Tracing failure risking a duplicate real agent call | First draft's tracing error-handling fallback re-called `agent.ainvoke()` unconditionally, which could double-execute a call that had already succeeded | Real correctness risk, caught in code review before ever reaching production | Self-review during Langfuse-tracing implementation | `result is None` guard ensures the real call executes exactly once regardless of tracing outcome | Implicit (no test directly targets this failure mode — see Technical Debt) | Fixed, never reached production |

**Engineering lesson extraction, as requested by this document's own
rules:**

- **Bugs #1, #2, #3 share one lesson:** in a system spanning two
  different storage layers with different durability/consistency
  guarantees (Redis vs. Postgres), the ORDER in which you write to
  each matters as much as the individual writes themselves. A future
  contributor adding a third storage layer to this codebase should
  explicitly think through commit ordering before writing any code.
- **Bugs #7, #8 share one lesson:** a claim in documentation (README)
  is not evidence that the underlying system actually does what it
  says — this codebase's own CI configuration silently drifted from
  its documented behavior with nobody noticing until an explicit
  architecture review checked.
- **Bugs #10, #11 share one lesson:** test-only bugs are still real
  bugs, and are sometimes MORE dangerous than application bugs
  precisely because a flaky/wrong test can mask a real regression by
  failing (or passing) for the wrong reason.
- **Bug #12's lesson:** any error-handling fallback that "retries" or
  "falls back to redoing the work" must be checked for whether the
  original work might have already partially or fully succeeded —
  the safe default is to track completion explicitly (a `result is
  None` check, a completion flag), never to assume failure implies
  nothing happened.

---

## 13. Authentication & Security

| Control | Status |
|---|---|
| Session-ownership tokens | IMPLEMENTED — `app/auth/session_tokens.py`, Redis-backed, fails closed |
| User accounts / login | NOT IMPLEMENTED — deliberate scope decision (see Section 2, Phase 11) |
| Rate limiting | IMPLEMENTED — per-IP, fails open on Redis outage |
| CORS | IMPLEMENTED — explicit localhost origin + regex-matched Vercel origins |
| Secrets management | IMPLEMENTED via environment variables (Render/Vercel dashboards); NOT VERIFIED whether any secret has ever been accidentally committed to git history — worth a dedicated check (e.g. `git log -p | grep` for known key prefixes) before treating this repository as safe to make public, if it is not already |
| File upload security | PARTIALLY IMPLEMENTED — extension allowlist and size limit exist; NOT IMPLEMENTED: no authentication on who can upload, no antivirus/content scanning |
| Prompt injection defense | NOT IMPLEMENTED — retrieved document content and web search results are not filtered or sandboxed before re-entering the LLM's context; a malicious or compromised document in the corpus, or a malicious web page returned by search, could in principle attempt to inject instructions into the model's context |
| Tool security | PARTIALLY IMPLEMENTED — see calculator's `eval()`-based implementation below; retrieve and web_search have no argument validation beyond what the LLM itself provides |
| Telemetry privacy | PARTIALLY IMPLEMENTED — Langfuse traces include real user messages and model outputs; NOT VERIFIED whether any PII-scrubbing or redaction exists before data reaches Langfuse's servers |

**Real, specific finding — the calculator tool's `eval()` use:**
verified directly in this pass (`app/tools/calculator.py`):

```
allowed = {"x": 1}
allowed.update({k: v for k, v in math.__dict__.items() if not k.startswith("_")})
result = eval(expression, {"__builtins__": {}}, allowed)
```

This is a **restricted `eval()`** — `__builtins__` is emptied and the
allowed namespace is limited to `math` module functions. This is a
real, deliberate attempt at sandboxing, not an unguarded `eval()`.
However, restricted-`eval()` sandboxing in Python is a **well-known,
historically bypassable category** (object introspection via
attributes like `__class__.__bases__` or `__subclasses__()`, reachable
even from objects inside a restricted namespace, has been used in
publicly documented Python sandbox escapes). **This was NOT tested for
exploitability in this pass** — flagging it here as a real, named risk
requiring a deliberate decision (either a documented acceptance of the
risk given the tool's LLM-mediated exposure, or a replacement with a
genuinely safe expression evaluator such as a proper math-expression
parser library), not a claim that it has been exploited or is
definitely exploitable as currently deployed.

---

## 14. Current Security Threat Model

### Identity
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Session token theft (XSS, network interception) | Low-Medium | High (full conversation access) | HTTPS in transit; token never logged | No token rotation; no expiry shorter than the session itself was verified | Medium |
| session_id-only impersonation | Low (fixed by Section 13) | — | Session-ownership tokens | None remaining — this was the original, now-closed gap | Closed |

### API
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Chat endpoint abuse (cost exhaustion via scripted requests) | Medium | Real dollar cost | Per-IP rate limiting (20/5min on chat) | Fails open on Redis outage — a Redis outage removes this protection at exactly the moment it might matter most | Medium |
| Upload endpoint abuse | Medium | Corpus pollution, storage cost | Tighter rate limit (5/10min), size/extension checks | No authentication on who can upload at all | Medium |

### RAG
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Cross-tenant retrieval leakage | N/A currently | N/A — no tenants exist yet | — | Real risk the MOMENT multi-tenancy is added without also adding per-tenant retrieval filtering | High, if multi-tenancy is ever added without this in the same change |
| Document poisoning (a malicious upload skewing retrieval/answers) | Medium (anyone can upload) | Medium — could cause the agent to state false information as if grounded | None | No content vetting on upload | Medium |
| Indirect prompt injection via retrieved content | Low-Medium | Medium-High depending on future tool capabilities | None | Retrieved text and web results are not filtered before re-entering the LLM context | Medium, HIGH if write-capable tools are ever added |

### Tools
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Calculator sandbox escape | Unknown (not tested) | Potentially high (arbitrary code execution) if exploitable | Restricted `__builtins__`/namespace | Not verified safe against known Python eval-sandbox bypass techniques | High — recommend explicit testing or replacement |
| Excessive agency (future write-capable tools) | N/A currently | N/A currently | 3 current tools are all read-only/side-effect-free | No tool-level authorization/confirmation layer exists for when this changes | High, if write-capable tools are ever added without this |

### LLM
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Prompt injection / jailbreak | Medium | Low-Medium given current read-only tool set | None specific | No input/output filtering | Low currently, rises with tool capability |
| Unbounded token/cost consumption | Low | Medium (cost) | Rate limiting | No explicit per-request token budget/cap found | Medium |

### Infrastructure
| Threat | Likelihood | Impact | Current mitigation | Gap | Priority |
|---|---|---|---|---|---|
| Secrets exposure (env vars, git history) | Low | High | Env vars not hardcoded in source (verified throughout this codebase's real files) | Git history itself not audited in this pass | Medium — recommend a dedicated audit |
| Third-party service compromise (Neon/Upstash/R2/OpenRouter) | Low | High | Real, working spending alerts on Neon and Cloudflare (see project history) | No equivalent alert confirmed for OpenRouter or Upstash | Low-Medium |

---

## 15. Testing Strategy

**Real counts, verified this session:** 13 backend test files
(`backend/tests/`), 3 frontend test files (`frontend/app/`).
`README.md` and `ENGINEERING_LOG.md` state 75 backend + 20 frontend
tests as the cumulative test count across all files — this reflects
number of individual test functions, not file count, and is
consistent with what was directly observed passing in full-suite runs
throughout this project's history.

**Categories genuinely present:**
- **Unit** — chunking, hybrid-retrieval fusion logic, rate-limit IP
  extraction, retryability classification
- **Integration, real infrastructure** — BM25 data-transfer regression
  (real DB), full queue/checkpoint suite (real Upstash), session
  tokens (real Upstash)
- **Failure-injection** — LLM/embedding retry behavior under simulated
  timeouts/connection errors/5xx; worker survival under unexpected
  exceptions; checkpoint-ordering under a simulated commit failure
- **Load/concurrency** — `scripts/load_test_queue.py` (200 jobs,
  stub handler, zero LLM cost),
  `scripts/concurrent_load_test.py` (real simultaneous HTTP requests
  against the live deployment, checking specifically for
  cross-request state contamination)
- **Evaluation** (`evals/`) — a genuinely separate category from
  `pytest`-based testing; see Section 16

**Categories NOT present / explicitly named as gaps:**
- **Security testing** — no dedicated test suite for the threats named
  in Section 14 (e.g., no test attempting to exploit the calculator's
  `eval()` sandbox)
- **True end-to-end (browser-driven) testing** — frontend tests are
  component-level (Vitest + RTL), not a real browser automation suite
  against the live deployed site
- **Database/Redis restart-recovery testing** — the project has proven
  recovery when a WORKER process crashes and the queue/database
  remain available; it has NOT been proven that the system recovers
  correctly if Postgres or Redis themselves restart mid-operation

---

## 16. Benchmarks & Empirical Evidence

`evals/results/BENCHMARKS.md` is the canonical, single source of
truth for every benchmark this project has produced — this section
summarizes what exists there rather than duplicating exact numbers
that could drift out of sync if updated in only one place.

**Real, measured, and dated results currently in `BENCHMARKS.md`:**
1. Task queue load test (200 jobs, 100% terminal completion, 98%
   success excluding deliberately injected failures)
2. Hybrid retrieval recall@3 (94.3%, 132 docs / 7,748 chunks / 4
   domains)
3. 4-model LLM benchmark (cost/latency/grounding-heuristic across
   gpt-4o-mini, gpt-4o, claude-haiku-4.5, gemini-3.7-flash)
4. LLM-as-judge grounding evaluation (35/35, independently confirming
   the heuristic)
5. Live concurrent-request test (real simultaneous requests against
   the deployed backend; zero cross-request contamination; the rate
   limiter caught real, unplanned overlapping traffic correctly)
6. Retrieval ablation (BM25 91.4% / dense 94.3% / hybrid 94.3% — a
   genuine tie honestly reported, not spun toward hybrid)
7. Agent tool-selection accuracy (86.7% overall; includes the real
   0/10 -> 6/10 bug fix documented as bug #9 in Section 11)
8. Model router cost savings (41.5% real savings vs. always-strong,
   12/12 routing decisions matched the documented logic)

**Explicitly distinguishing measured / estimated / claimed, per this
document's own rules:** every number above is MEASURED — computed
from real API responses' actual `usage` fields, real test-suite pass
counts, or real timed executions against live infrastructure. None of
the headline numbers in `BENCHMARKS.md` are estimated or merely
claimed without a corresponding real run. The one important caveat,
already self-documented in `BENCHMARKS.md` itself: the grounding
LLM-judge (item 4) shares a model family with the answer-generation
model in the same benchmark — a real, general limitation of
LLM-as-judge methodology, not a data-quality problem with this
specific result.

**What these benchmarks do NOT prove**, also already stated in
`BENCHMARKS.md`'s own closing section: performance at the scale of
hundreds of concurrent users, or the behavior of genuinely long-running
multi-step agent workflows (since none currently exist to benchmark —
see Section 5's finding that no multi-hop tool loop exists yet).
