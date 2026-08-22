"""
Tests for checkpoint/in-flight tracking (app/queue/redis_queue.py
additions) and the reclaim mechanism. Requires real Redis — this is
inherently about actual persisted state across simulated failures,
which can't be meaningfully mocked.
"""

import sys
import time
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
    redis_queue._client = None
    yield
    redis_queue._client = None


@requires_redis
@pytest.mark.asyncio
async def test_fresh_job_has_no_completed_chunks():
    job_id = f"test-job-{uuid.uuid4()}"
    result = await redis_queue.get_completed_chunks(job_id)
    assert result == set()


@requires_redis
@pytest.mark.asyncio
async def test_mark_and_retrieve_completed_chunks():
    job_id = f"test-job-{uuid.uuid4()}"
    await redis_queue.mark_chunk_done(job_id, 0)
    await redis_queue.mark_chunk_done(job_id, 3)
    await redis_queue.mark_chunk_done(job_id, 7)

    result = await redis_queue.get_completed_chunks(job_id)
    assert result == {0, 3, 7}


@requires_redis
@pytest.mark.asyncio
async def test_clear_checkpoint_removes_progress():
    job_id = f"test-job-{uuid.uuid4()}"
    await redis_queue.mark_chunk_done(job_id, 0)
    await redis_queue.clear_checkpoint(job_id)

    result = await redis_queue.get_completed_chunks(job_id)
    assert result == set()


@requires_redis
@pytest.mark.asyncio
async def test_enqueue_persists_durable_job_data():
    queue_name = f"test_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(
        queue_name, "ingest_document", {"file_path": "/tmp/x.pdf", "filename": "x.pdf"}
    )

    client = redis_queue.get_client()
    raw = await client.get(f"job_data:{job_id}")
    assert raw is not None


@requires_redis
@pytest.mark.asyncio
async def test_dequeue_marks_job_inflight_and_complete_removes_it():
    queue_name = f"test_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(queue_name, "job", {})

    job = await redis_queue.dequeue(queue_name, timeout=1)
    client = redis_queue.get_client()

    score = await client.zscore(f"inflight:{queue_name}", job_id)
    assert score is not None, "job should be tracked as in-flight after dequeue"

    await redis_queue.mark_job_complete(queue_name, job_id)
    score_after = await client.zscore(f"inflight:{queue_name}", job_id)
    assert score_after is None, "job should be removed from in-flight after completion"


@requires_redis
@pytest.mark.asyncio
async def test_reclaim_requeues_abandoned_job_with_original_payload():
    queue_name = f"test_reclaim_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(
        queue_name, "ingest_document", {"file_path": "/tmp/z.pdf", "filename": "z.pdf"}
    )
    job = await redis_queue.dequeue(queue_name, timeout=1)
    assert job["job_id"] == job_id

    # Simulate the worker having abandoned this job a while ago
    client = redis_queue.get_client()
    await client.zadd(f"inflight:{queue_name}", {job_id: time.time() - 200})

    reclaimed = await redis_queue.reclaim_stale_jobs(queue_name, stale_after_seconds=60)
    assert job_id in reclaimed

    requeued_len = await client.llen(queue_name)
    assert requeued_len == 1

    job_again = await redis_queue.dequeue(queue_name, timeout=1)
    assert job_again["job_id"] == job_id
    assert job_again["payload"]["filename"] == "z.pdf"


@requires_redis
@pytest.mark.asyncio
async def test_reclaim_does_not_touch_recently_dequeued_jobs():
    queue_name = f"test_reclaim_fresh_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(queue_name, "job", {})
    await redis_queue.dequeue(queue_name, timeout=1)
    # No backdating this time — job was JUST dequeued, should not be reclaimable yet

    reclaimed = await redis_queue.reclaim_stale_jobs(queue_name, stale_after_seconds=60)
    assert job_id not in reclaimed


@requires_redis
@pytest.mark.asyncio
async def test_touch_job_refreshes_inflight_timestamp():
    queue_name = f"test_touch_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(queue_name, "job", {})
    await redis_queue.dequeue(queue_name, timeout=1)

    client = redis_queue.get_client()
    # Backdate it so it WOULD be stale...
    await client.zadd(f"inflight:{queue_name}", {job_id: time.time() - 200})

    # ...but then touch it, simulating the handler reporting progress
    await redis_queue.touch_job(queue_name, job_id)

    # Now it should NOT be reclaimed, since its timestamp was just refreshed
    reclaimed = await redis_queue.reclaim_stale_jobs(queue_name, stale_after_seconds=60)
    assert job_id not in reclaimed
