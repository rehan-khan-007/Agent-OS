"""
BM25 keyword search over stored document chunks.

Vector similarity (pipeline.py) is good at semantic matches — "how
does gradient ascent work" finding a chunk about GRAPE even without
those exact words. It's weaker at exact keyword/name matches — a
specific term, acronym, or proper noun can get diluted in a dense
embedding. BM25 is the complementary case: pure keyword/term-frequency
matching, no semantic understanding at all. Combining both (see
hybrid.py) covers more ground than either alone.

Implementation note: the BM25 index is built fresh, in memory, on
every search call, rather than persisted. At this project's actual
scale (a few hundred chunks), tokenizing and building a BM25Okapi
index takes well under 100ms — building it in-process is simpler and
correct-enough here. A larger corpus would need a persisted/
incrementally-updated index instead of rebuilding per query.

Caveat worth knowing: BM25's classic IDF formula can degenerate at
very small corpus sizes — with only 2 documents, a term appearing in
exactly 1 of them gets IDF = log((2-1+0.5)/(1+0.5)) = log(1) = 0,
zeroing out its contribution entirely. This isn't a bug, just BM25
math; it only matters for corpora with a handful of documents, well
below the scale this is actually used at here.
"""

import re

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import defer
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.models import DocumentChunk


def _tokenize(text: str) -> list[str]:
    """Simple lowercase word tokenizer — sufficient for BM25, which
    only needs term frequency, not linguistic understanding."""
    return re.findall(r"[a-z0-9]+", text.lower())


async def bm25_search(
    query: str,
    session: AsyncSession,
    top_k: int = 5,
) -> list[tuple[DocumentChunk, float]]:
    """
    Returns the top_k chunks ranked by BM25 keyword relevance to the
    query, each paired with its BM25 score (higher = more relevant).

    Returns an empty list if there are no chunks in the database at
    all — BM25Okapi raises on an empty corpus, so this is checked
    explicitly rather than letting that exception surface.

    Explicitly defers loading the `embedding` column: BM25 scoring
    only ever touches `.text`, but this query fetches every row in
    the table on every call. Without deferring it, the (by far
    largest) embedding vector for all rows gets transferred over the
    network on every single search — at real corpus scale (7,700+
    rows), this was found to be the dominant cause of exhausting
    Neon's free-tier data transfer quota during real testing, not
    the actual document ingestion. defer() keeps returning genuine
    DocumentChunk objects with identical attribute access for every
    other field, so nothing downstream needs to change.
    """
    result = await session.execute(select(DocumentChunk).options(defer(DocumentChunk.embedding)))
    all_chunks = result.scalars().all()

    if not all_chunks:
        return []

    tokenized_corpus = [_tokenize(chunk.text) for chunk in all_chunks]
    bm25 = BM25Okapi(tokenized_corpus)

    tokenized_query = _tokenize(query)
    scores = bm25.get_scores(tokenized_query)

    scored_chunks = list(zip(all_chunks, scores))
    scored_chunks.sort(key=lambda pair: pair[1], reverse=True)

    return scored_chunks[:top_k]
