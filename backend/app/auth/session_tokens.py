"""
Session-ownership tokens — closes a real gap: session_id alone (a
UUID4) is not realistically brute-forceable, but there was previously
no way to verify the caller USING a session_id is the same one who
CREATED it. If a session_id ever leaked (server logs, browser
history, a shared screenshot), anyone holding that string could read
or continue that conversation with zero further verification.

This is NOT full user authentication — no accounts, no passwords, no
login UI. It's a lightweight ownership proof: when a new session
starts, the server issues a separate secret token alongside the
session_id; continuing that session requires presenting the matching
token. Losing the session_id alone (without the token) no longer
grants access.

Deliberately fails CLOSED, not open — unlike most Redis usage in this
project (cache, rate limiting), which fails open for availability.
If Redis is unreachable, we genuinely cannot verify ownership, so the
safe failure mode here is to deny the request, not silently allow it
through. Reuses app.queue.redis_queue.get_client(), which already
raises if Redis isn't configured/reachable — the same fail-closed
behavior needed here, despite living in the "queue" module.
"""

import secrets

from app.queue.redis_queue import get_client, QueueUnavailableError

SESSION_TOKEN_KEY_PREFIX = "session_token:"
SESSION_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7  # one week — generous for an ongoing conversation


class SessionAuthError(Exception):
    """Raised when a session_id is presented without a valid matching
    token — either it was never issued (a made-up or expired
    session_id), or the token presented doesn't match."""
    pass


async def issue_token(session_id: str) -> str:
    """Generates and stores a fresh ownership token for a brand-new
    session_id. Called exactly once, when a session is first created."""
    client = get_client()
    token = secrets.token_urlsafe(32)
    await client.set(f"{SESSION_TOKEN_KEY_PREFIX}{session_id}", token, ex=SESSION_TOKEN_TTL_SECONDS)
    return token


async def verify_token(session_id: str, provided_token: str | None) -> None:
    """Raises SessionAuthError if provided_token doesn't match what
    was issued for this session_id (including if none was ever
    issued — a fabricated or expired session_id)."""
    try:
        client = get_client()
    except QueueUnavailableError as e:
        # Fail closed: if we can't verify, we can't allow access.
        raise SessionAuthError(f"Cannot verify session ownership right now: {e}") from e

    stored_token = await client.get(f"{SESSION_TOKEN_KEY_PREFIX}{session_id}")
    if stored_token is None or provided_token != stored_token:
        raise SessionAuthError("Invalid or missing session token for this session_id.")
