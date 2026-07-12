# RAG embedding benchmark: `hashing` vs `fastembed` (issue #3)

Lycosa ships two embedding backends (ADR-013):

- **`hashing`** (default) — a deterministic bag-of-words hash projection. No
  downloads, no heavy dependencies; relevance is **keyword-level**. Ideal for
  tests and air-gapped LANs.
- **`fastembed`** — ONNX `BAAI/bge-small-en-v1.5` (CPU-only, no torch),
  installed via the `[embeddings]` extra; the model downloads (~90 MB) on first
  use. Relevance is **semantic**.

This benchmark measures the retrieval-quality gap so the choice is a number, not
an assumption. The harness lives in
[`backend/scripts/benchmark_embeddings.py`](../backend/scripts/benchmark_embeddings.py);
its metric math is unit-tested in `backend/tests/test_embedding_benchmark.py`.

## Method

A small curated corpus of **8 documents** across four topics (wolf-spider
behaviour, spider webs, GPU memory for ML, database ops, plus one unrelated
distractor) and **7 natural-language queries**, each labeled with the document
ids that are actually relevant. Queries are deliberately **paraphrased** — they
share little surface vocabulary with the target document (e.g. "How do wolf
spiders catch food?" → a document about *hunting prey on the ground*) so that
keyword-only matching is stressed and semantic matching can show its value.

For each backend we embed the documents and queries, rank documents by cosine
similarity to each query, and compute, at **k = 3**:

- **precision@k** — fraction of the top-k that are relevant,
- **recall@k** — fraction of the relevant docs found in the top-k,
- **MRR** — mean reciprocal rank of the first relevant doc.

## Results

Measured 2026-07-12 (`bge-small-en-v1.5`, `fastembed` 0.3, CPU):

| backend | precision@3 | recall@3 | MRR |
|---|---:|---:|---:|
| `hashing` | 0.333 | 0.929 | 0.929 |
| `fastembed` | **0.381** | **1.000** | **1.000** |

## Reading the numbers

- **`fastembed` retrieves every relevant document in the top 3 (recall 1.000)
  and always ranks a relevant doc first (MRR 1.000).** `hashing` misses one
  relevant doc and occasionally ranks a keyword-overlapping distractor above the
  true match (recall 0.929, MRR 0.929) — the semantic model handles paraphrase
  where the keyword projection can't.
- **precision@3 looks low for both** because most queries have a single relevant
  document, so the ceiling is 1/3 (the other two top-3 slots are non-relevant by
  construction); the one two-relevant query (GPU memory) is where `fastembed`'s
  0.381 pulls ahead of `hashing`'s 0.333. Treat **recall@k and MRR** as the
  discriminating metrics here, not precision@k.

## Recommendation

- **Default `hashing`** remains the right choice for tests, CI, and air-gapped
  or download-averse deployments — it needs no model and still recovers most
  relevant docs on keyword-heavy queries.
- **Switch to `fastembed`** (`EMBEDDING_BACKEND=fastembed`, install
  `pip install 'lycosa-backend[embeddings]'`) when answer quality matters and the
  controller can fetch the model once — especially for knowledge bases queried in
  natural language, where paraphrase between question and source is the norm.
  Note that switching an existing collection's backend requires re-ingestion
  (ADR-013; re-embed job is a backlog item).

## Reproducing

```bash
cd backend
pip install -e '.[embeddings]'
python scripts/benchmark_embeddings.py
```

`hashing` always runs; `fastembed` is skipped with an install hint if the extra
isn't present. Absolute numbers vary slightly with the corpus — extend `DOCS`
and `QUERIES` in the script to reflect your own knowledge base.
