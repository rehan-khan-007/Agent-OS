"""
Embeddings: converts text chunks into vector representations for
semantic similarity search during retrieval.
"""

import httpx

from app.config import settings

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_URL = "https://openrouter.ai/api/v1/embeddings"


def embed_text(text: str) -> list[float]:
    """Sends text to the embedding model and returns its vector."""
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


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    """Embeds multiple chunks sequentially."""
    return [embed_text(chunk) for chunk in chunks]