"""
Model router: picks which model tier to use for a given request,
based on simple complexity heuristics. This is intentionally basic
to start — a real production router would use classifier models,
historical performance data, or cost/latency budgets.
"""

from app.routing.policies import MODEL_TIERS, DEFAULT_TIER


def route_model(messages: list[dict], tools: list[dict] | None = None) -> str:
    """
    Decides which model to use based on request characteristics.

    Current heuristic (simple, explainable):
    - If tools are available AND there's meaningful conversation history
      (more than 2 messages), use the strong model — likely a complex,
      multi-step task.
    - Otherwise, use the fast model.
    """
    tier = DEFAULT_TIER

    if tools and len(messages) > 2:
        tier = "strong"

    return MODEL_TIERS[tier]