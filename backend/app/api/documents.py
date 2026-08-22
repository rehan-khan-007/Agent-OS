"""
Document upload API: lets a user upload a .txt, .md, or .pdf file
through the app itself (instead of running a local ingestion script),
and have it chunked, embedded, and stored for retrieval.

Embedding a full document can take a while (one API call per chunk,
sequentially) — long enough to exceed typical HTTP request timeouts.
So the upload endpoint saves the file, kicks off ingestion as a
background task, and returns immediately with a status the client can
poll instead of blocking the request until ingestion finishes.
"""

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.database import async_session
from app.retrieval.ingestion import load_document
from app.retrieval.chunking import chunk_text
from app.retrieval.embeddings import embed_text
from app.retrieval.models import DocumentChunk

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

# In-memory status tracking. Fine for a single-instance deployment;
# would need to move to Redis/DB if this ever runs on multiple workers.
_upload_status: dict[str, dict] = {}


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    status: str


class StatusResponse(BaseModel):
    upload_id: str
    filename: str
    status: str  # "processing" | "done" | "failed"
    chunks_processed: int = 0
    chunks_total: int = 0
    error: str | None = None


async def _ingest_in_background(upload_id: str, file_path: Path, filename: str):
    try:
        text = load_document(str(file_path))
        chunks = chunk_text(text, chunk_size=1000, overlap=100)
        _upload_status[upload_id]["chunks_total"] = len(chunks)

        async with async_session() as session:
            for chunk in chunks:
                try:
                    vector = embed_text(chunk.text)
                except Exception as e:
                    # Skip chunks that fail to embed rather than aborting
                    # the whole document — partial ingestion is better
                    # than none, and the error is still visible in status.
                    _upload_status[upload_id].setdefault("skipped_chunks", []).append(
                        {"index": chunk.index, "error": str(e)}
                    )
                    continue

                row = DocumentChunk(
                    source=filename,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    embedding=vector,
                )
                session.add(row)
                _upload_status[upload_id]["chunks_processed"] += 1

            await session.commit()

        _upload_status[upload_id]["status"] = "done"
    except Exception as e:
        _upload_status[upload_id]["status"] = "failed"
        _upload_status[upload_id]["error"] = str(e)
    finally:
        file_path.unlink(missing_ok=True)


@router.post("/upload", response_model=UploadResponse)
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
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

    upload_id = str(uuid.uuid4())
    tmp_path = Path(tempfile.gettempdir()) / f"{upload_id}{suffix}"
    tmp_path.write_bytes(contents)

    _upload_status[upload_id] = {
        "filename": file.filename,
        "status": "processing",
        "chunks_processed": 0,
        "chunks_total": 0,
    }

    background_tasks.add_task(_ingest_in_background, upload_id, tmp_path, file.filename)

    return UploadResponse(upload_id=upload_id, filename=file.filename, status="processing")


@router.get("/upload/{upload_id}/status", response_model=StatusResponse)
async def upload_status(upload_id: str):
    status = _upload_status.get(upload_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown upload_id.")
    return StatusResponse(
        upload_id=upload_id,
        filename=status["filename"],
        status=status["status"],
        chunks_processed=status["chunks_processed"],
        chunks_total=status["chunks_total"],
        error=status.get("error"),
    )
