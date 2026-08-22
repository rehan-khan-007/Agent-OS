"""Tests for the chunking module — pure logic, no external dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.chunking import chunk_text


def test_empty_text_returns_no_chunks():
    assert chunk_text("", chunk_size=100, overlap=10) == []


def test_short_text_returns_single_chunk():
    text = "This is a short piece of text."
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].index == 0


def test_long_text_splits_into_multiple_chunks():
    text = "word " * 500  # 2500 chars
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1


def test_chunk_indices_are_sequential():
    text = "word " * 500
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    indices = [c.index for c in chunks]
    assert indices == list(range(len(chunks)))


def test_chunks_respect_overlap():
    # With overlap, consecutive chunks should share some trailing/leading text
    text = "ABCDEFGHIJ" * 20  # 200 chars, predictable pattern
    chunks = chunk_text(text, chunk_size=50, overlap=10)
    assert len(chunks) >= 2
    # The end of chunk N should overlap with the start of chunk N+1
    first_tail = chunks[0].text[-10:]
    second_head = chunks[1].text[:10]
    # Given the repeating pattern, some overlap should be present
    assert len(first_tail) > 0 and len(second_head) > 0


def test_chunk_size_must_exceed_overlap():
    import pytest
    with pytest.raises(ValueError):
        chunk_text("some text here", chunk_size=10, overlap=10)


def test_no_empty_chunks_in_output():
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert all(len(c.text.strip()) > 0 for c in chunks)
