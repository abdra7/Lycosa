"""Ingestion pipeline: upload -> extract -> chunk -> embed -> Qdrant (ADR-013).

Synchronous within the request (consistent with ADR-012); every run leaves an
EmbeddingJob trace, and failures land on the document row instead of a 500.
"""

import hashlib
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, EmbeddingJob, JobStatus, KnowledgeCollection
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.loader import chunk_text, extract_text
from app.services.knowledge.store import ensure_collection, qdrant_name, upsert_chunks


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
        embedder = get_embedder(collection.embedding_backend)
        text = extract_text(filename, data)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("no extractable text in document")

        vectors = embedder.embed(chunks)
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
    except Exception as exc:  # noqa: BLE001 — failure is data, not a 500
        document.status = DocumentStatus.FAILED
        document.error = str(exc)
        job.status = JobStatus.FAILED
        job.error = str(exc)
    else:
        document.status = DocumentStatus.EMBEDDED
        document.chunk_count = len(chunks)
        job.status = JobStatus.SUCCEEDED
        job.chunks_embedded = len(chunks)

    job.finished_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(document)
    return document
