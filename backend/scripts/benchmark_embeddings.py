"""Precision/recall benchmark for the embedding backends (issue #3).

Compares the default `hashing` backend against `fastembed` on a small curated,
labeled retrieval set, so the semantic quality difference is a measured number
rather than an assumption. Opt-in: `fastembed` downloads a ~90 MB model and is
not a default/dev dependency, so this is a script, not a CI test — the metric
math itself is unit-tested in tests/test_embedding_benchmark.py.

Run:
    pip install -e '.[embeddings]'
    python scripts/benchmark_embeddings.py

The `hashing` backend always runs; `fastembed` is skipped with a hint if the
extra isn't installed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.services.knowledge.embedder import EmbedderUnavailableError, get_embedder

# --- labeled corpus --------------------------------------------------------
# Each doc has an id and text; each query names the doc ids that are relevant.
# Topics deliberately overlap in vocabulary so keyword-only matching (hashing)
# is challenged and semantic matching (fastembed) can show its advantage.

DOCS: dict[str, str] = {
    "spider-hunt": (
        "Wolf spiders are hunters that chase prey on the ground instead of spinning webs."
    ),
    "spider-web": "Orb-weaver spiders build large circular webs to trap flying insects.",
    "spider-habitat": "Many wolf spiders live in burrows they dig in soil and leaf litter.",
    "gpu-train": "Training large neural networks is much faster on GPUs with high VRAM.",
    "gpu-infer": (
        "Running inference for a language model needs enough GPU memory to hold the weights."
    ),
    "db-index": "Adding an index to a database column speeds up lookups on that column.",
    "db-backup": "Regular database backups protect against data loss from disk failure.",
    "coffee": "Cold brew coffee is steeped in cold water for many hours to reduce bitterness.",
}

QUERIES: list[tuple[str, list[str]]] = [
    ("How do wolf spiders catch food?", ["spider-hunt"]),
    ("Where do wolf spiders live?", ["spider-habitat"]),
    ("Which spiders make webs?", ["spider-web"]),
    ("Why do I need a lot of GPU memory for AI models?", ["gpu-infer", "gpu-train"]),
    ("How can I make database queries faster?", ["db-index"]),
    ("How do I avoid losing data if a disk dies?", ["db-backup"]),
    ("How is cold brew made?", ["coffee"]),
]

K = 3  # precision@k / recall@k cutoff


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank(query_vec: list[float], doc_vecs: dict[str, list[float]]) -> list[str]:
    """Doc ids ordered by descending cosine similarity to the query."""
    return sorted(doc_vecs, key=lambda doc_id: _cosine(query_vec, doc_vecs[doc_id]), reverse=True)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    top = ranked[:k]
    return sum(1 for d in top if d in relevant) / k


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = ranked[:k]
    return sum(1 for d in top if d in relevant) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for i, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / i
    return 0.0


@dataclass
class Scores:
    precision: float
    recall: float
    mrr: float


def evaluate(backend: str, k: int = K) -> Scores:
    embedder = get_embedder(backend)
    doc_ids = list(DOCS)
    doc_vecs = dict(zip(doc_ids, embedder.embed([DOCS[d] for d in doc_ids]), strict=True))
    query_texts = [q for q, _ in QUERIES]
    query_vecs = embedder.embed(query_texts)

    p = r = mrr = 0.0
    for (_, relevant_ids), qvec in zip(QUERIES, query_vecs, strict=True):
        relevant = set(relevant_ids)
        ranked = rank(qvec, doc_vecs)
        p += precision_at_k(ranked, relevant, k)
        r += recall_at_k(ranked, relevant, k)
        mrr += reciprocal_rank(ranked, relevant)
    n = len(QUERIES)
    return Scores(p / n, r / n, mrr / n)


def main() -> None:
    print(f"Embedding retrieval benchmark — {len(DOCS)} docs, {len(QUERIES)} queries, k={K}\n")
    print(f"{'backend':<12}{'precision@k':>14}{'recall@k':>12}{'MRR':>8}")
    print("-" * 46)
    for backend in ("hashing", "fastembed"):
        try:
            s = evaluate(backend)
        except EmbedderUnavailableError as exc:
            print(f"{backend:<12}  skipped — {exc}")
            continue
        print(f"{backend:<12}{s.precision:>14.3f}{s.recall:>12.3f}{s.mrr:>8.3f}")


if __name__ == "__main__":
    main()
