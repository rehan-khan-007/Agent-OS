"""
Failure-injection tests for the LLM client's retry/timeout policy.

These simulate real failure modes (timeouts, connection drops, 5xx
server errors, 4xx client errors) by monkeypatching the underlying
httpx call, and verify:

1. Transient failures (timeout, connection error, 5xx) are retried
   and the call eventually succeeds if a later attempt works.
2. Client errors (4xx) are never retried — retrying a malformed
   request just repeats the same failure, so this checks the fix is
   real, not just described in a docstring.
3. If every retry attempt fails, the original exception still
   propagates (failures aren't silently swallowed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from httpx import Request, Response, HTTPStatusError, TimeoutException, ConnectError

from app.llm.client import _call_openrouter, _is_retryable


def _mock_response(status_code: int, json_body: dict) -> Response:
    request = Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return Response(status_code=status_code, json=json_body, request=request)


class _FlakyPost:
    """Simulates an endpoint that fails N times, then succeeds."""

    def __init__(self, fail_count: int, failure: Exception):
        self.fail_count = fail_count
        self.failure = failure
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise self.failure
        return _mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})


class _AlwaysFailsWithStatus:
    """Simulates an endpoint that always returns a given status code."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        resp = _mock_response(self.status_code, {"error": "simulated failure"})
        resp.raise_for_status()  # will raise HTTPStatusError since 4xx/5xx


def test_is_retryable_classifies_correctly():
    request = Request("POST", "https://example.com")

    assert _is_retryable(TimeoutException("timed out")) is True
    assert _is_retryable(ConnectError("connection refused")) is True

    resp_500 = Response(500, request=request)
    assert _is_retryable(HTTPStatusError("server error", request=request, response=resp_500)) is True

    resp_400 = Response(400, request=request)
    assert _is_retryable(HTTPStatusError("bad request", request=request, response=resp_400)) is False

    resp_401 = Response(401, request=request)
    assert _is_retryable(HTTPStatusError("unauthorized", request=request, response=resp_401)) is False

    assert _is_retryable(ValueError("unrelated error")) is False


@pytest.mark.asyncio
async def test_recovers_from_transient_timeout(monkeypatch):
    flaky = _FlakyPost(fail_count=2, failure=TimeoutException("timed out"))

    async def mock_post(self, url, json, headers):
        return await flaky(url, json=json, headers=headers)

    from httpx import AsyncClient
    monkeypatch.setattr(AsyncClient, "post", mock_post)

    result = await _call_openrouter({"model": "test"}, {"Authorization": "Bearer x"})
    assert result["choices"][0]["message"]["content"] == "ok"
    assert flaky.calls == 3  # failed twice, succeeded on the 3rd (final allowed) attempt


@pytest.mark.asyncio
async def test_recovers_from_transient_connection_error(monkeypatch):
    flaky = _FlakyPost(fail_count=1, failure=ConnectError("connection refused"))

    async def mock_post(self, url, json, headers):
        return await flaky(url, json=json, headers=headers)

    from httpx import AsyncClient
    monkeypatch.setattr(AsyncClient, "post", mock_post)

    result = await _call_openrouter({"model": "test"}, {"Authorization": "Bearer x"})
    assert result["choices"][0]["message"]["content"] == "ok"
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_recovers_from_5xx_server_error(monkeypatch):
    call_count = {"n": 0}

    async def mock_post(self, url, json, headers):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _mock_response(503, {"error": "service unavailable"})
        return _mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "recovered"}}]})

    from httpx import AsyncClient
    monkeypatch.setattr(AsyncClient, "post", mock_post)

    result = await _call_openrouter({"model": "test"}, {"Authorization": "Bearer x"})
    assert result["choices"][0]["message"]["content"] == "recovered"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_400_bad_request_is_never_retried(monkeypatch):
    call_count = {"n": 0}

    async def mock_post(self, url, json, headers):
        call_count["n"] += 1
        return _mock_response(400, {"error": "malformed message history"})

    from httpx import AsyncClient
    monkeypatch.setattr(AsyncClient, "post", mock_post)

    with pytest.raises(HTTPStatusError) as exc_info:
        await _call_openrouter({"model": "test"}, {"Authorization": "Bearer x"})

    assert exc_info.value.response.status_code == 400
    assert call_count["n"] == 1  # exactly one attempt — no retries on a 4xx


@pytest.mark.asyncio
async def test_exhausting_all_retries_still_raises(monkeypatch):
    call_count = {"n": 0}

    async def mock_post(self, url, json, headers):
        call_count["n"] += 1
        raise TimeoutException("always times out")

    from httpx import AsyncClient
    monkeypatch.setattr(AsyncClient, "post", mock_post)

    with pytest.raises(TimeoutException):
        await _call_openrouter({"model": "test"}, {"Authorization": "Bearer x"})

    assert call_count["n"] == 3  # stop_after_attempt(3) — tried exactly 3 times, then gave up
