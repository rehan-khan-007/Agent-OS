"""
Tests for the Redis-backed job queue. Requires a real Redis instance
(same skip pattern as test_cache.py) — a queue cannot be meaningfully
tested against a fake/no-op backend, since its entire job is to
actually move data through Redis.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.config import settings
from app.queue import redis_queue

requires_redis = pytest.mark.skipif(
    not settings.redis_url or "localhost" in settings.redis_url,
    reason="No real Redis instance configured (REDIS_URL unset or points to localhost)",
)


@pytest.fixture(autouse=True)
def _reset_redis_client_singleton():
    """Same event-loop-isolation fix as test_cache.py — see that file
    for why this is necessary."""
    redis_queue._client = None
    yield
    redis_queue._client = None


def test_get_client_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    with pytest.raises(redis_queue.QueueUnavailableError):
        redis_queue.get_client()


def test_get_client_raises_for_localhost_placeholder(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    with pytest.raises(redis_queue.QueueUnavailableError):
        redis_queue.get_client()


@pytest.mark.asyncio
async def test_enqueue_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    with pytest.raises(redis_queue.QueueUnavailableError):
        await redis_queue.enqueue("q", "job_type", {})


@requires_redis
@pytest.mark.asyncio
async def test_enqueue_dequeue_roundtrip():
    queue_name = f"test_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(queue_name, "test_job", {"foo": "bar"})

    job = await redis_queue.dequeue(queue_name, timeout=2)
    assert job is not None
    assert job["job_id"] == job_id
    assert job["job_type"] == "test_job"
    assert job["payload"] == {"foo": "bar"}


@requires_redis
@pytest.mark.asyncio
async def test_dequeue_on_empty_queue_returns_none():
    queue_name = f"test_empty_{uuid.uuid4()}"
    result = await redis_queue.dequeue(queue_name, timeout=1)
    assert result is None


@requires_redis
@pytest.mark.asyncio
async def test_status_tracking_and_updates():
    job_id = await redis_queue.enqueue(f"test_q_{uuid.uuid4()}", "job", {})

    status = await redis_queue.get_status(job_id)
    assert status["status"] == "queued"

    await redis_queue.update_status(job_id, status="processing", progress=50)
    status2 = await redis_queue.get_status(job_id)
    assert status2["status"] == "processing"
    assert status2["progress"] == 50


@requires_redis
@pytest.mark.asyncio
async def test_unknown_job_id_returns_none_status():
    result = await redis_queue.get_status(f"nonexistent-{uuid.uuid4()}")
    assert result is None


@requires_redis
@pytest.mark.asyncio
async def test_jobs_dequeue_in_fifo_order():
    queue_name = f"test_fifo_{uuid.uuid4()}"
    await redis_queue.enqueue(queue_name, "job", {"n": 1})
    await redis_queue.enqueue(queue_name, "job", {"n": 2})
    await redis_queue.enqueue(queue_name, "job", {"n": 3})

    results = []
    for _ in range(3):
        job = await redis_queue.dequeue(queue_name, timeout=1)
        results.append(job["payload"]["n"])

    assert results == [1, 2, 3]


@requires_redis
@pytest.mark.asyncio
async def test_enqueue_with_initial_status_fields():
    job_id = await redis_queue.enqueue(
        f"test_q_{uuid.uuid4()}", "ingest_document",
        {"file_path": "/tmp/x.pdf", "filename": "doc.pdf"},
        filename="doc.pdf",
    )
    status = await redis_queue.get_status(job_id)
    assert status["status"] == "queued"
    assert status["filename"] == "doc.pdf"
