# 1. Architecture & Agent Runtime

## 1.1 System Topology
* **Backend:** FastAPI, deployed on Render, single process
  (`WEB_CONCURRENCY=1`).
* **Frontend:** Next.js/TypeScript, deployed on Vercel. Genuinely a
  single-page app — `frontend/app/page.tsx` (393 lines) is the entire
  UI; no `components/` directory, no separate routes.
* **Framework choice (agent):** LangGraph. Real, verified: LangGraph
  itself supports cyclic graphs — the current single-tool-call
  limitation (1.2) is a GRAPH-DEFINITION choice, not a framework
  limitation. Do not attribute this gap to LangGraph.
* **LLM gateway:** OpenRouter — one API/key for OpenAI, Anthropic,
  Google models, instead of three separate provider SDKs. Directly
  enabled the 4-model benchmark and routing work.

## 1.2 Agent Graph — Exact Structure
```text
model (call_model)
   |
router(): did the model request a tool call?
   |
   +-- yes --> tools (call_tool) --> respond (final LLM call) --> END
   |
   +-- no  --> respond (final LLM call) --> END
```
* **State:** `AgentState` (TypedDict) — `{messages: list[dict], next: str}`.
  Minimal by design.
* **Critical limitation:** NO edge from `tools` back to `model`. One
  tool-selection opportunity per turn. If the model calls `retrieve`
  and then needs `web_search` too, it cannot — the final `respond`
  call cannot itself request another tool.
* **Termination:** always reaches `END` after at most one `respond`
  call. No loop = no risk of a runaway agent loop today (an
  incidental, not deliberate, safety property).
* File: `backend/app/agents/graph.py` (137 lines).

## 1.3 Request Flow (verified against `_run_agent()` in `app/api/agents.py`)
```text
User message
   |
Session-ownership check (new -> issue token; existing -> verify,
fail closed on Redis outage)
   |
Idempotency cache check (same session_id+message -> return cached,
skip everything below)
   |
Load conversation history (Postgres, app.memory.long_term)
   |
agent.ainvoke() -- wrapped in ONE parent Langfuse observation so the
whole run traces as one connected tree, not fragments
   |
Persist new messages to Postgres
   |
Cache the response (Redis)
```

## 1.4 Tool System
| Tool | Read/Write | External dep | Side effects | Security risk |
|---|---|---|---|---|
| `retrieve` | Read | Postgres/pgvector | None | Low |
| `web_search` | Read | Tavily API | Real network call, real cost | Low-medium — no prompt-injection filtering on returned content |
| `calculator` | Read (compute) | None | None | **Uses restricted `eval()` — `__builtins__: {}`, math-namespace only. NOT VERIFIED safe against known Python sandbox-escape techniques. Real, open P0 item.** |

* **Tool description = behavior, not documentation.** Proven
  empirically: rewriting `retrieve`'s description (from "use only for
  explicit uploads" to "proactively search the standing corpus")
  changed a real, measured score from 0/10 to 6/10 on
  `agent_task_eval.py`, unchanged dataset. Any tool-description edit
  must be followed by re-running that eval.
* No tool-level authorization/confirmation layer exists. Fine today
  (all 3 tools are read-only/side-effect-free). Becomes
  security-critical the moment a write-capable tool is added.

## 1.5 Memory System
* **Short-term** (`app/memory/short_term.py`, 34 lines): in-process
  Python dict, explicitly documented as scaffolding in its own
  docstring. **NOT VERIFIED whether still live or fully superseded**
  — check before relying on it.
* **Long-term/persistent** (`app/memory/long_term.py`, 57 lines +
  `models.py`, 20 lines): the real, live system. One Postgres table,
  `conversation_messages` (`id, session_id, role, content, tool_calls,
  tool_call_id, created_at`). Keyed by `session_id` only — no
  `user_id`/tenant column.
* `get_history()` does a full, unfiltered fetch, chronological order.
  **No pagination or max-length cap found** — a very long
  conversation could grow the LLM context window unboundedly (real,
  unmeasured cost/latency risk).
* **No deletion/retention mechanism found.** Data persists
  indefinitely by default.

## 1.6 LLM Layer & Routing
* **Client:** `app/llm/client.py` (122 lines). Retry/backoff
  (tenacity), transient failures only (timeouts, connection errors,
  5xx) — 4xx never retried. Verified with a real 23-test
  failure-injection suite.
* **Router:** `app/routing/router.py` (25 lines), genuinely simple,
  self-documented as "intentionally basic to start":
  ```python
  if tools and len(messages) > 2:
      tier = "strong"
  else:
      tier = "fast"  # default
  ```
* **Empirically justified (not assumed):** `gpt-4o-mini` as fast-tier
  default — measured 100%/100% success/grounding vs `gpt-4o`, at
  ~1/18th the cost, same 35-question dataset. Router itself
  benchmarked: 12/12 real scenarios routed correctly, 41.5% real
  savings vs always-strong.
* **Heuristic, not learned:** the routing RULE itself is a starting
  point, not adaptive — explicitly self-documented as such.
* **Cost tracking:** computed from real `usage` fields per response,
  never estimated. Pricing tables currently hardcoded per-eval-script
  (`llm_benchmark.py`, `model_router_benchmark.py`) — a real, small
  duplication worth consolidating.

## 1.7 Observability
* **Tracing:** every `chat_completion()` call wraps in a Langfuse
  `start_as_current_observation(as_type="generation")`. As of the
  real Phase-11 fix, these correctly nest under ONE parent
  `"agent_run"` observation per request — previously disconnected
  fragments (a real, fixed bug).
* **Logging:** structured JSON (`app/observability/logging.py`),
  `extra_fields` pattern — this is what made several real bugs
  (silently-dying worker, etc.) diagnosable in the first place.
* **NOT implemented:** OpenTelemetry, despite being a listed
  dependency — confirmed absent from actual code. Langfuse is the
  real, working observability layer.

## 1.8 Real Architectural Decisions (condensed ADRs)
* **LangGraph chosen** over a hand-rolled state machine — real
  history not documented; likely an early, never-revisited choice.
  Framework is NOT the blocker for iterative tool use (see 1.2).
* **OpenRouter chosen** over direct provider SDKs — enabled real
  multi-model benchmarking/routing without 3 separate credential
  sets. Latency overhead vs. direct calls: NOT VERIFIED/measured.
* **Heuristic routing chosen** over a learned router — validated
  AFTER the fact via real benchmark, not before. Revisit if request
  patterns diversify beyond the two-signal heuristic's ability to
  distinguish them.
