"""
Tests for session-ownership tokens (app/auth/session_tokens.py) — the
mechanism that prevents a leaked session_id alone from letting
someone read or continue another user's conversation. Tests the token
logic directly rather than the full agent pipeline, since that would
require real, billed LLM calls to exercise the same code path.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.config import settings
from app.auth import session_tokens
from app.queue import redis_queue

requires_redis = pytest.mark.skipif(
    not settings.redis_url or "localhost" in settings.redis_url,
    reason="No real Redis instance configured (REDIS_URL unset or points to localhost)",
)


@pytest.fixture(autouse=True)
def reset_redis_client():
    """redis_queue.get_client() caches a module-level singleton client
    — correct and desirable in production, where there's only ever
    one long-running event loop. But pytest-asyncio gives each test
    function its own fresh event loop, so a client created during one
    test gets bound to that test's loop; reusing it in a later test
    (a different, by-then-closed loop) crashes with "Future attached
    to a different loop". Resetting the singleton before/after each
    test forces a fresh client bound to whatever loop that specific
    test is actually running under."""
    redis_queue._client = None
    yield
    redis_queue._client = None


@requires_redis
@pytest.mark.asyncio
async def test_issue_token_returns_a_real_token():
    session_id = f"test-session-{uuid.uuid4()}"
    token = await session_tokens.issue_token(session_id)
    assert token
    assert len(token) > 20  # real, high-entropy token, not a placeholder


@requires_redis
@pytest.mark.asyncio
async def test_verify_token_succeeds_with_correct_token():
    session_id = f"test-session-{uuid.uuid4()}"
    token = await session_tokens.issue_token(session_id)
    await session_tokens.verify_token(session_id, token)  # should not raise


@requires_redis
@pytest.mark.asyncio
async def test_verify_token_fails_with_wrong_token():
    session_id = f"test-session-{uuid.uuid4()}"
    await session_tokens.issue_token(session_id)
    with pytest.raises(session_tokens.SessionAuthError):
        await session_tokens.verify_token(session_id, "totally-wrong-token")


@requires_redis
@pytest.mark.asyncio
async def test_verify_token_fails_with_missing_token():
    session_id = f"test-session-{uuid.uuid4()}"
    await session_tokens.issue_token(session_id)
    with pytest.raises(session_tokens.SessionAuthError):
        await session_tokens.verify_token(session_id, None)


@requires_redis
@pytest.mark.asyncio
async def test_verify_token_fails_for_never_issued_session():
    # A fabricated session_id that was never actually issued a token —
    # this is the exact scenario a leaked/fake session_id should NOT
    # be able to bypass.
    fake_session_id = f"never-issued-{uuid.uuid4()}"
    with pytest.raises(session_tokens.SessionAuthError):
        await session_tokens.verify_token(fake_session_id, "any-token-at-all")
