"""
Chunking: splits a long document into smaller overlapping pieces.

Why chunk at all? Embedding models and LLM context windows both have
limits, and retrieval works better on focused passages than on whole
documents. Overlap between chunks helps avoid losing context that
straddles

 a chunk boundary.

This is a simple fixed-size chunker to start. A smarter version later
could split on paragraph/sentence boundaries instead of raw character
counts — that's a good candidate for a follow-up commit + ADR.
"""

from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    index: int  # position of this chunk within the document


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[Chunk]:
    """
    Splits text into overlapping chunks of roughly `chunk_size` characters.

    Args:
        text: the full document text
        chunk_size: target number of characters per chunk
        overlap: number of characters shared between consecutive chunks,
                 so context isn't lost at chunk boundaries

    Returns:
        A list of Chunk objects, in order.
    """
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")

    chunks: list[Chunk] = []
    start = 0
    index = 0

    while start < len(text):
        end = start + chunk_size
        chunk_text_piece = text[start:end].strip()

        if chunk_text_piece:  # skip empty trailing chunks
            chunks.append(Chunk(text=chunk_text_piece, index=index))
            index += 1

        # move forward by (chunk_size - overlap) so chunks overlap
        start += chunk_size - overlap

    return chunks