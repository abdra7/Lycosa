"""Qdrant vector store access. One Qdrant collection per KnowledgeCollection."""

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, ScoredPoint, VectorParams

from app.core.config import get_settings

_client: AsyncQdrantClient | None = None


class KnowledgeStoreError(RuntimeError):
    """A Qdrant operation failed; message names the service and URL to check."""


def _store_error(operation: str, exc: Exception) -> KnowledgeStoreError:
    url = get_settings().qdrant_url
    return KnowledgeStoreError(
        f"Qdrant {operation} failed (url {url}): {exc} — is the qdrant service running?"
    )


def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=get_settings().qdrant_url)
    return _client


def set_qdrant(client: AsyncQdrantClient | None) -> None:
    """Test seam: swap in an in-memory client."""
    global _client
    _client = client


def qdrant_name(collection_id: uuid.UUID) -> str:
    return f"kc_{collection_id.hex}"


async def ensure_collection(name: str, dim: int) -> None:
    client = get_qdrant()
    try:
        if not await client.collection_exists(name):
            await client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
    except Exception as exc:
        raise _store_error(f"collection setup for {name!r}", exc) from exc


async def drop_collection(name: str) -> None:
    """Delete a Qdrant collection and its vectors; a collection that never
    ingested a document has no Qdrant collection, which is fine."""
    client = get_qdrant()
    try:
        if await client.collection_exists(name):
            await client.delete_collection(name)
    except Exception as exc:
        raise _store_error(f"delete of {name!r}", exc) from exc


async def upsert_chunks(
    name: str, vectors: list[list[float]], payloads: list[dict[str, Any]]
) -> None:
    points = [
        PointStruct(id=str(uuid.uuid4()), vector=vector, payload=payload)
        for vector, payload in zip(vectors, payloads, strict=True)
    ]
    try:
        await get_qdrant().upsert(collection_name=name, points=points)
    except Exception as exc:
        raise _store_error(f"upsert into {name!r}", exc) from exc


async def search(name: str, vector: list[float], top_k: int) -> list[ScoredPoint]:
    client = get_qdrant()
    # a collection with no ingested documents has no Qdrant collection yet
    if not await client.collection_exists(name):
        return []
    response = await client.query_points(
        collection_name=name, query=vector, limit=top_k, with_payload=True
    )
    return list(response.points)
