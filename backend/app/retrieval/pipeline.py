"""
Retrieval pipeline: given a user's query, finds the most semantically
similar chunks already stored in the database.

Flow: query text -> embed it -> ask Postgres (via pgvector) for the
closest stored vectors -> return those chunks.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.embeddings import embed_text
from app.retrieval.models import DocumentChunk


async def retrieve_relevant_chunks(
    query: str,
    session: AsyncSession,
    top_k: int = 3,
) -> list[DocumentChunk]:
    """
    Embeds the query and returns the top_k most similar chunks stored
    in the database, ordered by similarity (closest first).

    Args:
        query: the user's question or search text
        session: an active database session
        top_k: how many chunks to return

    Returns:
        A list of DocumentChunk objects, most relevant first.
    """
    query_vector = embed_text(query)

    # pgvector's <-> operator computes distance between vectors —
    # smaller distance means more similar. order_by ascending distance
    # gives us the closest (most relevant) chunks first.
    stmt = (
        select(DocumentChunk)
        .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )

    result = await session.execute(stmt)
    return result.scalars().all()