"""
Regression test for a real correctness bug found via architecture
review: _handle_ingest_document() used to call mark_chunk_done() for
each chunk INSIDE the same loop that adds it to the DB session, but
the actual session.commit() only happened once, after the loop
finished. If that commit ever failed (a dropped connection, a bad
row — see the real NUL-byte bug hit earlier ingesting this project's
own corpus), Redis would already believe every chunk was durably
checkpointed as done, while Postgres held none of them. A retry via
reclaim_stale_jobs() would then skip every "already done" chunk
forever — silent, permanent data loss with no visible error.

This test simulates exactly that failure (a commit that always
raises) and asserts NO chunk gets checkpointed as done — proving the
fix (checkpointing only after a successful commit) actually holds.

Needs real Redis to check get_completed_chunks() accurately. Does
NOT need a real database or a real embedding API call: session.add()
only stages objects in memory (no I/O until commit/flush), and
commit() itself is monkeypatched to fail before ever attempting a
real connection — so this test is free and fast regardless of what
DATABASE_URL points to.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.queue import redis_queue, worker

requires_redis = pytest.mark.skipif(
    not settings.redis_url or "localhost" in settings.redis_url,
    reason="No real Redis instance configured (REDIS_URL unset or points to localhost)",
)


@requires_redis
@pytest.mark.asyncio
async def test_checkpoint_not_written_if_db_commit_fails(monkeypatch):
    job_id = f"test-checkpoint-fail-{uuid.uuid4()}"

    def fake_embed_chunks(texts):
        # No real API call — deterministic fake vectors, same
        # dimensionality as the real embedding model. Plain (not
        # async) function, matching embed_chunks()'s real signature —
        # worker.py calls it without await, so an async replacement
        # here would return an un-run coroutine instead of data.
        return [[0.1] * 1536 for _ in texts]

    monkeypatch.setattr(worker, "embed_chunks", fake_embed_chunks)

    async def failing_commit(self):
        raise RuntimeError("simulated database commit failure")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("A small test document with enough text to form at least one chunk.")
        temp_path = f.name

    try:
        with pytest.raises(RuntimeError, match="simulated database commit failure"):
            await worker._handle_ingest_document(
                job_id, {"file_path": temp_path, "filename": "test.txt"}
            )

        completed = await redis_queue.get_completed_chunks(job_id)
        assert completed == set(), (
            "mark_chunk_done() was called even though the database commit "
            "failed — a retry would skip chunks that were never actually "
            "persisted, silently losing data."
        )
    finally:
        os.unlink(temp_path)
        await redis_queue.clear_checkpoint(job_id)
