"""
Worker loop: pulls jobs off a Redis queue and dispatches them to the
right handler based on job_type. Runs as one or more background
asyncio tasks started in the app's lifespan (see main.py) — no
separate deployed process needed, since a single web-service instance
can run both the FastAPI app and its background workers in the same
event loop.

Adding a new job type: write an async handler function taking a
payload dict, register it in HANDLERS, done.
"""

import asyncio
import tempfile
import uuid
from pathlib import Path

from app.database import async_session
from app.observability.logging import get_logger
from app.queue import redis_queue
from app.queue.redis_queue import QueueUnavailableError
from app.retrieval.ingestion import load_document
from app.retrieval.chunking import chunk_text
from app.retrieval.embeddings import embed_chunks
from app.retrieval.models import DocumentChunk
from app.storage import r2_client

logger = get_logger(__name__)

DOCUMENT_QUEUE = "document_ingestion"


async def _handle_ingest_document(job_id: str, payload: dict) -> None:
    """
    Handler for 'ingest_document' jobs: load -> chunk -> embed -> store.

    Checkpoint-aware: before processing, loads the set of chunk
    indices already completed for this job_id (empty on a first run).
    Already-completed chunks are skipped entirely — no re-embedding,
    no re-storing. This is what makes reclaim_stale_jobs() actually
    useful: if a worker dies after embedding 150 of 200 chunks, the
    job gets re-queued, but resuming means paying for ~50 more
    embedding calls, not 200 all over again.

    Embedding uses embed_chunks(), which batches many chunks per API
    call instead of one call per chunk — at real ingestion scale
    (thousands of chunks), sequential one-at-a-time calls are
    dominated by network round-trip latency, not embedding compute,
    so batching is the single biggest lever for making large-corpus
    ingestion practical. Tradeoff worth naming: progress updates
    (chunks_processed) now land once per document rather than once
    per chunk, since embed_chunks() returns all its results together
    — a fair cost for the throughput win, but less granular live
    progress during a very large single-document ingestion.
    """
    filename = payload["filename"]
    r2_key = payload.get("r2_key")
    file_path = payload.get("file_path")

    already_done = await redis_queue.get_completed_chunks(job_id)
    resuming = len(already_done) > 0

    await redis_queue.update_status(
        job_id, status="processing", chunks_processed=len(already_done), chunks_total=0
    )

    # If this job's payload references an R2 object (the normal case
    # once R2 is configured) rather than a local file_path (local-dev
    # fallback), download it to a fresh local temp file first — the
    # text-extraction libraries this project uses read from a real
    # local path, not a byte stream. On a resumed/retried job, this
    # correctly re-downloads rather than assuming a previous worker's
    # local temp copy still exists — it wouldn't, if that worker died
    # or ran on a different machine.
    downloaded_temp_path = None
    if r2_key:
        suffix = Path(filename).suffix
        downloaded_temp_path = str(Path(tempfile.gettempdir()) / f"{uuid.uuid4()}{suffix}")
        await r2_client.download_to_path(r2_key, downloaded_temp_path)
        file_path = downloaded_temp_path

    try:
        text = load_document(file_path)
    finally:
        if downloaded_temp_path:
            Path(downloaded_temp_path).unlink(missing_ok=True)

    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    await redis_queue.update_status(job_id, chunks_total=len(chunks))

    if resuming:
        logger.info(
            "Resuming job from checkpoint",
            extra={"extra_fields": {"job_id": job_id, "already_done": len(already_done), "total": len(chunks)}},
        )

    remaining = [c for c in chunks if c.index not in already_done]
    vectors = embed_chunks([c.text for c in remaining])

    processed = len(already_done)
    skipped = []
    # Indices actually added to the session this round — checkpointed
    # only after the commit below succeeds, not before. Checkpointing
    # per-chunk *inside* this loop (as an earlier version of this code
    # did) is a real correctness bug: mark_chunk_done() would succeed
    # in Redis for every chunk before the single session.commit() at
    # the end ever ran. If that commit failed for any reason (a
    # dropped connection, a bad row — see the NUL-byte bug found
    # earlier ingesting this project's own corpus), Redis would
    # permanently believe every chunk was durably stored while
    # Postgres held zero of them — and a retry would skip them all
    # forever, since the checkpoint says "already done". Checkpoint
    # writes now only happen after commit() has actually succeeded.
    newly_committed_indices = []

    async with async_session() as session:
        for chunk, vector in zip(remaining, vectors):
            if vector is None:
                skipped.append({"index": chunk.index, "error": "embedding failed after retries"})
                continue

            session.add(DocumentChunk(
                source=filename,
                chunk_index=chunk.index,
                text=chunk.text,
                embedding=vector,
            ))
            processed += 1
            newly_committed_indices.append(chunk.index)

        await redis_queue.update_status(job_id, chunks_processed=processed)
        await redis_queue.touch_job(DOCUMENT_QUEUE, job_id)
        await session.commit()

    # Only reached if commit() above did not raise — if it did, the
    # exception propagates out of this handler, _process_job() marks
    # the job "failed", and reclaim can retry it from a checkpoint
    # that (correctly) still shows none of these chunks as done.
    for index in newly_committed_indices:
        await redis_queue.mark_chunk_done(job_id, index)

    await redis_queue.update_status(
        job_id,
        status="done",
        chunks_processed=processed,
        skipped_chunks=skipped,
    )
    await redis_queue.clear_checkpoint(job_id)

    if r2_key:
        # Safe to delete now — ingestion fully succeeded, so this
        # source file will never need to be re-downloaded for a retry.
        await r2_client.delete_object(r2_key)


HANDLERS = {
    "ingest_document": _handle_ingest_document,
}


async def _process_job(job: dict, queue_name: str) -> None:
    job_id = job["job_id"]
    job_type = job["job_type"]
    payload = job["payload"]

    handler = HANDLERS.get(job_type)
    if handler is None:
        logger.error("No handler registered for job type", extra={"extra_fields": {"job_type": job_type}})
        await redis_queue.update_status(job_id, status="failed", error=f"Unknown job type: {job_type}")
        await redis_queue.mark_job_complete(queue_name, job_id)
        return

    try:
        await handler(job_id, payload)
        await redis_queue.mark_job_complete(queue_name, job_id)
    except Exception as e:
        logger.error(
            "Job failed",
            extra={"extra_fields": {"job_id": job_id, "job_type": job_type, "error": str(e)}},
        )
        await redis_queue.update_status(job_id, status="failed", error=str(e))
        # Deliberately still marked complete (not left in-flight) even
        # on failure — a handler-level failure (e.g. a corrupt file)
        # will fail identically on every retry, so leaving it in-flight
        # for reclaim would just loop forever rather than resolve.
        await redis_queue.mark_job_complete(queue_name, job_id)


async def worker_loop(queue_name: str, worker_id: str, stop_event: asyncio.Event) -> None:
    """
    Continuously dequeues and processes jobs until stop_event is set.
    The dequeue timeout (5s) means the loop checks stop_event
    periodically instead of blocking forever, so shutdown is prompt.

    The while-loop body is wrapped in a broad except, not just
    QueueUnavailableError. This matters a lot: this loop runs inside
    an asyncio.create_task() that nothing actively awaits until
    shutdown. If a network-level exception from the Redis client
    (a timeout, a connection reset, anything not explicitly caught)
    ever escaped this loop, the whole task would silently die —
    logging nothing, and never processing another job again — with no
    visible symptom other than jobs mysteriously sitting in "queued"
    forever. That's a real failure mode this project hit while
    building and deploying this exact worker.
    """
    logger.info("Worker started", extra={"extra_fields": {"worker_id": worker_id, "queue": queue_name}})

    while not stop_event.is_set():
        try:
            job = await redis_queue.dequeue(queue_name, timeout=5)
        except QueueUnavailableError as e:
            logger.error("Worker cannot reach queue, retrying shortly", extra={"extra_fields": {"error": str(e)}})
            await asyncio.sleep(5)
            continue
        except Exception as e:
            # Any other failure talking to Redis (timeout, connection
            # reset, etc.) — log it and keep the worker alive instead
            # of letting the loop die silently.
            logger.error(
                "Unexpected error while dequeuing, worker staying alive and retrying",
                extra={"extra_fields": {"worker_id": worker_id, "error": str(e), "error_type": type(e).__name__}},
            )
            await asyncio.sleep(2)
            continue

        if job is None:
            await asyncio.sleep(1)  # nothing to do — brief pause before polling again
            continue

        logger.info("Processing job", extra={"extra_fields": {"job_id": job["job_id"], "worker_id": worker_id}})
        try:
            await _process_job(job, queue_name)
        except Exception as e:
            # _process_job already catches handler-level failures and
            # writes status="failed" for the job itself. This outer
            # catch is a last-resort safety net so a bug in
            # _process_job's own bookkeeping can't kill the worker.
            logger.error(
                "Unexpected error processing job, worker staying alive",
                extra={"extra_fields": {"worker_id": worker_id, "job_id": job.get("job_id"), "error": str(e)}},
            )

    logger.info("Worker stopped", extra={"extra_fields": {"worker_id": worker_id}})

RECLAIM_INTERVAL_SECONDS = 30
RECLAIM_STALE_AFTER_SECONDS = 120  # a job with no progress update in 2 minutes is presumed abandoned


async def reclaim_loop(queue_name: str, stop_event: asyncio.Event) -> None:
    """
    Periodically checks for jobs that have been in-flight (dequeued
    but never completed) longer than RECLAIM_STALE_AFTER_SECONDS, and
    re-queues them. This is what actually recovers from a worker
    crashing mid-job — without this running, reclaim_stale_jobs()
    exists but nothing ever calls it, and an abandoned job would sit
    forgotten forever.
    """
    logger.info("Reclaim loop started", extra={"extra_fields": {"queue": queue_name}})

    while not stop_event.is_set():
        try:
            reclaimed = await redis_queue.reclaim_stale_jobs(queue_name, RECLAIM_STALE_AFTER_SECONDS)
            if reclaimed:
                logger.info(
                    "Reclaimed stale jobs",
                    extra={"extra_fields": {"queue": queue_name, "job_ids": reclaimed, "count": len(reclaimed)}},
                )
        except Exception as e:
            logger.error(
                "Reclaim loop encountered an error, staying alive",
                extra={"extra_fields": {"queue": queue_name, "error": str(e)}},
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RECLAIM_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass  # normal — just means it's time to check again

    logger.info("Reclaim loop stopped", extra={"extra_fields": {"queue": queue_name}})
