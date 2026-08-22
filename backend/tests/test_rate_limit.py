"""
Tests for the rate limiter. Unit tests cover the core logic against
real Redis (fixed window, per-bucket, per-IP isolation, fail-open).
The full HTTP-level integration (429 actually returned by a real
FastAPI route) is deliberately not exercised here with mocked agent
calls, since that would require standing up the whole app + LLM
mocking; the limiter itself is what's being verified, and it's a
plain dependency any route can attach.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

from app.config import settings
from app.cache import redis_client
from app.ratelimit.limiter import enforce_rate_limit, get_client_ip

requires_redis = pytest.mark.skipif(
    not settings.redis_url or "localhost" in settings.redis_url,
    reason="No real Redis instance configured (REDIS_URL unset or points to localhost)",
)


@pytest.fixture(autouse=True)
def _reset_redis_client_singleton():
    redis_client._client = None
    yield
    redis_client._client = None


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, ip=None, forwarded=None):
        self.headers = {}
        if forwarded:
            self.headers["x-forwarded-for"] = forwarded
        self.client = _FakeClient(ip) if ip else None


def test_get_client_ip_prefers_x_forwarded_for():
    req = _FakeRequest(forwarded="1.2.3.4, 5.6.7.8")
    assert get_client_ip(req) == "1.2.3.4"


def test_get_client_ip_falls_back_to_request_client():
    req = _FakeRequest(ip="9.9.9.9")
    assert get_client_ip(req) == "9.9.9.9"


@pytest.mark.asyncio
async def test_fails_open_when_redis_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    redis_client._client = None

    req = _FakeRequest(forwarded="1.1.1.1")
    for _ in range(100):
        await enforce_rate_limit(req, "unconfigured_bucket", limit=5, window_seconds=60)
    # No exception after 100 requests against a limit of 5 — fails open correctly


@requires_redis
@pytest.mark.asyncio
async def test_allows_requests_under_limit():
    req = _FakeRequest(forwarded="10.1.1.1")
    for _ in range(5):
        await enforce_rate_limit(req, "test_under_limit", limit=5, window_seconds=60)


@requires_redis
@pytest.mark.asyncio
async def test_blocks_request_exceeding_limit():
    req = _FakeRequest(forwarded="10.1.1.2")
    for _ in range(5):
        await enforce_rate_limit(req, "test_exceed_limit", limit=5, window_seconds=60)

    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit(req, "test_exceed_limit", limit=5, window_seconds=60)
    assert exc_info.value.status_code == 429


@requires_redis
@pytest.mark.asyncio
async def test_different_ips_have_independent_limits():
    req_a = _FakeRequest(forwarded="10.1.1.3")
    req_b = _FakeRequest(forwarded="10.1.1.4")

    for _ in range(5):
        await enforce_rate_limit(req_a, "test_independent_ip", limit=5, window_seconds=60)

    # req_b should NOT be blocked even though req_a just hit its limit
    await enforce_rate_limit(req_b, "test_independent_ip", limit=5, window_seconds=60)


@requires_redis
@pytest.mark.asyncio
async def test_different_buckets_have_independent_limits_for_same_ip():
    req = _FakeRequest(forwarded="10.1.1.5")

    for _ in range(5):
        await enforce_rate_limit(req, "bucket_a", limit=5, window_seconds=60)

    # Same IP, different bucket — should not be affected by bucket_a's limit
    await enforce_rate_limit(req, "bucket_b", limit=5, window_seconds=60)
