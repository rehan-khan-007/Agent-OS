"""
Regression test for a real bug found in production: bm25_search()
was fetching every column of every row (including the large
`embedding` vector) on every single call, even though BM25 scoring
never touches it. At real corpus scale (7,700+ rows), this was the
dominant cause of exhausting a database's monthly network transfer
quota in a single day of testing — a genuine, costly bug, not a
theoretical one.

This test asserts the fix stays in place: after bm25_search() runs,
the returned DocumentChunk objects must have `embedding` in their
SQLAlchemy "unloaded" attribute set — proving it was never fetched
from the database at all, not just unused after being fetched.

Requires a real Postgres+pgvector database with at least one chunk
present (skips gracefully otherwise, same pattern as the Redis-
dependent tests in this suite) — this specifically cannot be
meaningfully mocked, since it's asserting something about what
SQLAlchemy actually requested from the database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.retrieval.bm25_search import bm25_search
from app.retrieval.models import DocumentChunk

requires_database = pytest.mark.skipif(
    not settings.database_url or "localhost" in settings.database_url,
    reason="No real DATABASE_URL configured (unset or points to localhost) — this test needs a real Postgres+pgvector database",
)


@requires_database
@pytest.mark.asyncio
async def test_bm25_search_never_loads_the_embedding_column():
    async with async_session() as session:
        # Confirm there's at least one real chunk to test against —
        # otherwise this test would pass vacuously (no rows to check).
        result = await session.execute(select(DocumentChunk).limit(1))
        if result.scalar_one_or_none() is None:
            pytest.skip("No chunks in the database to test against")

        results = await bm25_search("test query", session, top_k=3)

        assert len(results) > 0, "expected at least one result from a non-empty corpus"

        for chunk, _score in results:
            unloaded = inspect(chunk).unloaded
            assert "embedding" in unloaded, (
                "bm25_search() loaded the `embedding` column — this is the exact "
                "regression that exhausted a real database's monthly transfer quota "
                "in production. The query must use .options(defer(DocumentChunk.embedding)) "
                "so this large column is never fetched for BM25's text-only scoring."
            )
            # Sanity check: the fields BM25 actually needs should be loaded normally.
            assert "text" not in unloaded
            assert "source" not in unloaded
