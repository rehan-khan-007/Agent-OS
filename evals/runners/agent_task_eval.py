"""
Real agent-task evaluation: measures tool-selection accuracy across
the actual production agent graph (app/agents/graph.py's `agent`),
invoked the exact same way the real /agents/chat endpoint does
(agent.ainvoke({"messages": [...], "next": ""})) — not a simplified
stand-in.

This is a genuinely different question from the retrieval evals
elsewhere in this project: those measure "did the retrieval pipeline
find the right chunk", this measures "given a user message, does the
agent correctly decide WHICH of its 3 real tools to call (or
correctly decide to call none at all)". A model that always calls
`retrieve` regardless of the question, or never calls `calculator`
for math, would score well on retrieval recall but fail here.

30 tasks across 4 categories: 10 needing `retrieve` (real questions
about the ingested corpus), 10 needing `calculator` (real arithmetic
the model shouldn't just guess at), 5 needing `web_search` (current/
external info not in the corpus), and 5 needing no tool at all
(general knowledge/conversational — tests the agent doesn't
over-trigger tools unnecessarily, a real, distinct failure mode).

Honest architectural note: the current agent graph supports at most
ONE tool call per turn (model -> tools -> respond, no loop back to
model for a second tool call) — this eval is scoped to what the
graph actually supports, not a claim about multi-step tool chaining.

COST: each task is 1-2 real LLM calls (tool selection, then a second
call to generate the final answer if a tool was used) via whatever
model app/llm/client.py is configured to use. 30 tasks is a small,
real, but genuinely cheap cost — expect well under $0.10 total.

Usage: python3 evals/runners/agent_task_eval.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.agents.graph import agent


def _tool_called(messages: list[dict]) -> str | None:
    """Returns the name of the tool the agent called, or None if it
    never called one — found by scanning for an assistant message
    with tool_calls, matching how the real graph actually records this."""
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            return msg["tool_calls"][0]["function"]["name"]
    return None


async def run_eval():
    dataset_path = Path(__file__).resolve().parents[1] / "datasets" / "agent_task_eval.json"
    with open(dataset_path) as f:
        tasks = json.load(f)

    print(f"Running agent-task evaluation: {len(tasks)} real tasks through the actual agent graph")
    print("This makes real, billed API calls. Ctrl+C to stop at any point.\n")

    results = []

    for i, task in enumerate(tasks):
        message = task["message"]
        expected = task["expected_tool"]

        start = time.time()
        try:
            state = {"messages": [{"role": "user", "content": message}], "next": ""}
            result = await agent.ainvoke(state)
            duration = time.time() - start

            actual_tool = _tool_called(result["messages"])
            actual = actual_tool if actual_tool else "none"

            correct = actual == expected
            status = "correct" if correct else "WRONG"
            print(f"[{i+1}/{len(tasks)}] ({duration:.2f}s) {status}: expected={expected}, got={actual} — \"{message[:60]}\"")

            results.append({
                "message": message,
                "expected": expected,
                "actual": actual,
                "correct": correct,
                "duration": duration,
            })
        except Exception as e:
            duration = time.time() - start
            print(f"[{i+1}/{len(tasks)}] ({duration:.2f}s) ERROR: {e} — \"{message[:60]}\"")
            results.append({
                "message": message,
                "expected": expected,
                "actual": "ERROR",
                "correct": False,
                "duration": duration,
                "error": str(e),
            })

    print(f"\n{'=' * 70}")
    correct_count = sum(1 for r in results if r["correct"])
    total = len(results)
    print(f"Overall tool-selection accuracy: {correct_count}/{total} ({correct_count/total*100:.1f}%)")

    print("\nBy category:")
    categories = sorted(set(t["expected_tool"] for t in tasks))
    for cat in categories:
        cat_results = [r for r in results if r["expected"] == cat]
        cat_correct = sum(1 for r in cat_results if r["correct"])
        print(f"  {cat:12s}  {cat_correct}/{len(cat_results)}  ({cat_correct/len(cat_results)*100:.1f}%)")

    wrong = [r for r in results if not r["correct"]]
    if wrong:
        print(f"\nIncorrect ({len(wrong)}):")
        for r in wrong:
            print(f"  - expected={r['expected']}, got={r['actual']}: \"{r['message']}\"")

    durations = [r["duration"] for r in results]
    print(f"\nLatency — min: {min(durations):.2f}s, max: {max(durations):.2f}s, avg: {sum(durations)/len(durations):.2f}s")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(run_eval())
