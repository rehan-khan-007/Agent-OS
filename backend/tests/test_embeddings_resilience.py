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


def test_embed_chunks_batches_instead_of_one_call_per_chunk(monkeypatch):
    """
    The core throughput claim: embedding 250 chunks should take only
    3 API calls (ceil(250/100) with BATCH_SIZE=100), not 250 — this
    is what actually makes large-corpus ingestion practical.
    """
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    call_count = {"n": 0}

    def mock_post(url, headers, json, timeout):
        call_count["n"] += 1
        texts = json["input"]
        assert isinstance(texts, list), "batch call should send a list of texts, not a single string"
        return _mock_response(200, {
            "data": [{"index": i, "embedding": [float(i)]} for i in range(len(texts))]
        })

    monkeypatch.setattr(httpx, "post", mock_post)

    chunks = [f"chunk {i}" for i in range(250)]
    results = embed_chunks(chunks)

    assert call_count["n"] == 3, f"expected 3 batched calls for 250 chunks, got {call_count['n']}"
    assert len(results) == 250
    assert all(r is not None for r in results)


def test_embed_chunks_preserves_order_via_index_field(monkeypatch):
    """
    A batch response's `data` array isn't guaranteed to preserve
    input order — only the `index` field is. Simulates a
    provider returning results out of order and confirms embed_chunks
    still assigns each embedding to the correct original chunk.
    """
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    def mock_post(url, headers, json, timeout):
        texts = json["input"]
        # Return results in REVERSED order on purpose
        data = [{"index": i, "embedding": [float(i)]} for i in range(len(texts))]
        data.reverse()
        return _mock_response(200, {"data": data})

    monkeypatch.setattr(httpx, "post", mock_post)

    chunks = ["a", "b", "c"]
    results = embed_chunks(chunks)

    assert results == [[0.0], [1.0], [2.0]], (
        "results should be reordered by index, not trust response array order"
    )


def test_embed_chunks_falls_back_to_individual_calls_when_batch_fails(monkeypatch):
    """
    If an entire batch call fails (even after its own retries), the
    whole batch shouldn't be lost — embed_chunks should fall back to
    embedding each chunk in that batch individually via embed_text.
    """
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    batch_call_count = {"n": 0}
    individual_call_count = {"n": 0}

    def mock_post(url, headers, json, timeout):
        text_or_texts = json["input"]
        if isinstance(text_or_texts, list):
            batch_call_count["n"] += 1
            raise httpx.TimeoutException("batch call always times out")
        else:
            individual_call_count["n"] += 1
            return _mock_response(200, {"data": [{"embedding": [1.0]}]})

    monkeypatch.setattr(httpx, "post", mock_post)

    chunks = ["a", "b", "c"]
    results = embed_chunks(chunks)

    assert batch_call_count["n"] == 3, "batch call should have been retried 3 times before giving up"
    assert individual_call_count["n"] == 3, "should fall back to one individual call per chunk in the failed batch"
    assert results == [[1.0], [1.0], [1.0]]


def test_embed_chunks_batch_of_more_than_batch_size_splits_correctly(monkeypatch):
    """Confirms a batch larger than BATCH_SIZE is actually split into
    multiple requests, each no larger than BATCH_SIZE."""
    from app.retrieval.embeddings import BATCH_SIZE
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    seen_batch_sizes = []

    def mock_post(url, headers, json, timeout):
        texts = json["input"]
        seen_batch_sizes.append(len(texts))
        return _mock_response(200, {
            "data": [{"index": i, "embedding": [0.0]} for i in range(len(texts))]
        })

    monkeypatch.setattr(httpx, "post", mock_post)

    chunks = [f"chunk {i}" for i in range(BATCH_SIZE + 30)]
    embed_chunks(chunks)

    assert all(size <= BATCH_SIZE for size in seen_batch_sizes)
    assert sum(seen_batch_sizes) == BATCH_SIZE + 30
