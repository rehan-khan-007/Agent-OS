"""
One-off script: ingests PDF papers from docs/papers/ into the
document_chunks table (chunk -> embed -> store).

Run from backend/ with: python -m scripts.ingest_papers
"""

import asyncio
from pathlib import Path

from app.database import async_session
from app.retrieval.ingestion import load_document
from app.retrieval.chunking import chunk_text
from app.retrieval.embeddings import embed_text
from app.retrieval.models import DocumentChunk

PAPERS_DIR = Path(__file__).resolve().parents[2] / "docs" / "papers"


async def ingest_file(file_path: Path):
    print(f"\n--- Ingesting {file_path.name} ---")
    text = load_document(str(file_path))
    print(f"  Loaded {len(text)} characters")

    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    print(f"  Split into {len(chunks)} chunks")

    async with async_session() as session:
        for i, chunk in enumerate(chunks):
            try:
                vector = embed_text(chunk.text)
            except Exception as e:
                print(f"  Skipping chunk {i} (embedding failed: {e})")
                continue

            row = DocumentChunk(
                source=file_path.name,
                chunk_index=chunk.index,
                text=chunk.text,
                embedding=vector,
            )
            session.add(row)

            if (i + 1) % 10 == 0:
                print(f"  Embedded {i + 1}/{len(chunks)} chunks")

        await session.commit()
    print(f"  Done: {file_path.name} fully ingested")


async def main():
    pdfs = sorted(PAPERS_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {PAPERS_DIR}")
        return

    print(f"Found {len(pdfs)} PDF(s) to ingest: {[p.name for p in pdfs]}")
    for pdf in pdfs:
        await ingest_file(pdf)

    print("\nAll done.")


if __name__ == "__main__":
    asyncio.run(main())