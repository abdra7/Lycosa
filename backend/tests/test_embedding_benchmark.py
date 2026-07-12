"""Unit tests for the embedding-benchmark metric math (issue #3).

The benchmark script itself needs fastembed (a ~90 MB opt-in download) to be
meaningful, so it isn't run in CI — but its precision@k / recall@k / MRR /
ranking helpers are pure functions and are verified here with hand-checked
rankings (no embedding backend, no download).
"""

from scripts.benchmark_embeddings import (
    evaluate,
    precision_at_k,
    rank,
    recall_at_k,
    reciprocal_rank,
)


def test_precision_at_k_counts_relevant_in_top_k() -> None:
    ranked = ["a", "b", "c", "d"]
    assert precision_at_k(ranked, {"a", "c"}, 3) == 2 / 3
    assert precision_at_k(ranked, {"d"}, 3) == 0.0  # relevant doc is below the cutoff
    assert precision_at_k(ranked, {"a"}, 1) == 1.0


def test_recall_at_k_is_fraction_of_relevant_found() -> None:
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, {"a", "d"}, 3) == 0.5  # only 'a' is in the top 3
    assert recall_at_k(ranked, {"a", "b"}, 3) == 1.0
    assert recall_at_k(ranked, set(), 3) == 0.0


def test_reciprocal_rank_uses_first_relevant_position() -> None:
    ranked = ["a", "b", "c"]
    assert reciprocal_rank(ranked, {"a"}) == 1.0
    assert reciprocal_rank(ranked, {"b"}) == 0.5
    assert reciprocal_rank(ranked, {"c"}) == 1 / 3
    assert reciprocal_rank(ranked, {"z"}) == 0.0  # no relevant doc ranked


def test_rank_orders_by_cosine_similarity() -> None:
    query = [1.0, 0.0]
    docs = {"aligned": [1.0, 0.0], "orthogonal": [0.0, 1.0], "opposite": [-1.0, 0.0]}
    assert rank(query, docs) == ["aligned", "orthogonal", "opposite"]


def test_evaluate_on_hashing_backend_runs_and_scores_in_range() -> None:
    # hashing needs no download; the corpus is keyword-separable enough that a
    # keyword embedder scores clearly above zero — guards the end-to-end wiring
    scores = evaluate("hashing")
    assert 0.0 <= scores.precision <= 1.0
    assert 0.0 <= scores.recall <= 1.0
    assert 0.0 <= scores.mrr <= 1.0
    assert scores.mrr > 0.3
