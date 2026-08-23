"""
One-off script: adds an HNSW index on document_chunks.embedding for
cosine similarity search.

Without this, every vector search does a brute-force scan across
every row in the table — fine at a few hundred rows (this project's
original scale), genuinely slow once the corpus grows into the
thousands (7,700+ chunks as of this corpus expansion).

HNSW (vs. pgvector's other option, IVFFlat) is used because it
doesn't need a `lists` parameter tuned to the row count to perform
well, and gives strong query performance out of the box — a
reasonable tradeoff against slightly longer index build time and
higher memory use, both trivial at this project's actual scale.

vector_cosine_ops specifically, since the retrieval query
(_vector_search_ranked in app/retrieval/hybrid.py) uses
.cosine_distance() — the index's distance operator must match the
query's distance operator or it won't be used at all.

Run from backend/ with: python -m scripts.add_vector_index
"""

import asyncio
import time

from sqlalchemy import text

from app.database import engine


async def main():
    async with engine.begin() as conn:
        print("Checking current row count...")
        result = await conn.execute(text("SELECT COUNT(*) FROM document_chunks"))
        row_count = result.scalar()
        print(f"  {row_count} rows in document_chunks")

        print("\nCreating HNSW index (this may take a few seconds)...")
        start = time.time()
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx "
            "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
        ))
        duration = time.time() - start
        print(f"  Index created (or already existed) in {duration:.2f}s")

        # Confirm the index actually exists and get its size, as real
        # evidence rather than just trusting the CREATE INDEX call.
        result = await conn.execute(text(
            "SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass)) "
            "FROM pg_indexes WHERE tablename = 'document_chunks' AND indexname = 'document_chunks_embedding_hnsw_idx'"
        ))
        row = result.fetchone()
        if row:
            print(f"\nConfirmed: index '{row[0]}' exists, size {row[1]}")
        else:
            print("\nWARNING: index does not appear in pg_indexes — something went wrong")


if __name__ == "__main__":
    asyncio.run(main())
