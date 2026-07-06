"""Ingestion pipeline: upload -> extract -> chunk -> embed -> Qdrant (ADR-013).

Synchronous within the request (consistent with ADR-012); every run leaves an
EmbeddingJob trace, and failures land on the document row instead of a 500.
"""

import asyncio
import hashlib
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, EmbeddingJob, JobStatus, KnowledgeCollection
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.loader import chunk_text, extract_text
from app.services.knowledge.store import ensure_collection, qdrant_name, upsert_chunks

# Safety net against a stuck pipeline (Qdrant lock, hung model download):
# past this, the run is cancelled and recorded as FAILED instead of leaving
# the document in 'uploaded' and its job 'running' forever (Ticket #104).
INGEST_TIMEOUT_SECONDS = 300.0


async def _extract_embed_store(
    document: Document, collection: KnowledgeCollection, filename: str, data: bytes
) -> int:
    """Run the pipeline core; returns the number of chunks embedded."""
    embedder = get_embedder(collection.embedding_backend)
    text = extract_text(filename, data)
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("no extractable text in document")

    # embedding is CPU-bound (ONNX inference); keep it off the event loop
    vectors = await asyncio.to_thread(embedder.embed, chunks)
    name = qdrant_name(collection.id)
    await ensure_collection(name, collection.embedding_dim)
    await upsert_chunks(
        name,
        vectors,
        payloads=[
            {
                "document_id": str(document.id),
                "collection_id": str(collection.id),
                "filename": filename,
                "chunk_index": index,
                "text": chunk,
            }
            for index, chunk in enumerate(chunks)
        ],
    )
    return len(chunks)


async def ingest_document(
    db: AsyncSession,
    collection: KnowledgeCollection,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> Document:
    document = Document(
        collection_id=collection.id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        status=DocumentStatus.UPLOADED,
    )
    db.add(document)
    await db.flush()
    job = EmbeddingJob(document_id=document.id, status=JobStatus.RUNNING)
    db.add(job)
    await db.commit()

    try:
        chunk_count = await asyncio.wait_for(
            _extract_embed_store(document, collection, filename, data),
            timeout=INGEST_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        error = (
            f"ingestion timed out after {INGEST_TIMEOUT_SECONDS:.0f} s — check the "
            "qdrant service and the embedding model download, then re-upload"
        )
        document.status = DocumentStatus.FAILED
        document.error = error
        job.status = JobStatus.FAILED
        job.error = error
    except Exception as exc:  # noqa: BLE001 — failure is data, not a 500
        document.status = DocumentStatus.FAILED
        document.error = str(exc)
        job.status = JobStatus.FAILED
        job.error = str(exc)
    else:
        document.status = DocumentStatus.EMBEDDED
        document.chunk_count = chunk_count
        job.status = JobStatus.SUCCEEDED
        job.chunks_embedded = chunk_count

    job.finished_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(document)
    return document
