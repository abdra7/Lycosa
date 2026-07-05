"""Knowledge Router (SDD FR-9): semantic retrieval without the caller ever
naming a node or physical location.

v1 is "federated-lite": when no collection is named, the query is embedded
once per backend and searched across every collection, merged by score with
freshness as tiebreak. Real multi-node federation implements this same
interface later.
"""

import time
import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import RETRIEVAL_DURATION, RETRIEVALS_TOTAL
from app.models import KnowledgeCollection, RetrievalRequest
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.store import qdrant_name, search


class RetrievedChunk(BaseModel):
    text: str
    source: str  # filename
    collection: str
    score: float
    document_id: str


class RetrievalResult(BaseModel):
    chunks: list[RetrievedChunk]
    context_text: str
    latency_ms: float


class UnknownCollectionError(LookupError):
    pass


def _build_context(chunks: list[RetrievedChunk]) -> str:
    return "\n\n---\n\n".join(f"[{c.collection}/{c.source}] {c.text}" for c in chunks)


async def retrieve(
    db: AsyncSession,
    query: str,
    collection_name: str | None = None,
    top_k: int = 5,
    requested_by_user_id: uuid.UUID | None = None,
    requested_by_api_key_id: uuid.UUID | None = None,
) -> RetrievalResult:
    started = time.perf_counter()

    if collection_name is not None:
        found = (
            await db.execute(
                select(KnowledgeCollection).where(KnowledgeCollection.name == collection_name)
            )
        ).scalar_one_or_none()
        if found is None:
            raise UnknownCollectionError(collection_name)
        collections = [found]
    else:
        collections = list(
            (
                await db.execute(
                    select(KnowledgeCollection).order_by(KnowledgeCollection.updated_at.desc())
                )
            ).scalars()
        )

    # embed the query once per distinct backend (collections may differ)
    query_vectors: dict[str, list[float]] = {}
    chunks: list[RetrievedChunk] = []
    for collection in collections:
        backend = collection.embedding_backend
        if backend not in query_vectors:
            query_vectors[backend] = get_embedder(backend).embed([query])[0]
        points = await search(qdrant_name(collection.id), query_vectors[backend], top_k)
        for point in points:
            payload = point.payload or {}
            chunks.append(
                RetrievedChunk(
                    text=payload.get("text", ""),
                    source=payload.get("filename", "unknown"),
                    collection=collection.name,
                    score=point.score,
                    document_id=payload.get("document_id", ""),
                )
            )

    # merge across collections by relevance; collection order (freshness)
    # already breaks exact ties because sort is stable
    chunks.sort(key=lambda c: c.score, reverse=True)
    chunks = chunks[:top_k]

    latency_ms = (time.perf_counter() - started) * 1000
    RETRIEVALS_TOTAL.inc()
    RETRIEVAL_DURATION.observe(latency_ms / 1000)
    db.add(
        RetrievalRequest(
            query=query,
            collection_id=collections[0].id if collection_name is not None else None,
            top_k=top_k,
            results_count=len(chunks),
            latency_ms=latency_ms,
            requested_by_user_id=requested_by_user_id,
            requested_by_api_key_id=requested_by_api_key_id,
        )
    )
    await db.commit()

    return RetrievalResult(
        chunks=chunks, context_text=_build_context(chunks), latency_ms=latency_ms
    )
