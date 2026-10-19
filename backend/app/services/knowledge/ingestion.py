"""Ingestion pipeline: upload -> extract -> chunk -> embed -> Qdrant (ADR-013).

Synchronous within the request (consistent with ADR-012); every run leaves an
EmbeddingJob trace, and failures land on the document row instead of a 500.

Because an ingestion lives inside its request, a controller crash orphans its
document in `uploaded` and its job in `running`. Each worker holds a lease on
the jobs it is running and renews it on a timer; the leader reaps `running`
jobs whose lease lapsed (ADR-036).
"""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import get_settings
from app.models import Document, DocumentStatus, EmbeddingJob, JobStatus, KnowledgeCollection
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.loader import chunk_text, extract_text
from app.services.knowledge.store import (
    drop_collection,
    ensure_collection,
    qdrant_name,
    upsert_chunks,
)

# Safety net against a stuck pipeline (Qdrant lock, hung model download):
# past this, the run is cancelled and recorded as FAILED instead of leaving
# the document in 'uploaded' and its job 'running' forever (Ticket #104).
INGEST_TIMEOUT_SECONDS = 300.0


class CollectionDeletedError(RuntimeError):
    """The knowledge collection was deleted while its document was ingesting."""


ORPHANED_INGEST_ERROR = (
    "ingestion was interrupted: no controller worker is ingesting this document "
    "any more (a crash or restart) — re-upload the document"
)

# Embedding jobs this process is running right now (ADR-036). An ingestion
# only runs inside its upload request, so this set is exactly this worker's
# share of the live ingestions.
_live_jobs: set[uuid.UUID] = set()


@contextmanager
def _ingesting(job_id: uuid.UUID) -> Iterator[None]:
    """Hold the job's lease while this request ingests its document."""
    _live_jobs.add(job_id)
    try:
        yield
    finally:
        _live_jobs.discard(job_id)


async def heartbeat_live_ingestions(db: AsyncSession) -> int:
    """Renew the lease on every embedding job this worker is running
    (ADR-036). Returns how many leases were renewed."""
    if not _live_jobs:
        return 0
    result = await db.execute(
        update(EmbeddingJob)
        .where(EmbeddingJob.id.in_(list(_live_jobs)), EmbeddingJob.status == JobStatus.RUNNING)
        .values(heartbeat_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount


async def reap_stale_ingestions(db: AsyncSession) -> int:
    """Fail `running` embedding jobs whose lease lapsed, and their `uploaded`
    documents (ADR-036). The worker ingesting them crashed or restarted, and
    the in-request timeout died with it, so they would otherwise hang
    forever. A job some worker is still running renews its lease every
    heartbeat interval and is never picked, however long it runs. Rows from
    before the lease fall back to `started_at`. Returns how many ingestions
    were reaped."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=get_settings().ingest_stale_after_seconds)
    last_beat = func.coalesce(EmbeddingJob.heartbeat_at, EmbeddingJob.started_at)
    stale = (EmbeddingJob.status == JobStatus.RUNNING, last_beat < cutoff)

    reaped = 0
    for job in (await db.execute(select(EmbeddingJob).where(*stale))).scalars().all():
        # compare-and-set: a heartbeat that lands after the select keeps the job
        claimed = await db.execute(
            update(EmbeddingJob)
            .where(EmbeddingJob.id == job.id, *stale)
            .values(status=JobStatus.FAILED, error=ORPHANED_INGEST_ERROR, finished_at=now)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            continue
        await db.execute(
            update(Document)
            .where(Document.id == job.document_id, Document.status == DocumentStatus.UPLOADED)
            .values(status=DocumentStatus.FAILED, error=ORPHANED_INGEST_ERROR)
            .execution_options(synchronize_session=False)
        )
        reaped += 1
    if reaped:
        await db.commit()
    return reaped


async def _extract_embed_store(
    document: Document, collection: KnowledgeCollection, filename: str, data: bytes
) -> int:
    """Run the pipeline core; returns the number of chunks embedded."""
    embedder = get_embedder(collection.embedding_backend)
    # parsing is CPU-bound too: off the event loop, so other clients keep being
    # served and the ingestion timeout can fire while it runs (ADR-030)
    text = await asyncio.to_thread(extract_text, filename, data)
    chunks = await asyncio.to_thread(chunk_text, text)
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
    with _ingesting(job.id):
        return await _run_pipeline(db, collection, document, job, filename, data)


async def _run_pipeline(
    db: AsyncSession,
    collection: KnowledgeCollection,
    document: Document,
    job: EmbeddingJob,
    filename: str,
    data: bytes,
) -> Document:
    """Run the pipeline under its timeout and record the terminal state."""
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
    try:
        await db.commit()
    except StaleDataError:
        # Our terminal-state UPDATE matched rows that no longer exist: the
        # collection was deleted while the pipeline ran (upload/delete race).
        # The pipeline may have re-created the Qdrant collection after the
        # delete flushed it, so drop it again — no vectors may survive for a
        # deleted collection.
        await db.rollback()
        still_exists = (
            await db.execute(
                select(KnowledgeCollection.id).where(KnowledgeCollection.id == collection.id)
            )
        ).scalar_one_or_none()
        if still_exists is None:
            await drop_collection(qdrant_name(collection.id))
            raise CollectionDeletedError(
                f"collection {collection.name!r} was deleted while ingesting {filename!r}"
            ) from None
        raise
    await db.refresh(document)
    return document
