"""
Redis-backed caching and idempotency layer.

Two responsibilities:
1. Idempotency: if the same (session_id, message) pair is submitted twice
   in quick succession (double-click, client retry, etc.), return the
   cached result instead of re-running the agent and paying for a second
   LLM call.
2. Tool-result caching: short-lived cache for tool outputs (e.g. web
   search) so identical queries within a short window don't hit the
   external API again.

Fails open: if Redis is unreachable, every function degrades to a no-op
(cache miss / pass-through) rather than crashing the request. Caching is
an optimization, not a dependency the app should go down over.
"""

import hashlib
import json
from typing import Any

import redis.asyncio as redis

from app.config import settings

_client: redis.Redis | None = None

IDEMPOTENCY_TTL_SECONDS = 60
TOOL_CACHE_TTL_SECONDS = 300


def get_client() -> redis.Redis | None:
    """Returns a shared Redis client, or None if Redis isn't configured/reachable."""
    global _client
    if not settings.redis_url or "localhost" in settings.redis_url:
        return None
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def _idempotency_key(session_id: str, message: str) -> str:
    digest = hashlib.sha256(f"{session_id}:{message}".encode()).hexdigest()
    return f"idem:{digest}"


async def get_cached_response(session_id: str, message: str) -> dict | None:
    """Returns a previously-cached response for this exact (session, message)
    pair if one was stored within the idempotency window, else None."""
    client = get_client()
    if client is None:
        return None
    try:
        raw = await client.get(_idempotency_key(session_id, message))
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def cache_response(session_id: str, message: str, response: dict) -> None:
    """Stores a response so a duplicate request within the TTL window
    returns instantly instead of re-running the agent."""
    client = get_client()
    if client is None:
        return
    try:
        await client.set(
            _idempotency_key(session_id, message),
            json.dumps(response),
            ex=IDEMPOTENCY_TTL_SECONDS,
        )
    except Exception:
        pass  # caching failures should never break the actual request


def _tool_cache_key(tool_name: str, args: dict) -> str:
    digest = hashlib.sha256(f"{tool_name}:{json.dumps(args, sort_keys=True)}".encode()).hexdigest()
    return f"tool:{digest}"


async def get_cached_tool_result(tool_name: str, args: dict) -> Any | None:
    """Returns a cached tool result for identical (tool, args) within the
    cache window, else None."""
    client = get_client()
    if client is None:
        return None
    try:
        raw = await client.get(_tool_cache_key(tool_name, args))
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def cache_tool_result(tool_name: str, args: dict, result: Any) -> None:
    """Caches a tool result for TOOL_CACHE_TTL_SECONDS."""
    client = get_client()
    if client is None:
        return
    try:
        await client.set(
            _tool_cache_key(tool_name, args),
            json.dumps(result),
            ex=TOOL_CACHE_TTL_SECONDS,
        )
    except Exception:
        pass
