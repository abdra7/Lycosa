"""Pluggable embedding backends (ADR-013).

- hashing (default): deterministic bag-of-words hash projection. No downloads,
  no heavy deps; relevance is keyword-level. Ideal for tests and air-gapped
  LANs; swap to fastembed for semantic quality.
- fastembed: ONNX MiniLM via the `fastembed` package (CPU-only, no torch).
  Model downloads on first use. Install with the [embeddings] extra.
"""

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

_WORD_RE = re.compile(r"[a-z0-9]+")


class EmbedderUnavailableError(RuntimeError):
    """The configured embedding backend cannot be used; message says how to fix it."""


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    name = "hashing"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in _WORD_RE.findall(text.lower()):
            digest = hashlib.md5(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class FastEmbedEmbedder:
    name = "fastembed"
    dim = 384  # BAAI/bge-small-en-v1.5

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from fastembed import TextEmbedding  # deferred: optional heavy dep
        except ImportError as exc:
            raise EmbedderUnavailableError(
                "fastembed is not installed — install the embeddings extra: "
                "pip install 'lycosa-backend[embeddings]'"
            ) from exc
        try:
            self._model = TextEmbedding(model_name=model)
        except Exception as exc:
            raise EmbedderUnavailableError(
                f"failed to load fastembed model {model!r} (first use downloads ~90 MB; "
                f"check the controller's internet access or pre-seed the model cache): {exc}"
            ) from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]


@lru_cache
def get_embedder(name: str | None = None) -> Embedder:
    backend = name or get_settings().embedding_backend
    if backend == "hashing":
        return HashingEmbedder(dim=get_settings().embedding_dim)
    if backend == "fastembed":
        return FastEmbedEmbedder()
    raise ValueError(f"unknown embedding backend: {backend!r}")
