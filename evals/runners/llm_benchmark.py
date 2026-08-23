"""
4-model LLM benchmark: compares success rate, grounding (heuristic),
latency, and real dollar cost across 4 real models, answering the
same set of real questions with the same retrieved context.

Uses the project's existing 14-question eval dataset and the real
hybrid_search retrieval pipeline — retrieval runs ONCE per question
(shared across all 4 models), so this isolates generation quality as
the variable being compared, not differences in what got retrieved.

Real cost, not an estimate: computed from each response's actual
`usage` field (prompt_tokens, completion_tokens), multiplied by each
model's real per-token price. Prices below are current as of the
research done for this benchmark (Aug 2026) — verified against
OpenRouter's own listings, not assumed. Since prices can change,
these are hardcoded with their source date for transparency.

Grounding is a labeled heuristic, not a true grounding judge: it
checks for meaningful vocabulary overlap between the answer and the
retrieved context, as a proxy for "did the model actually use what
was retrieved" without the cost of an additional LLM-as-judge call.
It will have false positives/negatives — treat it as directional,
not authoritative.

COST WARNING: this script makes real, billed API calls. Running the
default dataset (14 questions x 4 models = 56 completions, plus 14
embedding calls) costs roughly $0.09 USD total based on the pricing
below. Live running cost is printed after every call so you can
interrupt (Ctrl+C) if anything looks unexpected.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.database import async_session
from app.retrieval.hybrid import hybrid_search
from app.llm.client import chat_completion, extract_choice

# (input $ per 1M tokens, output $ per 1M tokens) — verified against
# OpenRouter's own listings, Aug 2026.
MODELS = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "anthropic/claude-haiku-4.5": (1.00, 5.00),
    "google/gemini-3.7-flash": (0.1875, 0.9375),
}

SYSTEM_PROMPT = (
    "Answer the user's question using only the provided context. "
    "If the context doesn't contain the answer, say so plainly."
)


def _build_prompt(question: str, context_chunks: list) -> list[dict]:
    context_text = "\n\n".join(
        f"[{c.source}, chunk {c.chunk_index}]: {c.text}" for c in context_chunks
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {question}"},
    ]


def _grounding_heuristic(answer: str, context_chunks: list) -> bool:
    """
    Labeled heuristic (see module docstring) — checks whether a
    meaningful fraction of the context's distinctive vocabulary shows
    up in the answer, as a cheap proxy for "did the model actually
    draw on what was retrieved" rather than answer generically.
    """
    if not answer or not context_chunks:
        return False
    context_words = set()
    for c in context_chunks:
        context_words |= {w.lower() for w in c.text.split() if len(w) > 5}
    answer_words = {w.lower().strip(".,()") for w in answer.split()}
    overlap = context_words & answer_words
    return len(overlap) >= 3


async def run_benchmark():
    dataset_path = Path(__file__).resolve().parents[1] / "datasets" / "retrieval_qa.json"
    with open(dataset_path) as f:
        dataset = json.load(f)

    positive_items = [d for d in dataset if d["expected_sources"]]

    total_calls = len(positive_items) * len(MODELS)
    print(f"Running {len(positive_items)} questions x {len(MODELS)} models = {total_calls} completions")
    print(f"Models: {', '.join(MODELS.keys())}")
    print("This makes real, billed API calls. Ctrl+C to stop at any point.\n")

    results = {model: [] for model in MODELS}
    running_cost = 0.0

    async with async_session() as db:
        for i, item in enumerate(positive_items):
            question = item["question"]
            print(f"[{i+1}/{len(positive_items)}] {question}")

            context_chunks = await hybrid_search(question, db, top_k=3)
            messages = _build_prompt(question, context_chunks)

            for model in MODELS:
                start = time.time()
                try:
                    response = await chat_completion(messages, model=model)
                    latency = time.time() - start
                    answer = extract_choice(response)["content"] or ""
                    usage = response.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

                    input_price, output_price = MODELS[model]
                    cost = (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000
                    running_cost += cost

                    grounded = _grounding_heuristic(answer, context_chunks)

                    results[model].append({
                        "success": True,
                        "latency": latency,
                        "cost": cost,
                        "grounded": grounded,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    })
                    print(f"    {model}: {latency:.2f}s, ${cost:.5f}, grounded={grounded} "
                          f"(running total: ${running_cost:.4f})")
                except Exception as e:
                    latency = time.time() - start
                    results[model].append({"success": False, "latency": latency, "cost": 0.0, "grounded": False})
                    print(f"    {model}: FAILED after {latency:.2f}s — {e}")

    print()
    print("=" * 70)
    print(f"{'Model':<32} {'Success':<10} {'Grounded':<10} {'Avg Latency':<12} {'Total Cost'}")
    print("-" * 70)
    for model, runs in results.items():
        n = len(runs)
        success_rate = sum(r["success"] for r in runs) / n * 100
        grounded_rate = sum(r["grounded"] for r in runs) / n * 100
        avg_latency = sum(r["latency"] for r in runs) / n
        total_cost = sum(r["cost"] for r in runs)
        print(f"{model:<32} {success_rate:>6.1f}%   {grounded_rate:>6.1f}%    {avg_latency:>7.2f}s     ${total_cost:.5f}")
    print("=" * 70)
    print(f"Total actual cost this run: ${running_cost:.4f}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
