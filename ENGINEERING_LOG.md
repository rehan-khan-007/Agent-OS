# Engineering Log

A record of what actually happened building this project — not a feature
list, but the bugs that were found by running things for real, and how
they were fixed. Kept here because "walk me through a bug you found and
fixed" is one of the most common interview questions, and these are all
real, verifiable ones: every fix below was reproduced, fixed, and
re-verified against real production infrastructure (live Neon Postgres,
live Upstash Redis, and the live Render deployment) before being
committed.

See [`evals/results/BENCHMARKS.md`](evals/results/BENCHMARKS.md) for the
measured results this system produces; this document is about how it got
built correctly.

## Bugs found and fixed

### 1. BM25 search silently exhausting the database's monthly transfer quota

**Symptom:** Neon's free-tier 5GB/month data transfer quota was
exhausted in roughly 4 days, with no obvious single cause — the app
itself wasn't doing anything unusual.

**Cause:** `bm25_search()` ran `select(DocumentChunk)`, which fetches
every column — including the 1536-dimension embedding vector — on
every single call, even though BM25 scoring never uses the embedding
at all. At 7,748 rows and roughly 22KB/row (dominated by the unused
embedding), a handful of searches per session was enough to transfer
hundreds of megabytes of data that was never actually read.

**Fix:** `select(DocumentChunk).options(defer(DocumentChunk.embedding))`
— explicitly excludes the embedding column from the query. Verified
with a regression test (`test_bm25_data_transfer.py`) that asserts,
via `sqlalchemy.inspect(chunk).unloaded`, that the embedding column
never gets loaded by a real BM25 call against a live database.

### 2. A crash-window race that could silently lose a queued job forever

**Symptom:** found via architecture review, not a live incident —
but a real, reproducible gap: `dequeue()` performed an `LPOP` (remove
from queue) and a separate `ZADD` (mark in-flight) as two independent
Redis calls.

**Cause:** if a worker process died in the window between those two
calls, the job was already gone from the queue but never recorded as
in-flight — meaning `reclaim_stale_jobs()` could never find it to
retry. The job would simply vanish with no error and no trace.

**Fix:** replaced the two-step sequence with a single atomic Redis Lua
script that performs the `LPOP` and `ZADD` as one server-side
operation — Redis guarantees a Lua script executes atomically, so a
crashing client can no longer interrupt it mid-way. Verified with the
full queue test suite (21 tests) run against real, live Upstash Redis.

### 3. A database commit failure that would make Redis lie about what was actually saved

**Symptom:** also found via architecture review — a real correctness
gap, not yet a live incident, in the document-ingestion pipeline.

**Cause:** `mark_chunk_done()` (writing to Redis) was called *inside*
the same loop that added chunks to the database session, while the
actual `session.commit()` only happened once, after the loop finished.
If that commit failed for any reason — the exact NUL-byte corruption
bug (below) is a real example of the kind of failure that could
trigger this — Redis would already believe every chunk was durably
stored, while Postgres held none of them. A retry would then skip
every "already done" chunk forever, since the checkpoint said they
were finished.

**Fix:** checkpoint writes now only happen *after* `session.commit()`
has confirmed success. Verified with a dedicated regression test that
monkeypatches `commit()` to always fail and asserts no chunk gets
checkpointed as done despite reaching the code that adds it to the
session — proving the ordering fix actually holds, not just that the
code compiles.

### 4. A NUL byte in a real PDF crashing the entire ingestion batch

**Symptom:** ingesting the real 132-document corpus, one specific PDF
caused `CharacterNotInRepertoireError` from asyncpg, and — before the
fix below — took the *entire* ingestion run down with it, including
documents that had already succeeded.

**Cause:** the PDF's extracted text contained a literal NUL byte
(`0x00`), which PostgreSQL's text columns cannot store. The ingestion
script had no per-document error isolation, so one bad document's
exception propagated up and killed the whole batch.

**Fix:** `text.replace("\x00", "")` sanitizes extracted text before
storage, and the ingestion script was restructured with per-document
`try`/`except` isolation so one bad document is skipped and logged,
not fatal to the rest of the run.

### 5. `MaxConnectionsError` from checking 200 job statuses at once

**Symptom:** a queue load test (200 jobs) crashed with
`MaxConnectionsError` while polling job statuses to compute the final
completion rate.

**Cause:** the status-checking code fired all 200 status lookups
concurrently with no cap, opening far more simultaneous connections to
Upstash than the plan allows.

**Fix:** capped concurrent status checks with an `asyncio.Semaphore`,
processing them in bounded batches instead of all at once.

### 6. A worker task that could die silently and never process another job

**Symptom:** found via code review while building the worker loop,
then deliberately reproduced with a test — a genuinely serious failure
mode: if any unexpected exception (a Redis timeout, a connection
reset) escaped the main dequeue loop, the entire `asyncio.Task` running
that worker would terminate with no visible symptom except jobs
silently never being picked up again.

**Fix:** wrapped the dequeue loop in a broad exception handler that
logs the failure and continues, rather than letting the task die.
Verified with `test_worker_survives_unexpected_dequeue_exception`,
which injects exactly this kind of failure and asserts the worker
keeps running afterward.

### 7. CI claiming to gate 20 frontend tests it never actually ran

**Symptom:** the README stated the project had "20 frontend tests,
gated in CI" — true of the tests existing and passing locally, but
CI's actual workflow only ran the backend `pytest` suite. The frontend
suite had never once executed in CI.

**Fix:** added a real `frontend-test` job to `.github/workflows/ci.yml`
that installs Node dependencies and runs `npm test`. This immediately
surfaced bug #8 below, since it had genuinely never run in this
environment before.

### 8. Frontend CI failing on a Node/jsdom version mismatch invisible locally

**Symptom:** the new `frontend-test` CI job failed with
`TypeError: webidl.util.markAsUncloneable is not a function` — a
failure that never occurred locally.

**Cause:** `jsdom`'s dependency chain (via `undici`) requires a newer
Node.js API than GitHub's pinned Node 20 runner provides. The local
development machine already had a newer Node version installed,
masking the incompatibility entirely.

**Fix:** bumped the CI workflow's Node version from 20 to 22.

### 9. A retrieve tool that silently never got called for genuine domain questions

**Symptom:** a dedicated agent-task evaluation (30 real tasks) scored
0/10 on questions that should have triggered document retrieval — the
agent answered every one from pretrained knowledge instead.

**Cause:** the tool's own description told the model to use it only
when the user "refers to something they uploaded, attached, or
added" — language written for an early, per-session-upload mental
model of the system, never updated after the project grew a 132-
document standing corpus. The model was behaving correctly according
to its own instructions.

**Fix:** rewrote the tool description to frame it as a persistent,
proactively-searchable knowledge base — while explicitly warning
against over-triggering on general conversation, to avoid swinging
too far the other way. Re-ran the *exact same, unchanged* 30-question
evaluation afterward: retrieval accuracy went from 0/10 to 6/10, with
zero regression on the other 20 tasks. Full details and the honest
remaining gap in `BENCHMARKS.md`.

### 10. Async/sync mismatch silently breaking a test's mock

**Symptom:** a new regression test for the checkpoint-ordering fix
(#3) failed with `TypeError: 'coroutine' object is not iterable`,
along with a warning that a coroutine was never awaited.

**Cause:** the real `embed_chunks()` function is called *synchronously*
(no `await`) inside the worker, but the test's mock replacement was
written as `async def` — calling it without `await` returned an
unexecuted coroutine object instead of the fake embedding data,
which then failed to iterate over.

**Fix:** made the mock a plain (non-async) function, matching the real
function's actual calling convention exactly.

### 11. A module-level Redis client breaking across pytest's per-test event loops

**Symptom:** new session-token tests intermittently failed with
`RuntimeError: Task ... got Future ... attached to a different loop`
— but only some tests, and only when run together with others in the
same file.

**Cause:** `redis_queue.get_client()` deliberately caches a
module-level singleton client — correct behavior in production, where
one process runs one long-lived event loop. But `pytest-asyncio` gives
each test function its own fresh event loop by default; a client
created and bound to one test's loop crashes when reused inside a
later test's (by-then-closed) loop.

**Fix:** an `autouse` pytest fixture that resets the cached singleton
to `None` before and after every test in the affected file, forcing a
fresh client bound to whichever loop that specific test is actually
running under. Production code itself was never changed — this is a
test-infrastructure-only fix, same as the underlying principle in
bug #6.

### 12. A tracing failure that could have caused a real agent call to run twice

**Symptom:** caught via code review before ever reaching production,
while adding Langfuse tracing around the full agent run — not a live
incident, but a real correctness risk that would have been genuinely
serious if shipped.

**Cause:** the first draft wrapped `agent.ainvoke()` inside a
try/except meant to make tracing failures non-fatal — but the except
block's fallback was to call `agent.ainvoke()` *again*. If the
original call had already succeeded and a failure only occurred
afterward (e.g. during `span.update()`), the except block would run
the real agent call a second time — doubling real LLM cost and any
side effects, silently.

**Fix:** restructured so the real call is only ever invoked once,
guarded by a `result is None` check — a tracing-only failure at any
point can no longer cause the underlying agent call to execute twice,
regardless of when in the tracing lifecycle the failure happens.

## What this adds up to

Most of these were found by actually running the system against real
infrastructure — a live 132-document corpus, live Upstash Redis, a
live Neon Postgres instance, and the live Render deployment — not by
writing more tests in the abstract. A few (bugs #2, #3, #12) were
caught through architecture review and code reading before they ever
became live incidents, and were still verified with the same rigor:
a reproducing test, a fix, and a re-run against real infrastructure
before being trusted. Bugs #10 and #11 are a useful reminder that this
principle applies to test infrastructure itself, not just application
code — a test that fails for a reason unrelated to what it's actually
checking is its own kind of bug worth taking seriously.
