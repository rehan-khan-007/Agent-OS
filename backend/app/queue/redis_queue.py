"""
Redis-backed job queue.

Unlike the cache module (app/cache/redis_client.py), which fails open
when Redis is unavailable — caching is an optimization, and skipping
it just means slower requests — a queue cannot fail open. If a job
were silently dropped instead of enqueued, that job is simply lost.
So queue operations raise clearly when Redis isn't configured or
unreachable, rather than pretending to succeed.

Job lifecycle: enqueue() pushes a job onto a Redis list and writes its
initial status to a Redis hash. A worker calls dequeue() (blocking pop
with a timeout) to pick up the next job, processes it, and calls
update_status() to record progress or completion. Status is stored in
Redis (not in-process memory), so it survives a server restart and
would work correctly across multiple app instances.
"""

import json
import time
import uuid
from typing import Any

import redis.asyncio as redis

from app.config import settings

_client: redis.Redis | None = None

STATUS_KEY_PREFIX = "job_status:"
JOB_DATA_KEY_PREFIX = "job_data:"
INFLIGHT_KEY_PREFIX = "inflight:"
STATUS_TTL_SECONDS = 60 * 60 * 24  # keep job status visible for 24h

# Atomically pops a job and marks it in-flight in a single Redis-side
# operation. Doing this as two separate client calls (LPOP, then ZADD)
# leaves a real crash window: if the worker process dies between the
# two calls, the job is already gone from the queue but was never
# recorded as in-flight, so reclaim_stale_jobs() can never find it —
# a genuinely lost job with no code path back. Redis guarantees a Lua
# script runs atomically on the server; a crashing client can't
# interrupt it mid-script the way it could interrupt two separate
# round-trips.
_DEQUEUE_AND_MARK_INFLIGHT_SCRIPT = """
local raw_job = redis.call('LPOP', KEYS[1])
if raw_job == false then
  return false
end
local job = cjson.decode(raw_job)
redis.call('ZADD', KEYS[2], ARGV[1], job['job_id'])
return raw_job
"""


class QueueUnavailableError(RuntimeError):
    """Raised when Redis isn't configured/reachable — queue operations
    cannot proceed, since silently dropping a job would lose it."""
    pass


def get_client() -> redis.Redis:
    global _client
    if not settings.redis_url or "localhost" in settings.redis_url:
        raise QueueUnavailableError(
            "Redis is not configured (REDIS_URL unset or points to localhost) — "
            "cannot enqueue or dequeue jobs."
        )
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def enqueue(queue_name: str, job_type: str, payload: dict, **initial_status: Any) -> str:
    """Adds a job to the named queue. Returns the job's id.

    Any keyword arguments beyond queue_name/job_type/payload are
    written into the job's initial status alongside status="queued"
    — e.g. enqueue(..., filename="doc.pdf") makes "filename" readable
    via get_status() immediately, before a worker even picks it up.

    Status is written BEFORE the job is pushed onto the queue, not
    after. This ordering matters: a worker can pick up and finish a
    job (writing status="done") almost immediately after it's pushed
    — if the initial "queued" status write happened afterward, it
    could land after the worker's "done" write and incorrectly
    overwrite it back to "queued", making a genuinely finished job
    look stuck. Writing status first closes that race.

    The full job (job_type, payload, queue_name) is also persisted
    keyed by job_id, separately from the queue list itself. This is
    what makes reclaim_stale_jobs() possible: once a job is popped
    off the list by a worker (dequeue), it's gone from the list even
    if that worker later crashes mid-processing — without a durable
    copy of the job data, there would be nothing to re-enqueue.

    Raises QueueUnavailableError if Redis isn't reachable — callers
    must handle this rather than assume the job was queued."""
    client = get_client()

    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "job_type": job_type, "payload": payload}

    await update_status(job_id, status="queued", **initial_status)
    await client.set(f"{JOB_DATA_KEY_PREFIX}{job_id}", json.dumps({**job, "queue_name": queue_name}), ex=STATUS_TTL_SECONDS)
    await client.rpush(queue_name, json.dumps(job))

    return job_id


async def dequeue(queue_name: str, timeout: int = 5) -> dict | None:
    """
    Pops the next job off the named queue, or None if it's empty.

    Uses a plain non-blocking LPOP rather than a blocking BLPOP.
    Blocking commands don't play well with Upstash's serverless proxy
    — in production this caused BLPOP to reliably time out
    ("Timeout reading from ...upstash.io:6379") even on a healthy
    connection. LPOP returns immediately either way, and the caller
    (worker_loop) handles "check again shortly" itself via a short
    sleep when nothing's available. `timeout` is now unused but kept
    so callers don't need to change.

    On a successful pop, the job is marked "in-flight" (a sorted set
    of queue_name -> {job_id: dequeued_timestamp}) — atomically, via
    a single Lua script (see _DEQUEUE_AND_MARK_INFLIGHT_SCRIPT), not
    two separate LPOP/ZADD calls. This is how reclaim_stale_jobs()
    later notices a job whose worker died before finishing —
    mark_job_complete() removes it from this set on success, so
    anything still present after a staleness threshold is presumed
    abandoned.
    """
    client = get_client()
    raw_job = await client.eval(
        _DEQUEUE_AND_MARK_INFLIGHT_SCRIPT,
        2,
        queue_name,
        f"{INFLIGHT_KEY_PREFIX}{queue_name}",
        time.time(),
    )
    if not raw_job:
        return None
    return json.loads(raw_job)


async def mark_job_complete(queue_name: str, job_id: str) -> None:
    """Removes a job from the in-flight set — call this once a job
    reaches a terminal state (done or failed), so it's no longer a
    candidate for reclaim."""
    client = get_client()
    await client.zrem(f"{INFLIGHT_KEY_PREFIX}{queue_name}", job_id)


async def touch_job(queue_name: str, job_id: str) -> None:
    """Refreshes a job's in-flight timestamp. A worker still actively
    processing a long job (e.g. embedding hundreds of chunks) should
    call this periodically so reclaim_stale_jobs() doesn't mistake
    genuinely-in-progress work for an abandoned job."""
    client = get_client()
    await client.zadd(f"{INFLIGHT_KEY_PREFIX}{queue_name}", {job_id: time.time()})


async def reclaim_stale_jobs(queue_name: str, stale_after_seconds: int) -> list[str]:
    """
    Finds jobs marked in-flight for longer than stale_after_seconds
    (almost certainly abandoned by a worker that crashed or was
    killed mid-job) and re-enqueues them using their durably-stored
    job data. Returns the list of reclaimed job_ids.

    This is the actual crash-recovery mechanism: a job's own handler
    is responsible for resuming from a checkpoint rather than
    reprocessing everything from scratch (see is_chunk_done /
    mark_chunk_done, used by the document ingestion handler).
    """
    client = get_client()
    cutoff = time.time() - stale_after_seconds

    stale_ids = await client.zrangebyscore(f"{INFLIGHT_KEY_PREFIX}{queue_name}", min=0, max=cutoff)
    reclaimed = []

    for job_id in stale_ids:
        raw_job_data = await client.get(f"{JOB_DATA_KEY_PREFIX}{job_id}")
        if raw_job_data is None:
            # Job data expired or was never stored — can't reclaim,
            # just drop it from in-flight tracking so it's not
            # checked again every reclaim cycle.
            await client.zrem(f"{INFLIGHT_KEY_PREFIX}{queue_name}", job_id)
            continue

        job_data = json.loads(raw_job_data)
        await update_status(job_id, status="queued", reclaimed=True)
        await client.rpush(queue_name, json.dumps({
            "job_id": job_id,
            "job_type": job_data["job_type"],
            "payload": job_data["payload"],
        }))
        await client.zrem(f"{INFLIGHT_KEY_PREFIX}{queue_name}", job_id)
        reclaimed.append(job_id)

    return reclaimed


async def update_status(job_id: str, **fields: Any) -> None:
    """Merges the given fields into the job's status hash in Redis."""
    client = get_client()
    string_fields = {k: json.dumps(v) for k, v in fields.items()}
    await client.hset(f"{STATUS_KEY_PREFIX}{job_id}", mapping=string_fields)
    await client.expire(f"{STATUS_KEY_PREFIX}{job_id}", STATUS_TTL_SECONDS)


async def get_status(job_id: str) -> dict | None:
    """Returns the current status fields for a job, or None if unknown."""
    client = get_client()
    raw = await client.hgetall(f"{STATUS_KEY_PREFIX}{job_id}")
    if not raw:
        return None
    return {k: json.loads(v) for k, v in raw.items()}

CHECKPOINT_KEY_PREFIX = "checkpoint:"


async def mark_chunk_done(job_id: str, chunk_index: int) -> None:
    """Records that a specific chunk has been successfully processed
    for this job. Used to resume a job from where it left off instead
    of reprocessing (and re-paying for) chunks already completed."""
    client = get_client()
    key = f"{CHECKPOINT_KEY_PREFIX}{job_id}"
    await client.sadd(key, chunk_index)
    await client.expire(key, STATUS_TTL_SECONDS)


async def get_completed_chunks(job_id: str) -> set[int]:
    """Returns the set of chunk indices already completed for this
    job, so a handler can skip them on resume. Empty set for a job
    that's never been checkpointed (including one running for the
    first time)."""
    client = get_client()
    raw = await client.smembers(f"{CHECKPOINT_KEY_PREFIX}{job_id}")
    return {int(x) for x in raw}


async def clear_checkpoint(job_id: str) -> None:
    """Removes checkpoint data once a job reaches a terminal state —
    no need to keep tracking per-chunk progress for a job that's
    already done or has permanently failed."""
    client = get_client()
    await client.delete(f"{CHECKPOINT_KEY_PREFIX}{job_id}")
