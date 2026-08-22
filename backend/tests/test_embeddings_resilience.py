"""
Failure-injection tests for the embedding client's retry policy.
Same pattern as test_llm_resilience.py — verifies transient failures
are retried, 4xx errors are not, and retries eventually give up.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import httpx

from app.retrieval.embeddings import embed_text, embed_chunks, _is_retryable
from app.config import settings


def _mock_response(status_code: int, json_body: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings")
    return httpx.Response(status_code=status_code, json=json_body, request=request)


def test_is_retryable_matches_llm_client_policy():
    request = httpx.Request("POST", "https://example.com")

    assert _is_retryable(httpx.TimeoutException("timed out")) is True
    assert _is_retryable(httpx.ConnectError("connection refused")) is True

    resp_500 = httpx.Response(500, request=request)
    assert _is_retryable(httpx.HTTPStatusError("server error", request=request, response=resp_500)) is True

    resp_400 = httpx.Response(400, request=request)
    assert _is_retryable(httpx.HTTPStatusError("bad request", request=request, response=resp_400)) is False


def test_embed_text_recovers_from_transient_timeout(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    call_count = {"n": 0}

    def mock_post(url, headers, json, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.TimeoutException("timed out")
        return _mock_response(200, {"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr(httpx, "post", mock_post)

    result = embed_text("some text")
    assert result == [0.1, 0.2, 0.3]
    assert call_count["n"] == 2


def test_embed_text_does_not_retry_400(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    call_count = {"n": 0}

    def mock_post(url, headers, json, timeout):
        call_count["n"] += 1
        return _mock_response(400, {"error": "invalid input"})

    monkeypatch.setattr(httpx, "post", mock_post)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        embed_text("some text")

    assert exc_info.value.response.status_code == 400
    assert call_count["n"] == 1


def test_embed_chunks_skips_persistently_failing_chunk_but_keeps_others(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    call_count = {"n": 0}

    def mock_post(url, headers, json, timeout):
        call_count["n"] += 1
        # The second chunk ("bad chunk") always fails; others succeed immediately.
        if json["input"] == "bad chunk":
            raise httpx.TimeoutException("always times out")
        return _mock_response(200, {"data": [{"embedding": [1.0]}]})

    monkeypatch.setattr(httpx, "post", mock_post)

    results = embed_chunks(["good chunk 1", "bad chunk", "good chunk 2"])

    assert results[0] == [1.0]
    assert results[1] is None  # failed after exhausting retries, but didn't abort the batch
    assert results[2] == [1.0]  # later chunks still got processed
