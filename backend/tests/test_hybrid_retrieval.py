"""
Tests for BM25 keyword search and RRF-based hybrid retrieval.

reciprocal_rank_fusion() and the BM25 tokenizer/scoring are pure
logic — tested here directly against synthetic data, no database or
embedding API calls required. hybrid_search() and bm25_search()
themselves (the DB-querying wrappers) aren't separately tested here,
since exercising them meaningfully would require a real embedding API
call (real cost) — the pure logic they depend on is what's actually
being verified.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rank_bm25 import BM25Okapi

from app.retrieval.hybrid import reciprocal_rank_fusion
from app.retrieval.bm25_search import _tokenize


class _FakeChunk:
    def __init__(self, id, label=None):
        self.id = id
        self.label = label or str(id)


def test_item_found_by_both_methods_ranks_first():
    a, b, c, d = _FakeChunk(1), _FakeChunk(2), _FakeChunk(3), _FakeChunk(4)

    vector_ranked = [a, b, c]
    bm25_ranked = [a, d]

    result = reciprocal_rank_fusion(vector_ranked, bm25_ranked)
    assert result[0].id == 1


def test_moderate_rank_in_both_lists_beats_top_rank_in_one():
    a, b = _FakeChunk(1), _FakeChunk(2)

    vector_ranked = [b, _FakeChunk(3), a]  # a ranked last (index 2) by vector
    bm25_ranked = [a]  # a ranked first (index 0) by BM25, b absent

    result = reciprocal_rank_fusion(vector_ranked, bm25_ranked)
    assert result[0].id == a.id


def test_item_absent_from_all_lists_never_appears():
    a, b, c, d = _FakeChunk(1), _FakeChunk(2), _FakeChunk(3), _FakeChunk(4)

    result = reciprocal_rank_fusion([a, b], [c, d])
    result_ids = {item.id for item in result}
    assert result_ids == {1, 2, 3, 4}


def test_empty_lists_produce_empty_result():
    assert reciprocal_rank_fusion([], []) == []


def test_single_nonempty_list_preserves_its_order():
    a, b, c = _FakeChunk(1), _FakeChunk(2), _FakeChunk(3)
    result = reciprocal_rank_fusion([a, b, c], [])
    assert [item.id for item in result] == [1, 2, 3]


def test_variadic_fusion_supports_more_than_two_lists():
    """reciprocal_rank_fusion accepts *ranked_lists — confirm 3+ lists
    combine correctly, not just the two-list case used in practice."""
    a, b, c = _FakeChunk(1), _FakeChunk(2), _FakeChunk(3)

    list1 = [a]
    list2 = [b]
    list3 = [c, a]  # a also appears here, at rank 1

    result = reciprocal_rank_fusion(list1, list2, list3)
    # a appears in list1 (rank 0) and list3 (rank 1) — should outrank
    # b and c, which each appear in exactly one list at rank 0
    assert result[0].id == a.id


def test_tokenizer_lowercases_and_strips_punctuation():
    tokens = _tokenize("The GRAPE Algorithm: Quantum-Optimal Control (2023)!")
    assert tokens == ["the", "grape", "algorithm", "quantum", "optimal", "control", "2023"]


def test_bm25_ranks_exact_keyword_match_highest():
    corpus = [
        "The GRAPE algorithm optimizes pulse sequences for quantum control",
        "Newton-Raphson methods provide second-order convergence",
        "A completely unrelated document about cooking recipes and pasta",
    ]
    tokenized = [_tokenize(doc) for doc in corpus]
    bm25 = BM25Okapi(tokenized)

    scores = bm25.get_scores(_tokenize("GRAPE algorithm"))
    assert scores.argmax() == 0


def test_bm25_distinguishes_between_different_queries():
    # A 3rd, clearly-irrelevant document is included deliberately, not
    # just for realism: with exactly 2 documents, a term appearing in
    # only 1 of them gets IDF = log((2-1+0.5)/(1+0.5)) = log(1) = 0 —
    # a real mathematical edge case of BM25's classic IDF formula that
    # degenerates at very small corpus sizes. A 3rd document avoids
    # this degenerate case, matching how BM25 actually behaves at any
    # realistic corpus size (this project's real corpus has hundreds
    # of chunks, so this only shows up in a minimal synthetic test).
    corpus = [
        "The GRAPE algorithm optimizes pulse sequences for quantum control",
        "Newton-Raphson methods provide second-order convergence",
        "A completely unrelated document about cooking recipes and pasta",
    ]
    tokenized = [_tokenize(doc) for doc in corpus]
    bm25 = BM25Okapi(tokenized)

    scores = bm25.get_scores(_tokenize("Newton-Raphson convergence"))
    assert scores.argmax() == 1
