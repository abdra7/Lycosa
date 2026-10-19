"""E2E-02: ingestion crash recovery.

A controller crash mid-ingest (power loss, OOM-kill, `docker restart`) dies
after ingest_document's first commit — leaving the document 'uploaded' and its
job 'running' with no pipeline alive to ever finish them. The in-request
timeout is no help: it died with the process. Once the job's lease has lapsed
the leader's reaper must fail those rows, so the operator sees an actionable
failure instead of a document hung in 'uploaded' forever (ADR-036).

The "crash" seeds exactly the rows that first commit persists, with a lease
nobody renewed for an hour; the "restart" enters the app's real lifespan and
waits for its lease loop, so the startup wiring itself is under test, not
just the reaper.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import main
from app.core.config import get_settings
from app.main import app
from app.models import Document, DocumentStatus, EmbeddingJob, JobStatus
from app.services.knowledge.ingestion import ORPHANED_INGEST_ERROR, reap_stale_ingestions
from tests.conftest import OPERATOR_EMAIL, bearer, login
from tests.test_ingest_reaper import LONG_AGO, seed_ingestion
from tests.test_knowledge_ingest import MARKDOWN, create_collection, upload


async def test_restart_recovers_stuck_ingestion_and_operator_can_reupload(
    client: AsyncClient,
    users: dict,
    qdrant,
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="fragile")
    stuck, _ = await seed_ingestion(db_session, uuid.UUID(collection["id"]))

    # -- "restart the stack": the app's real lease loop reaps the orphan -----
    fast = get_settings().model_copy(update={"ingest_heartbeat_interval_seconds": 1})
    monkeypatch.setattr(main, "get_settings", lambda: fast)
    async with app.router.lifespan_context(app):
        for _ in range(50):
            await asyncio.sleep(0.1)
            async with sessionmaker_() as db:
                document = await db.get(Document, stuck.id)
                if document.status != DocumentStatus.UPLOADED:
                    break

    assert document.status == DocumentStatus.FAILED
    assert document.error == ORPHANED_INGEST_ERROR
    job = (
        await db_session.execute(select(EmbeddingJob).where(EmbeddingJob.document_id == stuck.id))
    ).scalar_one()
    assert job.status == JobStatus.FAILED
    assert job.error == ORPHANED_INGEST_ERROR
    assert job.finished_at is not None

    # -- the operator sees an actionable failure, not a forever-pending doc --
    listed = await client.get(
        f"/api/v1/knowledge/collections/{collection['id']}/documents", headers=bearer(token)
    )
    row = next(d for d in listed.json() if d["id"] == str(stuck.id))
    assert row["status"] == "failed"
    assert "re-upload" in row["error"]

    # -- and the graceful recovery completes: re-upload works end to end -----
    document = await upload(client, token, collection["id"], "interrupted.md", MARKDOWN)
    assert document["status"] == "embedded"
    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "wolf spider venom", "collection": "fragile"},
        headers=bearer(token),
    )
    assert response.status_code == 200
    assert response.json()["chunks"], "re-uploaded document must be retrievable"


async def test_recovery_leaves_healthy_documents_alone(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    """Recovery must only touch orphans: embedded and already-failed documents
    (and their terminal jobs) are never reaped, however old their lease."""
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="steady")
    healthy = await upload(client, token, collection["id"], "fine.md", MARKDOWN)
    assert healthy["status"] == "embedded"
    failed = await upload(client, token, collection["id"], "broken.pdf", b"%PDF-1.4 garbage")
    assert failed["status"] == "failed"
    old = datetime.now(UTC) - LONG_AGO
    await db_session.execute(update(EmbeddingJob).values(heartbeat_at=old, started_at=old))
    await db_session.commit()

    recovered = await reap_stale_ingestions(db_session)
    assert recovered == 0

    db_session.expire_all()
    documents = (
        (
            await db_session.execute(
                select(Document).where(Document.collection_id == uuid.UUID(collection["id"]))
            )
        )
        .scalars()
        .all()
    )
    by_name = {d.filename: d for d in documents}
    assert by_name["fine.md"].status == DocumentStatus.EMBEDDED
    assert by_name["fine.md"].error is None
    assert by_name["broken.pdf"].status == DocumentStatus.FAILED
    assert by_name["broken.pdf"].error != ORPHANED_INGEST_ERROR


async def test_recovery_reports_count_and_is_idempotent(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="twice")
    await seed_ingestion(db_session, uuid.UUID(collection["id"]), "one.md")
    await seed_ingestion(db_session, uuid.UUID(collection["id"]), "two.md")

    assert await reap_stale_ingestions(db_session) == 2
    # a second sweep finds nothing left to recover
    assert await reap_stale_ingestions(db_session) == 0
