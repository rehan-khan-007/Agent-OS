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
    up to 3 times with exponential backoff — important for large
    ingestion batches (hundreds of chunks, one blocking call each),
    where the odds of hitting at least one transient network hiccup
    rise with the batch size. 4xx errors are never retried."""
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


def embed_chunks(chunks: list[str]) -> list[list[float] | None]:
    """
    Embeds multiple chunks sequentially. If a chunk fails even after
    retries, its slot is None rather than a vector, and the failure
    is logged — callers must handle None entries. Partial ingestion
    (skip the bad chunk, keep the rest) is better than losing an
    entire large document to one persistently-failing chunk.
    """
    results = []
    for i, chunk in enumerate(chunks):
        try:
            results.append(embed_text(chunk))
        except Exception as e:
            logger.error(
                "Failed to embed chunk after retries, skipping",
                extra={"extra_fields": {"chunk_index": i, "error": str(e)}},
            )
            results.append(None)
    return results
