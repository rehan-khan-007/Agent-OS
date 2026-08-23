"""
Load test for the task queue + worker infrastructure — the real
production code (app/queue/redis_queue.py, app/queue/worker.py),
not a mock. Zero LLM/API cost: the job handler used here is a stub
that does no external calls, so this measures the orchestration
layer itself (enqueue, dequeue, worker concurrency, status tracking,
failure handling), not answer quality.

Usage:
    REDIS_URL=<your Upstash URL> python3 scripts/load_test_queue.py [N] [failure_rate]

    N: number of jobs to enqueue (default 200)
    failure_rate: fraction of jobs deliberately made to fail, to
        produce a genuine (not assumed) completion percentage
        (default 0.02, i.e. ~2% of jobs simulate a real failure)

Run against a REAL Redis instance (Upstash or local) — this exercises
actual network round-trips to Redis, same as production.
"""

import asyncio
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.queue import redis_queue, worker as worker_module

LOAD_TEST_QUEUE = "load_test_queue"


async def _stub_handler(job_id: str, payload: dict) -> None:
    """
    Simulates real work with no external API calls: a short random
    delay (standing in for the kind of I/O-bound work a real job
    does) and a deliberately injected failure for a subset of jobs,
    determined at enqueue time via the payload — not randomized here,
    so the test's expected failure count is known in advance and the
    measured completion rate can be checked against it.
    """
    await asyncio.sleep(random.uniform(0.01, 0.08))
    if payload.get("should_fail"):
        raise RuntimeError("Simulated failure (deliberately injected for this test)")
    await redis_queue.update_status(job_id, status="done")


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    failure_rate = float(sys.argv[2]) if len(sys.argv) > 2 else 0.02

    worker_module.HANDLERS["load_test_job"] = _stub_handler

    # Clear any leftover jobs from a previous (possibly crashed) run
    # of this script sharing the same queue name, so this run starts
    # from a clean, known state.
    client = redis_queue.get_client()
    leftover = await client.delete(LOAD_TEST_QUEUE)
    if leftover:
        print(f"Cleared {leftover} leftover job(s) from a previous run on this queue.")

    expected_failures = round(n * failure_rate)
    should_fail_flags = [True] * expected_failures + [False] * (n - expected_failures)
    random.shuffle(should_fail_flags)

    print(f"Enqueueing {n} jobs ({expected_failures} deliberately set to fail, "
          f"{n - expected_failures} expected to succeed)...")

    job_ids = []
    enqueue_start = time.time()
    for flag in should_fail_flags:
        job_id = await redis_queue.enqueue(LOAD_TEST_QUEUE, "load_test_job", {"should_fail": flag})
        job_ids.append(job_id)
    enqueue_duration = time.time() - enqueue_start
    print(f"Enqueued {n} jobs in {enqueue_duration:.2f}s ({n / enqueue_duration:.1f} jobs/sec)")

    stop_event = asyncio.Event()
    num_workers = 4
    worker_tasks = [
        asyncio.create_task(worker_module.worker_loop(LOAD_TEST_QUEUE, f"load-test-worker-{i}", stop_event))
        for i in range(num_workers)
    ]
    print(f"Started {num_workers} workers, processing...")

    # Checking hundreds of job statuses concurrently via a single
    # gather() overwhelms Redis's default connection pool limit
    # (hit this for real while building this test: MaxConnectionsError
    # at 200 concurrent connections). A semaphore caps how many status
    # checks run at once, client-side, so the pool never gets
    # hammered with hundreds of simultaneous requests.
    status_semaphore = asyncio.Semaphore(20)

    async def _get_status_limited(job_id):
        async with status_semaphore:
            return await redis_queue.get_status(job_id)

    process_start = time.time()
    # Poll until every job reaches a terminal state (done or failed),
    # or a generous timeout elapses.
    max_wait_seconds = 120
    while time.time() - process_start < max_wait_seconds:
        statuses = await asyncio.gather(*[_get_status_limited(jid) for jid in job_ids])
        terminal = sum(1 for s in statuses if s and s.get("status") in ("done", "failed"))
        if terminal == n:
            break
        await asyncio.sleep(0.5)
    process_duration = time.time() - process_start

    stop_event.set()
    await asyncio.gather(*worker_tasks, return_exceptions=True)

    final_statuses = await asyncio.gather(*[_get_status_limited(jid) for jid in job_ids])
    done_count = sum(1 for s in final_statuses if s and s.get("status") == "done")
    failed_count = sum(1 for s in final_statuses if s and s.get("status") == "failed")
    unresolved_count = n - done_count - failed_count

    print()
    print("=" * 50)
    print(f"Total jobs:          {n}")
    print(f"Completed (done):    {done_count}")
    print(f"Failed (expected):   {failed_count}  (deliberately injected: {expected_failures})")
    print(f"Unresolved/timeout:  {unresolved_count}")
    print(f"Processing time:     {process_duration:.2f}s")
    print(f"Throughput:          {n / process_duration:.1f} jobs/sec ({num_workers} workers)")
    completion_rate = (done_count + failed_count) / n * 100
    success_rate = done_count / n * 100
    print(f"Completion rate (reached a terminal state): {completion_rate:.1f}%")
    print(f"Success rate (done, excluding deliberate failures): {success_rate:.1f}%")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
