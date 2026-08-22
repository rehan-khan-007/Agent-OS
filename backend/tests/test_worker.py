"""
End-to-end test for the worker loop: enqueues a real job onto Redis,
runs the worker loop against it, and verifies the job actually gets
picked up, processed, and its status updated. Requires real Redis —
there's no meaningful way to test a queue consumer without one.
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.config import settings
from app.queue import redis_queue, worker

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
async def test_worker_processes_enqueued_job(monkeypatch):
    call_log = []

    async def fake_handler(job_id, payload):
        call_log.append((job_id, payload))
        await redis_queue.update_status(job_id, status="done", result="fake result")

    monkeypatch.setitem(worker.HANDLERS, "test_job", fake_handler)

    orig_dequeue = redis_queue.dequeue

    async def fast_dequeue(queue_name, timeout=1):
        return await orig_dequeue(queue_name, timeout=1)

    monkeypatch.setattr(redis_queue, "dequeue", fast_dequeue)

    queue_name = f"test_worker_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(queue_name, "test_job", {"value": 42})

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(
        worker.worker_loop(queue_name, worker_id="test-worker", stop_event=stop_event)
    )

    await asyncio.sleep(0.5)
    stop_event.set()
    await asyncio.wait_for(worker_task, timeout=5)

    assert len(call_log) == 1
    assert call_log[0][0] == job_id
    assert call_log[0][1] == {"value": 42}

    status = await redis_queue.get_status(job_id)
    assert status["status"] == "done"
    assert status["result"] == "fake result"


@requires_redis
@pytest.mark.asyncio
async def test_worker_marks_job_failed_when_handler_raises(monkeypatch):
    async def failing_handler(job_id, payload):
        raise ValueError("simulated processing failure")

    monkeypatch.setitem(worker.HANDLERS, "failing_job", failing_handler)

    orig_dequeue = redis_queue.dequeue

    async def fast_dequeue(queue_name, timeout=1):
        return await orig_dequeue(queue_name, timeout=1)

    monkeypatch.setattr(redis_queue, "dequeue", fast_dequeue)

    queue_name = f"test_worker_fail_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(queue_name, "failing_job", {})

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(
        worker.worker_loop(queue_name, worker_id="test-worker", stop_event=stop_event)
    )

    await asyncio.sleep(0.5)
    stop_event.set()
    await asyncio.wait_for(worker_task, timeout=5)

    status = await redis_queue.get_status(job_id)
    assert status["status"] == "failed"
    assert "simulated processing failure" in status["error"]


@requires_redis
@pytest.mark.asyncio
async def test_worker_handles_unknown_job_type_gracefully(monkeypatch):
    orig_dequeue = redis_queue.dequeue

    async def fast_dequeue(queue_name, timeout=1):
        return await orig_dequeue(queue_name, timeout=1)

    monkeypatch.setattr(redis_queue, "dequeue", fast_dequeue)

    queue_name = f"test_worker_unknown_q_{uuid.uuid4()}"
    job_id = await redis_queue.enqueue(queue_name, "totally_unregistered_job_type", {})

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(
        worker.worker_loop(queue_name, worker_id="test-worker", stop_event=stop_event)
    )

    await asyncio.sleep(0.5)
    stop_event.set()
    await asyncio.wait_for(worker_task, timeout=5)

    status = await redis_queue.get_status(job_id)
    assert status["status"] == "failed"
    assert "Unknown job type" in status["error"]
