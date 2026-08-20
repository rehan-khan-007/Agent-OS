"""
Model routing policies: defines which models are available at each
tier, so routing decisions reference named tiers rather than hardcoded
model strings scattered across the codebase.
"""

MODEL_TIERS = {
    "fast": "openai/gpt-4o-mini",      # cheap, low-latency — simple lookups, short queries
    "strong": "openai/gpt-4o",          # more capable — complex reasoning, multi-tool tasks
}

DEFAULT_TIER = "fast"