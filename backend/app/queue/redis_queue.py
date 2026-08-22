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
import uuid
from typing import Any

import redis.asyncio as redis

from app.config import settings

_client: redis.Redis | None = None

STATUS_KEY_PREFIX = "job_status:"
STATUS_TTL_SECONDS = 60 * 60 * 24  # keep job status visible for 24h


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

    Raises QueueUnavailableError if Redis isn't reachable — callers
    must handle this rather than assume the job was queued."""
    client = get_client()

    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "job_type": job_type, "payload": payload}

    await update_status(job_id, status="queued", **initial_status)
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
    """
    client = get_client()
    raw_job = await client.lpop(queue_name)
    if raw_job is None:
        return None
    return json.loads(raw_job)


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
