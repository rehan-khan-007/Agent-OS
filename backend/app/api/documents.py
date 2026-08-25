"""
Document upload API: lets a user upload a .txt, .md, or .pdf file
through the app itself, and have it chunked, embedded, and stored for
retrieval.

Ingestion runs through a real Redis-backed job queue (app/queue/) —
the upload endpoint enqueues a job and returns immediately with an id
the client can poll; a background worker (started in main.py's
lifespan) picks the job up, processes it, and writes status updates
to Redis. This means job status survives a server restart (unlike an
in-process dict), and the same queue/worker pattern could take on
other job types beyond document ingestion without changing this file.
"""

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from pydantic import BaseModel

from app.queue.redis_queue import enqueue, get_status, QueueUnavailableError
from app.queue.worker import DOCUMENT_QUEUE
from app.ratelimit.limiter import rate_limit
from app.storage import r2_client

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

# Uploads are far more expensive than a chat turn — a single document
# can trigger hundreds of embedding API calls — so this limit is much
# tighter than the chat rate limit.
UPLOAD_RATE_LIMIT = rate_limit("documents_upload", limit=5, window_seconds=600)


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    status: str


class StatusResponse(BaseModel):
    upload_id: str
    filename: str
    status: str  # "queued" | "processing" | "done" | "failed"
    chunks_processed: int = 0
    chunks_total: int = 0
    error: str | None = None


@router.post("/upload", response_model=UploadResponse, dependencies=[Depends(UPLOAD_RATE_LIMIT)])
async def upload_document(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 20MB).")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    tmp_id = str(uuid.uuid4())

    if r2_client.is_configured():
        # Real, distributed-worker-safe path: store the upload in R2
        # and pass its object key through the queue, instead of a
        # local path that would only ever exist on this one process.
        r2_key = f"uploads/{tmp_id}{suffix}"
        await r2_client.upload_bytes(r2_key, contents)
        payload = {"r2_key": r2_key, "filename": file.filename}
    else:
        # Local-dev fallback when R2 isn't configured — preserves the
        # previous behavior so local development doesn't require R2
        # credentials just to test document ingestion.
        tmp_path = Path(tempfile.gettempdir()) / f"{tmp_id}{suffix}"
        tmp_path.write_bytes(contents)
        payload = {"file_path": str(tmp_path), "filename": file.filename}

    try:
        job_id = await enqueue(
            DOCUMENT_QUEUE,
            job_type="ingest_document",
            payload=payload,
            filename=file.filename,
        )
    except QueueUnavailableError as e:
        raise HTTPException(status_code=503, detail=f"Upload processing is temporarily unavailable: {e}")

    return UploadResponse(upload_id=job_id, filename=file.filename, status="queued")


@router.get("/upload/{upload_id}/status", response_model=StatusResponse)
async def upload_status(upload_id: str):
    try:
        status = await get_status(upload_id)
    except QueueUnavailableError as e:
        raise HTTPException(status_code=503, detail=f"Status lookup is temporarily unavailable: {e}")

    if status is None:
        raise HTTPException(status_code=404, detail="Unknown upload_id.")

    return StatusResponse(
        upload_id=upload_id,
        filename=status.get("filename", ""),
        status=status.get("status", "unknown"),
        chunks_processed=status.get("chunks_processed", 0),
        chunks_total=status.get("chunks_total", 0),
        error=status.get("error"),
    )
