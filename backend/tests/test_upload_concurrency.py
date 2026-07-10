"""IT-API-02: concurrent document uploads — no deadlocks, no lost updates.

Concurrency here is real: uploads run through the ASGI app in parallel on the
event loop, embeddings run in worker threads, and the file-based SQLite test
database takes concurrent connections. The gated-embedder tests make race
windows deterministic instead of sleeping and hoping.
"""

import asyncio
import threading
import uuid
from contextlib import suppress

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, EmbeddingJob, JobStatus
from app.services.knowledge.embedder import HashingEmbedder
from app.services.knowledge.store import qdrant_name
from tests.conftest import OPERATOR_EMAIL, bearer, login
from tests.test_knowledge_ingest import MARKDOWN, create_collection, upload

GATHER_TIMEOUT = 60.0  # a deadlock fails the test instead of hanging the suite


def _doc(i: int) -> bytes:
    return (
        f"# Document {i}\n\n"
        f"Wolf spider fact number {i}: lycosa hunts on the ground.\n\n"
        f"{'Additional detail paragraph. ' * (i + 1)}"
    ).encode()


class GatedEmbedder:
    """HashingEmbedder that parks inside embed() until the test releases it,
    pinning the pipeline mid-flight so races can be staged deterministically."""

    name = "hashing"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.started = threading.Event()
        self.release = threading.Event()
        self._inner = HashingEmbedder(dim=dim)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.started.set()
        assert self.release.wait(timeout=30), "test never released the gated embedder"
        return self._inner.embed(texts)


async def _wait_for_document(db_session: AsyncSession, filename: str) -> Document:
    """Poll until the named document reaches a terminal status."""
    async with asyncio.timeout(10):
        while True:
            db_session.expire_all()
            document = (
                await db_session.execute(select(Document).where(Document.filename == filename))
            ).scalar_one_or_none()
            if document is not None and document.status != DocumentStatus.UPLOADED:
                return document
            await asyncio.sleep(0.05)


async def _wait_until_parked(gated: GatedEmbedder) -> None:
    """Block (off the event loop) until the pipeline reaches embed()."""
    assert await asyncio.to_thread(gated.started.wait, 10), "pipeline never reached embed()"


async def test_concurrent_uploads_same_collection(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="burst")
    collection_id = uuid.UUID(collection["id"])

    results = await asyncio.wait_for(
        asyncio.gather(
            *(upload(client, token, collection["id"], f"doc-{i}.md", _doc(i)) for i in range(8))
        ),
        timeout=GATHER_TIMEOUT,
    )

    assert len(results) == 8
    assert all(r["status"] == "embedded" for r in results), [r["error"] for r in results]
    assert len({r["id"] for r in results}) == 8

    jobs = (await db_session.execute(select(EmbeddingJob))).scalars().all()
    assert len(jobs) == 8
    assert all(j.status == JobStatus.SUCCEEDED for j in jobs)

    # every chunk from every parallel upload landed; none overwrote another
    points = await qdrant.count(qdrant_name(collection_id))
    assert points.count == sum(r["chunk_count"] for r in results)


async def test_concurrent_uploads_across_collections(
    client: AsyncClient, users: dict, qdrant
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collections = [await create_collection(client, token, name=f"shelf-{i}") for i in range(3)]

    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                upload(client, token, c["id"], f"c{i}-d{j}.md", _doc(i * 2 + j))
                for i, c in enumerate(collections)
                for j in range(2)
            )
        ),
        timeout=GATHER_TIMEOUT,
    )
    assert all(r["status"] == "embedded" for r in results)

    # vectors stayed in their own collections
    for c in collections:
        expected = sum(r["chunk_count"] for r in results if r["collection_id"] == c["id"])
        points = await qdrant.count(qdrant_name(uuid.UUID(c["id"])))
        assert points.count == expected


async def test_client_disconnect_mid_ingestion_still_records_terminal_state(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession, monkeypatch
) -> None:
    """Ticket #104: the upload handler shields its ingestion task, so a client
    that times out mid-embedding must not leave the document stuck in
    'uploaded' with its job 'running' forever."""
    gated = GatedEmbedder()
    monkeypatch.setattr("app.services.knowledge.ingestion.get_embedder", lambda name=None: gated)

    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="dropped-call")

    request_task = asyncio.create_task(
        client.post(
            f"/api/v1/knowledge/collections/{collection['id']}/documents",
            files={"file": ("orphan.md", MARKDOWN, "text/markdown")},
            headers=bearer(token),
        )
    )
    await _wait_until_parked(gated)  # pipeline is now inside embed()
    request_task.cancel()  # the client gives up mid-ingestion
    with suppress(asyncio.CancelledError):
        await request_task
    gated.release.set()

    document = await _wait_for_document(db_session, "orphan.md")
    assert document.status == DocumentStatus.EMBEDDED
    assert document.chunk_count > 0
    job = (
        await db_session.execute(
            select(EmbeddingJob).where(EmbeddingJob.document_id == document.id)
        )
    ).scalar_one()
    assert job.status == JobStatus.SUCCEEDED
    assert job.finished_at is not None
    points = await qdrant.count(qdrant_name(uuid.UUID(collection["id"])))
    assert points.count == document.chunk_count


async def test_upload_delete_race_leaves_no_zombie_vectors(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession, monkeypatch
) -> None:
    """Race probe (found a real bug, since fixed): DELETE the collection while
    an upload is parked mid-embed. The losing upload gets a 409 and the system
    may not end up with Qdrant vectors for a collection that no longer exists."""
    gated = GatedEmbedder()
    monkeypatch.setattr("app.services.knowledge.ingestion.get_embedder", lambda name=None: gated)

    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="pulled-rug")
    collection_id = uuid.UUID(collection["id"])

    upload_task = asyncio.create_task(
        client.post(
            f"/api/v1/knowledge/collections/{collection['id']}/documents",
            files={"file": ("late.md", MARKDOWN, "text/markdown")},
            headers=bearer(token),
        )
    )
    await _wait_until_parked(gated)  # upload committed its rows, now parked

    response = await client.delete(
        f"/api/v1/knowledge/collections/{collection['id']}", headers=bearer(token)
    )
    assert response.status_code == 204, response.text

    gated.release.set()
    upload_response = await asyncio.wait_for(upload_task, timeout=GATHER_TIMEOUT)

    # the losing upload gets a clean conflict, not a 5xx…
    assert upload_response.status_code == 409, upload_response.text
    assert "deleted" in upload_response.json()["error"]["message"]
    # …must not resurrect DB metadata for the deleted collection…
    db_session.expire_all()
    documents = (
        (await db_session.execute(select(Document).where(Document.collection_id == collection_id)))
        .scalars()
        .all()
    )
    assert documents == []
    # …and must not leave zombie vectors behind in Qdrant
    assert not await qdrant.collection_exists(qdrant_name(collection_id))
