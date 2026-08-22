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

from app.database import async_session
from app.observability.logging import get_logger
from app.queue import redis_queue
from app.queue.redis_queue import QueueUnavailableError
from app.retrieval.ingestion import load_document
from app.retrieval.chunking import chunk_text
from app.retrieval.embeddings import embed_text
from app.retrieval.models import DocumentChunk

logger = get_logger(__name__)

DOCUMENT_QUEUE = "document_ingestion"


async def _handle_ingest_document(job_id: str, payload: dict) -> None:
    """Handler for 'ingest_document' jobs: load -> chunk -> embed -> store."""
    file_path = payload["file_path"]
    filename = payload["filename"]

    await redis_queue.update_status(job_id, status="processing", chunks_processed=0, chunks_total=0)

    text = load_document(file_path)
    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    await redis_queue.update_status(job_id, chunks_total=len(chunks))

    processed = 0
    skipped = []

    async with async_session() as session:
        for chunk in chunks:
            try:
                vector = embed_text(chunk.text)
            except Exception as e:
                skipped.append({"index": chunk.index, "error": str(e)})
                continue

            session.add(DocumentChunk(
                source=filename,
                chunk_index=chunk.index,
                text=chunk.text,
                embedding=vector,
            ))
            processed += 1
            await redis_queue.update_status(job_id, chunks_processed=processed)

        await session.commit()

    await redis_queue.update_status(
        job_id,
        status="done",
        chunks_processed=processed,
        skipped_chunks=skipped,
    )


HANDLERS = {
    "ingest_document": _handle_ingest_document,
}


async def _process_job(job: dict) -> None:
    job_id = job["job_id"]
    job_type = job["job_type"]
    payload = job["payload"]

    handler = HANDLERS.get(job_type)
    if handler is None:
        logger.error("No handler registered for job type", extra={"extra_fields": {"job_type": job_type}})
        await redis_queue.update_status(job_id, status="failed", error=f"Unknown job type: {job_type}")
        return

    try:
        await handler(job_id, payload)
    except Exception as e:
        logger.error(
            "Job failed",
            extra={"extra_fields": {"job_id": job_id, "job_type": job_type, "error": str(e)}},
        )
        await redis_queue.update_status(job_id, status="failed", error=str(e))


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
            await _process_job(job)
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
