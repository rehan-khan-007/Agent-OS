"""
LLM-as-judge grounding evaluation — a genuine, more rigorous
alternative to the vocabulary-overlap heuristic used in
llm_benchmark.py. This directly closes a named, honest gap from
BENCHMARKS.md: "Grounding evaluation... is a vocabulary-overlap
heuristic, not an LLM-as-judge score."

For each question: retrieve real context (hybrid_search), generate a
real answer (gpt-4o-mini, the cheapest model — proven in this
project's own 4-model benchmark to have the best cost/performance
ratio), then have a SEPARATE judge call assess whether the answer is
actually grounded in the retrieved context, with structured
reasoning, not just a heuristic word-overlap guess.

COST: uses the cheapest model for both generation and judging.
35 questions x 2 calls (generate + judge) = 70 completions + 35
embedding calls. Based on this project's own measured gpt-4o-mini
costs (~$0.006 for 35 generation calls), the judge calls should be
similar or smaller (more constrained output), so total cost is
expected to be well under $0.05 — genuinely cheap, but still real,
billed API usage. Live running cost is printed after every call.

Usage: python3 evals/runners/grounding_judge_eval.py
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

MODEL = "openai/gpt-4o-mini"
# (input $ per 1M tokens, output $ per 1M tokens) — same pricing used
# in llm_benchmark.py, verified against OpenRouter's listings.
PRICE = (0.15, 0.60)

ANSWER_SYSTEM_PROMPT = (
    "Answer the user's question using only the provided context. "
    "If the context doesn't contain the answer, say so plainly."
)

JUDGE_SYSTEM_PROMPT = (
    "You are evaluating whether an AI-generated answer is genuinely "
    "grounded in the provided source context, or whether it makes "
    "claims not actually supported by that context. Respond with "
    "ONLY a JSON object, no other text: "
    '{"grounded": true or false, "reasoning": "one brief sentence"}'
)


def _cost(usage: dict) -> float:
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    input_price, output_price = PRICE
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


async def run_eval():
    dataset_path = Path(__file__).resolve().parents[1] / "datasets" / "retrieval_qa.json"
    with open(dataset_path) as f:
        dataset = json.load(f)

    positive_items = [d for d in dataset if d["expected_sources"]]

    print(f"Running LLM-as-judge grounding eval: {len(positive_items)} questions, model={MODEL}")
    print("This makes real, billed API calls. Ctrl+C to stop at any point.\n")

    results = []
    running_cost = 0.0

    async with async_session() as db:
        for i, item in enumerate(positive_items):
            question = item["question"]
            print(f"[{i+1}/{len(positive_items)}] {question}")

            context_chunks = await hybrid_search(question, db, top_k=3)
            context_text = "\n\n".join(
                f"[{c.source}, chunk {c.chunk_index}]: {c.text}" for c in context_chunks
            )

            answer_messages = [
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {question}"},
            ]
            answer_response = await chat_completion(answer_messages, model=MODEL)
            answer = extract_choice(answer_response)["content"] or ""
            running_cost += _cost(answer_response.get("usage", {}))

            judge_messages = [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Source context:\n{context_text}\n\n"
                    f"Question: {question}\n\n"
                    f"Generated answer: {answer}\n\n"
                    "Is this answer genuinely grounded in the source context?"
                )},
            ]
            judge_response = await chat_completion(judge_messages, model=MODEL)
            judge_raw = extract_choice(judge_response)["content"] or "{}"
            running_cost += _cost(judge_response.get("usage", {}))

            try:
                # Strip potential markdown code fences the model might add
                # despite instructions, before parsing as JSON.
                cleaned = judge_raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                verdict = json.loads(cleaned)
                grounded = bool(verdict.get("grounded"))
                reasoning = verdict.get("reasoning", "")
            except (json.JSONDecodeError, AttributeError):
                grounded = None
                reasoning = f"(judge response was not valid JSON: {judge_raw[:100]})"

            results.append({
                "question": question,
                "grounded": grounded,
                "reasoning": reasoning,
            })

            status = "✓ grounded" if grounded else ("✗ NOT grounded" if grounded is False else "? unparseable")
            print(f"    {status} — {reasoning} (running total: ${running_cost:.4f})")

    print()
    print("=" * 70)
    valid_results = [r for r in results if r["grounded"] is not None]
    grounded_count = sum(1 for r in valid_results if r["grounded"])
    unparseable = len(results) - len(valid_results)

    print(f"Judged grounded: {grounded_count}/{len(valid_results)} "
          f"({grounded_count / len(valid_results) * 100:.1f}%)" if valid_results else "No valid judge results")
    if unparseable:
        print(f"Unparseable judge responses: {unparseable}")

    not_grounded = [r for r in results if r["grounded"] is False]
    if not_grounded:
        print("\nQuestions the judge flagged as NOT grounded:")
        for r in not_grounded:
            print(f"  - {r['question']}")
            print(f"    reason: {r['reasoning']}")

    print(f"\nTotal actual cost this run: ${running_cost:.4f}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_eval())
