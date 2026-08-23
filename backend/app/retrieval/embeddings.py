"""
Embeddings: converts text chunks into vector representations for
semantic similarity search during retrieval.
"""

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.observability.logging import get_logger

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_URL = "https://openrouter.ai/api/v1/embeddings"

# How many texts to send per batched API call. The embeddings
# endpoint accepts a list of strings in one request (same
# OpenAI-compatible spec as chat completions) — batching turns N
# individual network round-trips into N/BATCH_SIZE, which is the
# actual bottleneck at real scale: sequential per-chunk calls are
# dominated by network latency, not compute, so cutting the *number*
# of round-trips matters far more than any per-call optimization.
BATCH_SIZE = 100

logger = get_logger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """
    Same policy as the LLM chat client: retry transient network
    failures and 5xx server errors, never retry a 4xx client error
    (a malformed request will fail identically every time, so
    retrying it just wastes calls and delays the real failure).
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
def embed_text(text: str) -> list[float]:
    """Sends text to the embedding model and returns its vector.

    Transient failures (timeout, connection error, 5xx) are retried
    up to 3 times with exponential backoff. 4xx errors are never
    retried. Used directly for single-text embedding (e.g. embedding
    a search query), and as the per-chunk fallback in embed_chunks
    when a batch call fails."""
    if not settings.openrouter_api_key:
        raise RuntimeError("openrouter_api_key is not set")

    response = httpx.post(
        EMBEDDING_URL,
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json={"model": EMBEDDING_MODEL, "input": text},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
def _embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Sends up to BATCH_SIZE texts in a single API call. The embeddings
    endpoint returns results in `data`, each with an `index` field
    matching its position in the input list — results are sorted by
    that index before returning, rather than trusting response order,
    since providers aren't required to preserve input order in the
    array itself (only the index field is a documented guarantee).
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("openrouter_api_key is not set")

    response = httpx.post(
        EMBEDDING_URL,
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()["data"]
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def embed_chunks(chunks: list[str]) -> list[list[float] | None]:
    """
    Embeds multiple chunks using batched API calls (BATCH_SIZE texts
    per request) instead of one call per chunk — at real ingestion
    scale (thousands of chunks), this is the difference between
    thousands of sequential network round-trips and a few dozen,
    since network latency (not embedding compute) is the actual
    bottleneck for one-at-a-time calls.

    If an entire batch fails even after its own retries (e.g. one
    malformed text poisons the whole batch), this falls back to
    embedding that batch's texts one at a time via embed_text, so a
    single bad chunk doesn't sacrifice the rest of a 100-chunk batch
    — consistent with the project's existing "partial ingestion beats
    total failure" approach. A chunk that still fails individually
    gets None in its slot, logged, and skipped, exactly as before.
    """
    results: list[list[float] | None] = [None] * len(chunks)

    for batch_start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[batch_start:batch_start + BATCH_SIZE]
        try:
            embeddings = _embed_batch(batch)
            for offset, vector in enumerate(embeddings):
                results[batch_start + offset] = vector
        except Exception as e:
            logger.error(
                "Batch embedding failed after retries, falling back to per-chunk embedding for this batch",
                extra={"extra_fields": {"batch_start": batch_start, "batch_size": len(batch), "error": str(e)}},
            )
            for offset, text in enumerate(batch):
                chunk_index = batch_start + offset
                try:
                    results[chunk_index] = embed_text(text)
                except Exception as inner_e:
                    logger.error(
                        "Failed to embed chunk after retries, skipping",
                        extra={"extra_fields": {"chunk_index": chunk_index, "error": str(inner_e)}},
                    )
                    results[chunk_index] = None

    return results
