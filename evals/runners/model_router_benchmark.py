"""
Model router benchmark: measures real dollar cost of the actual
production routing logic (app/routing/router.py's route_model())
versus an always-strong baseline (forcing every request to gpt-4o
regardless), across a realistic set of request patterns.

This directly tests the router's real decision boundary — per the
actual code, it only escalates to the strong model when BOTH tools
are available AND the conversation has more than 2 messages. 12
realistic scenarios (3 each) span all 4 combinations of that
boundary: short/no-tools, short/with-tools, long/with-tools,
long/no-tools — so this measures the router's actual behavior at its
real decision edges, not synthetic cases.

Each scenario is run TWICE: once through chat_completion() with no
model override (letting the real router decide), once forced to
gpt-4o via an explicit model= override — using the exact same
verified per-token pricing already established in llm_benchmark.py
($0.15/$0.60 per 1M tokens for gpt-4o-mini, $2.50/$10.00 for gpt-4o).

COST: 24 real completions total (12 scenarios x 2 paths). Small,
real cost — expect roughly $0.05-0.15 depending on gpt-4o-mini vs
gpt-4o's actual share of the routed path.

Usage: python3 evals/runners/model_router_benchmark.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.llm.client import chat_completion
from app.agents.graph import _get_openai_tools

PRICING = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
}


def _cost(model: str, usage: dict) -> float:
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    input_price, output_price = PRICING.get(model, (0, 0))
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


SCENARIOS = [
    # (label, messages, use_tools)
    ("short, no tools", [{"role": "user", "content": "What's the capital of Japan?"}], False),
    ("short, no tools", [{"role": "user", "content": "Explain recursion in one sentence."}], False),
    ("short, no tools", [{"role": "user", "content": "Hi there!"}], False),

    ("short, with tools", [{"role": "user", "content": "What is 234 times 88?"}], True),
    ("short, with tools", [{"role": "user", "content": "What is GRAPE?"}], True),
    ("short, with tools", [{"role": "user", "content": "What's the weather in Delhi right now?"}], True),

    ("long, with tools", [
        {"role": "user", "content": "I'm planning my investments."},
        {"role": "assistant", "content": "Sure, happy to help — what would you like to know?"},
        {"role": "user", "content": "What are corporate bonds and how do they work?"},
    ], True),
    ("long, with tools", [
        {"role": "user", "content": "Can you help me with some quantum control questions?"},
        {"role": "assistant", "content": "Of course! What would you like to know?"},
        {"role": "user", "content": "What is the Newton-Raphson GRAPE method?"},
    ], True),
    ("long, with tools", [
        {"role": "user", "content": "I need to do some quick math for a project."},
        {"role": "assistant", "content": "Sure, go ahead."},
        {"role": "user", "content": "What is 15% of 47000?"},
    ], True),

    ("long, no tools", [
        {"role": "user", "content": "Let's talk about machine learning."},
        {"role": "assistant", "content": "Sounds good — what aspect interests you?"},
        {"role": "user", "content": "Explain the difference between supervised and unsupervised learning."},
    ], False),
    ("long, no tools", [
        {"role": "user", "content": "Tell me a fun fact about space."},
        {"role": "assistant", "content": "Sure! Did you know a day on Venus is longer than its year?"},
        {"role": "user", "content": "That's wild, tell me another one."},
    ], False),
    ("long, no tools", [
        {"role": "user", "content": "I'm curious about history."},
        {"role": "assistant", "content": "Great, what era or topic?"},
        {"role": "user", "content": "Tell me about the fall of the Roman Empire in a few sentences."},
    ], False),
]


async def run_benchmark():
    print(f"Running model router benchmark: {len(SCENARIOS)} scenarios x 2 paths (routed, always-strong)")
    print("This makes real, billed API calls. Ctrl+C to stop at any point.\n")

    routed_cost = 0.0
    strong_cost = 0.0
    routed_models_used = []

    for i, (label, messages, use_tools) in enumerate(SCENARIOS):
        tools = _get_openai_tools() if use_tools else None

        # Routed path — no model override, real router decides.
        start = time.time()
        routed_response = await chat_completion(messages, tools=tools, model=None)
        routed_latency = time.time() - start
        routed_model = routed_response.get("model", "unknown")
        c1 = _cost(routed_model, routed_response.get("usage", {}))
        routed_cost += c1
        routed_models_used.append(routed_model)

        # Always-strong path — forced to gpt-4o regardless.
        start = time.time()
        strong_response = await chat_completion(messages, tools=tools, model="openai/gpt-4o")
        strong_latency = time.time() - start
        c2 = _cost("openai/gpt-4o", strong_response.get("usage", {}))
        strong_cost += c2

        print(f"[{i+1}/{len(SCENARIOS)}] {label:20s}  routed->{routed_model:22s} ${c1:.5f} ({routed_latency:.2f}s)  "
              f"| always-strong ${c2:.5f} ({strong_latency:.2f}s)")

    print(f"\n{'=' * 70}")
    print(f"Total cost — routed:        ${routed_cost:.5f}")
    print(f"Total cost — always-strong: ${strong_cost:.5f}")
    if strong_cost > 0:
        savings_pct = (1 - routed_cost / strong_cost) * 100
        print(f"Real savings from routing:  {savings_pct:.1f}%")

    from collections import Counter
    print(f"\nModels the real router actually chose: {dict(Counter(routed_models_used))}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
