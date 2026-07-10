"""E2E-02: ingestion crash recovery.

A controller crash mid-ingest (power loss, OOM-kill, `docker restart`) dies
after ingest_document's first commit — leaving the document 'uploaded' and its
job 'running' with no pipeline alive to ever finish them. The in-request
timeout is no help: it died with the process. On restart the lifespan must
recover those rows so the operator sees an actionable failure instead of a
document hung in 'uploaded' forever.

The "crash" seeds exactly the rows that first commit persists; the "restart"
enters the app's real lifespan context, so the startup wiring itself is under
test, not just the recovery helper.
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import Document, DocumentStatus, EmbeddingJob, JobStatus
from app.services.knowledge.ingestion import RESTART_RECOVERY_ERROR, recover_stuck_ingestions
from tests.conftest import OPERATOR_EMAIL, bearer, login
from tests.test_knowledge_ingest import MARKDOWN, create_collection, upload


async def _seed_crashed_ingestion(
    db_session: AsyncSession, collection_id: str, filename: str = "interrupted.md"
) -> Document:
    """Persist the exact state ingest_document commits before the pipeline
    runs — what a kill -9 mid-embed leaves behind."""
    document = Document(
        collection_id=uuid.UUID(collection_id),
        filename=filename,
        content_type="text/markdown",
        size_bytes=len(MARKDOWN),
        sha256="0" * 64,
        status=DocumentStatus.UPLOADED,
    )
    db_session.add(document)
    await db_session.flush()
    db_session.add(EmbeddingJob(document_id=document.id, status=JobStatus.RUNNING))
    await db_session.commit()
    await db_session.refresh(document)
    return document


async def test_restart_recovers_stuck_ingestion_and_operator_can_reupload(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="fragile")
    stuck = await _seed_crashed_ingestion(db_session, collection["id"])

    # -- "restart the stack": the app's real startup hook runs recovery ------
    async with app.router.lifespan_context(app):
        pass

    db_session.expire_all()
    await db_session.refresh(stuck)
    assert stuck.status == DocumentStatus.FAILED
    assert stuck.error == RESTART_RECOVERY_ERROR
    job = (
        await db_session.execute(select(EmbeddingJob).where(EmbeddingJob.document_id == stuck.id))
    ).scalar_one()
    assert job.status == JobStatus.FAILED
    assert job.error == RESTART_RECOVERY_ERROR
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
    (and their terminal jobs) pass through a restart unchanged."""
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="steady")
    healthy = await upload(client, token, collection["id"], "fine.md", MARKDOWN)
    assert healthy["status"] == "embedded"
    failed = await upload(client, token, collection["id"], "broken.pdf", b"%PDF-1.4 garbage")
    assert failed["status"] == "failed"

    recovered = await recover_stuck_ingestions(db_session)
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
    assert "restart" not in (by_name["broken.pdf"].error or "")


async def test_recovery_reports_count_and_is_idempotent(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="twice")
    await _seed_crashed_ingestion(db_session, collection["id"], "one.md")
    await _seed_crashed_ingestion(db_session, collection["id"], "two.md")

    assert await recover_stuck_ingestions(db_session) == 2
    # a second restart finds nothing left to recover
    assert await recover_stuck_ingestions(db_session) == 0
