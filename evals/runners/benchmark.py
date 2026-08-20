"""
Retrieval evaluation: measures how often the retrieval pipeline
returns the expected chunk for a given question, within the top-k results.
"""

import asyncio
import json
import sys
from pathlib import Path

# Make 'app' importable — this script lives outside backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.database import async_session
from app.retrieval.pipeline import retrieve_relevant_chunks


async def run_benchmark():
    dataset_path = Path(__file__).resolve().parents[1] / "datasets" / "retrieval_qa.json"
    with open(dataset_path) as f:
        dataset = json.load(f)

    hits = 0
    results = []

    async with async_session() as db:
        for item in dataset:
            retrieved = await retrieve_relevant_chunks(item["question"], db, top_k=3)
            retrieved_indices = [(r.source, r.chunk_index) for r in retrieved]
            expected = (item["expected_source"], item["expected_chunk_index"])
            hit = expected in retrieved_indices

            hits += hit
            results.append({
                "question": item["question"],
                "expected": expected,
                "retrieved": retrieved_indices,
                "hit": hit,
            })

    accuracy = hits / len(dataset) * 100
    print(f"\nRetrieval accuracy: {hits}/{len(dataset)} ({accuracy:.1f}%)\n")
    for r in results:
        status = "✓" if r["hit"] else "✗"
        print(f"{status} {r['question']}")
        print(f"   expected: {r['expected']}, got: {r['retrieved']}")

    return accuracy


if __name__ == "__main__":
    asyncio.run(run_benchmark())
    