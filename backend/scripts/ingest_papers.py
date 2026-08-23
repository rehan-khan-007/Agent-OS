"""
One-off script: ingests PDF papers from docs/papers/ into the
document_chunks table (chunk -> batch-embed -> store).

Skips any file whose name already appears as a `source` in the
table, so it's safe to re-run repeatedly as new PDFs are added to
docs/papers/ (e.g. by fetch_arxiv_papers.py) without re-embedding
(and re-paying for) documents already ingested.

Uses embed_chunks() (batched — up to 100 texts per API call) rather
than one embed_text() call per chunk, which is what makes ingesting
a real multi-document corpus practical rather than a multi-hour job.

Two things learned the hard way while ingesting a real 129-document
corpus, both fixed here:
1. PostgreSQL text columns cannot store a literal NUL byte (\x00) —
   ever, by design. A malformed/corrupted PDF can make pypdf's text
   extraction emit one, which crashes the insert with
   CharacterNotInRepertoireError. Extracted text is sanitized (NUL
   bytes stripped) before chunking.
2. A single bad document previously crashed the ENTIRE run — one
   PDF failing partway through a 129-document batch meant the other
   ~100 were never attempted, even though every prior document's
   data was already safely committed. Each document is now wrapped
   in its own try/except so one failure is logged and skipped, not
   fatal to the whole batch — the same "partial success beats total
   failure" principle applied everywhere else in this project.

Run from backend/ with: python -m scripts.ingest_papers
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.database import async_session
from app.retrieval.ingestion import load_document
from app.retrieval.chunking import chunk_text
from app.retrieval.embeddings import embed_chunks
from app.retrieval.models import DocumentChunk

PAPERS_DIR = Path(__file__).resolve().parents[2] / "docs" / "papers"


async def get_already_ingested_sources() -> set[str]:
    async with async_session() as session:
        result = await session.execute(select(DocumentChunk.source).distinct())
        return {row[0] for row in result.all()}


async def ingest_file(file_path: Path) -> bool:
    """Returns True on success, False if this document failed and
    was skipped (never raises — caller doesn't need its own try/except)."""
    print(f"\n--- Ingesting {file_path.name} ---")
    try:
        text = load_document(str(file_path))
    except Exception as e:
        print(f"  FAILED to load/extract text: {e} — skipping this document")
        return False

    # PostgreSQL text columns reject a literal NUL byte outright — a
    # malformed PDF's text extraction can produce one, which would
    # otherwise crash the insert below.
    text = text.replace("\x00", "")
    print(f"  Loaded {len(text)} characters")

    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    print(f"  Split into {len(chunks)} chunks")

    vectors = embed_chunks([c.text for c in chunks])

    stored = 0
    skipped = 0
    try:
        async with async_session() as session:
            for chunk, vector in zip(chunks, vectors):
                if vector is None:
                    skipped += 1
                    continue
                session.add(DocumentChunk(
                    source=file_path.name,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    embedding=vector,
                ))
                stored += 1
            await session.commit()
    except Exception as e:
        print(f"  FAILED to store chunks in the database: {e} — skipping this document")
        return False

    print(f"  Done: {stored} chunks stored, {skipped} skipped (embedding failed)")
    return True


async def main():
    # Optional: python -m scripts.ingest_papers 3  ->  only process
    # the first 3 new documents this run, for a quick real test
    # before committing to the full corpus.
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    pdfs = sorted(PAPERS_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {PAPERS_DIR}")
        return

    already_ingested = await get_already_ingested_sources()
    to_ingest = [p for p in pdfs if p.name not in already_ingested]
    already_count = len(pdfs) - len(to_ingest)

    print(f"Found {len(pdfs)} PDF(s) total ({already_count} already ingested, {len(to_ingest)} new)")

    if not to_ingest:
        print("Nothing new to ingest.")
        return

    if limit is not None:
        to_ingest = to_ingest[:limit]
        print(f"Limiting this run to the first {len(to_ingest)} new document(s) (test mode)")

    succeeded = 0
    failed = []
    for pdf in to_ingest:
        if await ingest_file(pdf):
            succeeded += 1
        else:
            failed.append(pdf.name)

    print(f"\nAll done. {succeeded}/{len(to_ingest)} new document(s) ingested successfully.")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}")


if __name__ == "__main__":
    asyncio.run(main())
