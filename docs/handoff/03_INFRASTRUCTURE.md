# 3. Infrastructure, Security & Deployment

## 3.1 Redis (Upstash) — Multi-Purpose Layer
* One Redis instance serves 4 purposes: job queue, response/
  idempotency cache, rate limiting, session-ownership tokens.
* **Fail-open vs. fail-closed — deliberate, not inconsistent:**
  * Cache, rate limiting: fail OPEN (Redis outage -> app degrades,
    doesn't break).
  * Session tokens: fail CLOSED (Redis outage -> request correctly
    rejected, cannot verify ownership). **Invariant: do not
    "normalize" this to match the fail-open pattern elsewhere — that
    would be a real security regression.**

## 3.2 Queue — Job Lifecycle
```text
enqueue() -- writes job STATUS first, then job DATA, then pushes to
the queue list (this ordering prevents a race where a worker
finishes almost instantly and its "done" write gets clobbered by a
late-arriving "queued" write)
   |
dequeue() -- ATOMIC via a single Redis Lua script: LPOP + ZADD
(mark in-flight) as ONE server-side operation. Closes a real
crash-window race: two separate calls meant a worker dying between
them could lose a job with zero trace.
   |
worker processes; touch_job() refreshes in-flight timestamp on
long-running jobs
   |
mark_job_complete() -- removes from in-flight set
```
* **Stale-job recovery:** `reclaim_loop()` runs every 30s, finds
  jobs in-flight >120s via the in-flight sorted set's timestamp
  scores, re-enqueues using DURABLY-STORED job data (a SEPARATE
  Redis key from the queue list — this is what makes reclaim
  possible even after the original LPOP already removed the job).
* **Real, measured bottleneck:** enqueue is sequential, not
  batched/pipelined — 1.2 jobs/sec measured throughput. Real fix
  path: pipeline/batch the writes. Not yet done.
* Files: `app/queue/redis_queue.py` (246 lines), `worker.py` (279
  lines).

## 3.3 Authentication & Security
| Control | Status |
|---|---|
| Session-ownership tokens | IMPLEMENTED — `app/auth/session_tokens.py`, Redis-backed, fails closed |
| User accounts/login | NOT IMPLEMENTED — deliberate scope decision |
| Rate limiting | IMPLEMENTED — per-IP, fails open |
| CORS | IMPLEMENTED — localhost + regex-matched Vercel origins |
| Upload authentication | NOT IMPLEMENTED — anyone can add documents to the shared corpus |
| Prompt injection defense | NOT IMPLEMENTED — retrieved/web content not filtered before re-entering LLM context |
| Calculator sandbox | **UNVERIFIED — real, open P0.** Restricted `eval()` (`__builtins__: {}`, math-namespace only) is a well-known, historically bypassable Python sandboxing category. Not tested for exploitability. |
| Multi-tenancy | NOT IMPLEMENTED — biggest named risk; corpus + rate limits shared across ALL users |

* **Session-token mechanism:** new session -> issue token (returned
  once, client must store it); existing session -> must present
  matching token or 403. Closes the real gap where a leaked
  `session_id` alone (no further check) could let anyone read/
  continue someone else's conversation.

## 3.4 Threat Model — Real, Prioritized
* **RAG:** cross-tenant retrieval leakage — N/A today (no tenants),
  becomes HIGH priority the MOMENT multi-tenancy is added without
  per-tenant retrieval filtering in the SAME change.
* **Tools:** calculator sandbox escape — Unknown likelihood (not
  tested), potentially HIGH impact if exploitable. Recommend
  explicit testing or replacement with a real math-expression parser.
* **Infra:** secrets exposure — git history not independently audited
  in any pass; recommend a dedicated check before treating the repo
  as safe to make public if not already.
* **API:** chat/upload endpoint abuse — real rate limits exist but
  fail open on Redis outage, removing protection at exactly the
  moment it might matter most.

## 3.5 Deployment
* **Backend:** Render, single process, `WEB_CONCURRENCY=1`.
* **Frontend:** Vercel.
* **Database:** Neon, Launch plan (real upgrade after the BM25 quota
  incident). Real spending safety: informational alert configured.
* **Redis:** Upstash free tier, real $2 spending alert.
* **Storage:** Cloudflare R2 free tier, real $2 budget alert
  (informational only, no hard cap exists on Cloudflare).
* **10 real env vars (backend):** `DATABASE_URL`, `REDIS_URL`,
  `OPENROUTER_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
  `TAVILY_API_KEY`, `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`.

## 3.6 Failure Behavior by Infrastructure Component
| Event | Behavior |
|---|---|
| Worker crash mid-ingestion-job | RECOVERED — real, tested (`reclaim_loop`) |
| Worker/process crash mid-agent-turn | NOT recovered — that turn is simply lost; no durable agent execution exists |
| Database outage | Requests fail directly — no fail-open for DB layer |
| Redis outage | Cache/rate-limit fail open; session-token verification fails closed |
| R2 outage (vs. unconfigured) | Local fallback triggers on UNCONFIGURED credentials, not on a live outage — NOT VERIFIED whether a genuine R2 outage is handled gracefully |
| LLM (OpenRouter) outage | Real retry/backoff handles transient failures; sustained outage surfaces as a real chat failure after retries exhausted — no fallback provider |

## 3.7 CI/CD
* `.github/workflows/ci.yml` — two real jobs: `test` (backend, real
  Postgres service container w/ pgvector) and `frontend-test` (Node
  22, `npm test`). Both gate every push — verified green.
* **Real, previously-shipped bug, now fixed:** CI claimed to gate 20
  frontend tests for a real period before that was actually true
  (the job didn't exist). Immediately after adding it, a SEPARATE
  real bug surfaced: Node 20's jsdom/undici dependency chain needs a
  newer Node API — fixed by bumping CI to Node 22.
* **NOT VERIFIED:** whether frontend's `lint` script actually runs in
  CI (workflow jobs are named `test`/`frontend-test`, not `lint`). No
  security/dependency-vulnerability scanning gate found.

## 3.8 Real Invariants (Infrastructure)
1. Atomic dequeue (LPOP+ZADD as one Lua script) — must remain atomic.
2. Session tokens fail closed — must never be normalized to fail open.
3. Worker loop's outer exception handler must stay BROAD
   (catch-log-continue) — narrowing it reintroduces a real bug where
   an unexpected exception silently killed the whole worker task with
   zero visible symptom besides jobs never being processed again.
4. R2 credentials required for any >1-worker-process deployment.
