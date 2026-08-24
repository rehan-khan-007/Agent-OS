"""
Retrieval ablation: measures source-level recall@3 for dense-only,
BM25-only, and hybrid (RRF-fused) retrieval independently, against
the same 35-question dataset used by benchmark.py — producing a real
comparative number ("hybrid beats either method alone by X points")
instead of a single, unqualified hybrid score.

Reuses the actual production retrieval functions (_vector_search_ranked,
bm25_search, hybrid_search from app/retrieval/hybrid.py) rather than
reimplementing retrieval logic here — this measures the real pipeline
components, not a simplified stand-in.

Usage: python3 evals/runners/retrieval_ablation.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.database import async_session
from app.retrieval.hybrid import _vector_search_ranked, hybrid_search
from app.retrieval.bm25_search import bm25_search

TOP_K = 3


async def _dense_only(query: str, session, top_k: int) -> list:
    return await _vector_search_ranked(query, session, top_k)


async def _bm25_only(query: str, session, top_k: int) -> list:
    results_with_scores = await bm25_search(query, session, top_k)
    return [chunk for chunk, _score in results_with_scores]


async def _hybrid(query: str, session, top_k: int) -> list:
    return await hybrid_search(query, session, top_k=top_k)


METHODS = {
    "BM25-only": _bm25_only,
    "Dense-only": _dense_only,
    "Hybrid (RRF)": _hybrid,
}


async def run_ablation():
    dataset_path = Path(__file__).resolve().parents[1] / "datasets" / "retrieval_qa.json"
    with open(dataset_path) as f:
        dataset = json.load(f)

    positive_items = [d for d in dataset if d["expected_sources"]]

    print(f"Running retrieval ablation: {len(positive_items)} questions x {len(METHODS)} methods\n")

    results: dict[str, int] = {name: 0 for name in METHODS}
    misses: dict[str, list[str]] = {name: [] for name in METHODS}

    async with async_session() as db:
        for item in positive_items:
            expected = set(item["expected_sources"])
            for name, method_fn in METHODS.items():
                retrieved = await method_fn(item["question"], db, TOP_K)
                retrieved_sources = {r.source for r in retrieved}
                if retrieved_sources & expected:
                    results[name] += 1
                else:
                    misses[name].append(item["question"])

    total = len(positive_items)
    print(f"{'=' * 70}")
    print(f"Retrieval ablation results (recall@{TOP_K}, {total} questions)")
    print(f"{'=' * 70}")
    for name in METHODS:
        hits = results[name]
        pct = hits / total * 100
        print(f"{name:15s}  {hits}/{total}  ({pct:.1f}%)")
    print(f"{'=' * 70}\n")

    for name in METHODS:
        if misses[name]:
            print(f"{name} missed {len(misses[name])} question(s):")
            for q in misses[name]:
                print(f"  - {q}")
            print()


if __name__ == "__main__":
    asyncio.run(run_ablation())
