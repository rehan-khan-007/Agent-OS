"""
Hybrid retrieval: combines vector similarity search and BM25 keyword
search into a single ranking via Reciprocal Rank Fusion (RRF).

RRF is a standard, well-established technique for combining ranked
result lists from different retrieval methods — it doesn't need the
two methods' raw scores to be on comparable scales (vector cosine
distance and BM25 scores aren't), because it only uses each result's
*rank position* within its own list, not the score value itself. A
chunk's final RRF score is the sum, across every list it appears in,
of 1 / (k + rank), where k is a smoothing constant (60 is the
standard value from the original RRF paper and widely used since).
A chunk ranked highly by either method — or moderately by both —
scores well; a chunk found by neither doesn't appear at all.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.embeddings import embed_text
from app.retrieval.models import DocumentChunk
from app.retrieval.bm25_search import bm25_search

RRF_K = 60


def reciprocal_rank_fusion(*ranked_lists: list) -> list:
    """
    Pure fusion logic, deliberately separated from any I/O so it can
    be tested directly against synthetic ranked lists without needing
    a real database or embedding API call.

    Each argument is a list of objects with an `.id` attribute,
    already ranked best-first by that method. Returns a single list
    of the distinct objects across all input lists, ordered by
    combined RRF score (highest first).
    """
    rrf_scores: dict = {}
    items_by_id: dict = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            rrf_scores[item.id] = rrf_scores.get(item.id, 0.0) + 1.0 / (RRF_K + rank)
            items_by_id[item.id] = item

    ranked_ids = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)
    return [items_by_id[i] for i in ranked_ids]


async def _vector_search_ranked(query: str, session: AsyncSession, top_k: int) -> list[DocumentChunk]:
    """Same query pgvector already supports, kept local to this module
    (rather than importing pipeline.py's wrapper) to avoid a circular
    import if pipeline.py is later updated to call into hybrid.py."""
    query_vector = embed_text(query)
    stmt = (
        select(DocumentChunk)
        .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def hybrid_search(
    query: str,
    session: AsyncSession,
    top_k: int = 3,
    candidate_pool_size: int = 10,
) -> list[DocumentChunk]:
    """
    Runs vector search and BM25 search independently (each returning
    up to candidate_pool_size candidates), fuses their rankings via
    RRF, and returns the final top_k chunks.

    candidate_pool_size is larger than top_k deliberately: RRF needs
    enough candidates from each method to meaningfully combine ranks
    — pulling only the final top_k from each source first would
    throw away exactly the kind of near-miss candidates that hybrid
    search is meant to rescue (e.g. a chunk vector search ranked #7
    but BM25 ranked #2, which should plausibly end up in the final
    top 3).
    """
    vector_results = await _vector_search_ranked(query, session, candidate_pool_size)
    bm25_results_with_scores = await bm25_search(query, session, candidate_pool_size)
    bm25_results = [chunk for chunk, _score in bm25_results_with_scores]

    fused = reciprocal_rank_fusion(vector_results, bm25_results)
    return fused[:top_k]
