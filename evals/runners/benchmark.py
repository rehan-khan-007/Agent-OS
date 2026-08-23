"""
Retrieval evaluation: measures how often the retrieval pipeline surfaces
a chunk from the correct source document(s) for a given question, within
the top-k results (source-level recall@k).

Why source-level rather than exact chunk-index matching: chunk indices
shift any time a document is re-ingested or re-chunked, making an
index-based ground truth fragile and quick to silently go stale. Source-
level recall answers the question that actually matters for a RAG system
— "did it find the right document?" — and stays valid across re-ingestion.

Also reports on "no relevant source" questions (expected_sources: []) to
surface a known limitation: pgvector nearest-neighbor search always
returns top_k results regardless of relevance, so the pipeline has no
built-in way to say "nothing in the corpus answers this."
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.database import async_session
from app.retrieval.hybrid import hybrid_search


async def run_benchmark(top_k: int = 3):
    dataset_path = Path(__file__).resolve().parents[1] / "datasets" / "retrieval_qa.json"
    with open(dataset_path) as f:
        dataset = json.load(f)

    positive_items = [d for d in dataset if d["expected_sources"]]
    negative_items = [d for d in dataset if not d["expected_sources"]]

    hits = 0
    results = []

    async with async_session() as db:
        for item in dataset:
            retrieved = await hybrid_search(item["question"], db, top_k=top_k)
            retrieved_sources = {r.source for r in retrieved}
            expected = set(item["expected_sources"])

            if expected:
                hit = bool(retrieved_sources & expected)
                hits += hit
            else:
                # No source in the corpus should answer this — there's no
                # "correct" retrieval outcome, since pgvector always
                # returns something. Logged, not scored.
                hit = None

            results.append({
                "question": item["question"],
                "expected_sources": sorted(expected),
                "retrieved_sources": sorted(retrieved_sources),
                "hit": hit,
            })

    accuracy = (hits / len(positive_items) * 100) if positive_items else 0.0

    print(f"\n=== Retrieval Evaluation (top_k={top_k}) ===")
    print(f"Source-level recall@{top_k}: {hits}/{len(positive_items)} ({accuracy:.1f}%)\n")

    for r in results:
        if r["hit"] is None:
            status = "—"
        elif r["hit"]:
            status = "✓"
        else:
            status = "✗"
        print(f"{status} {r['question']}")
        print(f"   expected: {r['expected_sources'] or '(none — negative control)'}")
        print(f"   retrieved: {r['retrieved_sources']}")

    if negative_items:
        print(f"\nNote: {len(negative_items)} question(s) had no relevant source in the corpus.")
        print("These are excluded from the recall score above (marked '—') since pgvector")
        print("nearest-neighbor search always returns top_k results regardless of true")
        print("relevance — there is no built-in 'no answer found' signal in this pipeline.")

    return accuracy


if __name__ == "__main__":
    asyncio.run(run_benchmark())
