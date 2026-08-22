"""
Tests for the Redis cache module. Requires a real Redis instance
(REDIS_URL env var pointing at something other than localhost) —
these tests are skipped automatically if Redis isn't configured,
rather than failing, since CI environments may not have Redis set up.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.config import settings
from app.cache import redis_client

requires_redis = pytest.mark.skipif(
    not settings.redis_url or "localhost" in settings.redis_url,
    reason="No real Redis instance configured (REDIS_URL unset or points to localhost)",
)


def test_get_client_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(redis_client, "_client", None)
    assert redis_client.get_client() is None


def test_get_client_returns_none_for_localhost_placeholder(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_client, "_client", None)
    assert redis_client.get_client() is None


@pytest.mark.asyncio
async def test_cache_functions_fail_open_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(redis_client, "_client", None)

    result = await redis_client.get_cached_response("session", "message")
    assert result is None

    # Should not raise, even though there's no real Redis to write to
    await redis_client.cache_response("session", "message", {"messages": []})


@requires_redis
@pytest.mark.asyncio
async def test_idempotency_cache_roundtrip():
    await redis_client.cache_response("test-session-pytest", "test-message", {"messages": [{"role": "assistant", "content": "cached answer"}]})
    result = await redis_client.get_cached_response("test-session-pytest", "test-message")
    assert result is not None
    assert result["messages"][0]["content"] == "cached answer"


@requires_redis
@pytest.mark.asyncio
async def test_idempotency_cache_is_session_isolated():
    await redis_client.cache_response("session-a-pytest", "shared-message", {"messages": [{"content": "a"}]})
    result = await redis_client.get_cached_response("session-b-pytest", "shared-message")
    assert result is None
