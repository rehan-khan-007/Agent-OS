"""
Redis-backed rate limiting: fixed-window counter per client IP.

Design choice — fails OPEN, not loud, unlike the job queue
(app/queue/redis_queue.py). If Redis is briefly unavailable, letting
every request through (no rate limiting for that window) is a better
tradeoff than making the whole app unusable because its *protective*
layer had a hiccup — especially on a project where Redis itself has
already shown occasional flakiness. The downside (no cost protection
during a rare outage window) is acceptable; making the app
unreachable over it is not.

Implementation: a Redis key per (client, window) is incremented on
each request via INCR, with an expiry set only on the first increment
so the counter resets every `window_seconds`. This is a standard
fixed-window limiter — simple and sufficient here, though it allows
some burstiness right at window boundaries compared to a sliding-
window approach, which isn't worth the added complexity for this use
case.
"""

import time

from fastapi import HTTPException, Request

from app.cache.redis_client import get_client
from app.observability.logging import get_logger

logger = get_logger(__name__)


def get_client_ip(request: Request) -> str:
    """
    Returns the real client IP, accounting for Render's reverse
    proxy. Render (like most PaaS platforms) terminates TLS and
    proxies requests, so request.client.host would return the
    proxy's internal IP, not the actual visitor — the real IP is in
    the X-Forwarded-For header instead, as its first (leftmost) entry.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: int) -> None:
    """
    Raises HTTPException(429) if the client has exceeded `limit`
    requests to this bucket within the current `window_seconds`
    window. Fails open (allows the request, logs the issue) if Redis
    is unavailable — see module docstring for why.
    """
    client = get_client()
    if client is None:
        return  # Redis not configured/reachable — fail open

    ip = get_client_ip(request)
    window = int(time.time()) // window_seconds
    key = f"ratelimit:{bucket}:{ip}:{window}"

    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
    except Exception as e:
        logger.error(
            "Rate limiter failed to reach Redis, failing open",
            extra={"extra_fields": {"bucket": bucket, "error": str(e)}},
        )
        return  # fail open on any Redis error

    if count > limit:
        logger.info(
            "Rate limit exceeded",
            extra={"extra_fields": {"bucket": bucket, "ip": ip, "count": count, "limit": limit}},
        )
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {limit} requests per {window_seconds} seconds for this action.",
        )


def rate_limit(bucket: str, limit: int, window_seconds: int):
    """
    Returns a FastAPI dependency enforcing the given rate limit,
    scoped to `bucket` (so /agents/chat and /documents/upload track
    separate limits) and keyed by client IP.

    Usage: @router.post("/x", dependencies=[Depends(rate_limit("x", limit=20, window_seconds=300))])
    """
    async def _dependency(request: Request) -> None:
        await enforce_rate_limit(request, bucket, limit, window_seconds)
    return _dependency
