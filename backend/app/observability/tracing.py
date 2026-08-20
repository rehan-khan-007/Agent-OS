"""
Observability: wraps LLM calls and tool calls with Langfuse tracing,
so we can see full execution traces (prompts, tool calls, latency,
token usage) for every agent run.
"""

from langfuse import Langfuse

from app.config import settings

langfuse = Langfuse(
    public_key=settings.langfuse_public_key,
    secret_key=settings.langfuse_secret_key,
    host="https://cloud.langfuse.com",
)


def is_enabled() -> bool:
    """Tracing only runs if keys are actually configured."""
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def flush():
    """Force-send any buffered trace events. Call this before a short-lived
    script exits, since Langfuse batches events in the background."""
    langfuse.flush()