"""Stuck-ingestion reaper (ADR-036).

An ingestion runs inside its upload request, so a controller crash strands its
document in `uploaded` and its job in `running`. Under several workers
(ADR-028) uvicorn respawns a single dead worker while the others keep
ingesting, so recovery must never touch an ingestion some worker is still
running. Every worker renews a lease (`heartbeat_at`) on the embedding jobs it
is running; the leader fails `running` jobs whose lease lapsed, and their
`uploaded` documents.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, enforce_ingest_lease
from app.core.leader import LeaderGate, set_leader_gate
from app.db.session import set_sessionmaker_override
from app.main import _ingest_lease_tick, app
from app.models import Document, DocumentStatus, EmbeddingJob, JobStatus
from app.services.knowledge import ingestion
from app.services.knowledge.ingestion import (
    ORPHANED_INGEST_ERROR,
    heartbeat_live_ingestions,
    reap_stale_ingestions,
)
from tests.test_chunking_hashing import MARKDOWN, make_collection

LONG_AGO = timedelta(hours=1)
JUST_NOW = timedelta(seconds=5)


async def seed_ingestion(
    db: AsyncSession,
    collection_id: uuid.UUID,
    filename: str = "interrupted.md",
    *,
    beat_age: timedelta | None = LONG_AGO,
    started_age: timedelta = LONG_AGO,
    document_status: DocumentStatus = DocumentStatus.UPLOADED,
    job_status: JobStatus = JobStatus.RUNNING,
) -> tuple[Document, EmbeddingJob]:
    """The rows ingest_document commits before its pipeline runs, as a worker
    left them: `beat_age` is how long since the job's lease was last renewed
    (None = a row from before the lease existed)."""
    now = datetime.now(UTC)
    document = Document(
        collection_id=collection_id,
        filename=filename,
        content_type="text/markdown",
        size_bytes=len(MARKDOWN),
        sha256="0" * 64,
        status=document_status,
    )
    db.add(document)
    await db.flush()
    job = EmbeddingJob(document_id=document.id, status=job_status, started_at=now - started_age)
    db.add(job)
    await db.commit()
    beat = None if beat_age is None else now - beat_age
    await db.execute(
        update(EmbeddingJob).where(EmbeddingJob.id == job.id).values(heartbeat_at=beat)
    )
    await db.commit()
    return document, job


async def _states(sessionmaker_: async_sessionmaker, document_id) -> tuple[Document, EmbeddingJob]:
    async with sessionmaker_() as db:
        document = (
            await db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
        job = (
            await db.execute(select(EmbeddingJob).where(EmbeddingJob.document_id == document_id))
        ).scalar_one()
        return document, job


@pytest.fixture
def _runtime_sessions(sessionmaker_: async_sessionmaker):
    set_sessionmaker_override(sessionmaker_)
    yield
    set_sessionmaker_override(None)
    set_leader_gate(None)


async def test_a_respawned_worker_never_fails_a_live_ingestion(
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    _runtime_sessions,
) -> None:
    """uvicorn respawns a dead worker B long after boot, while worker A is
    mid-ingest. B's startup must not fail A's document."""
    collection = await make_collection(db_session)
    pipeline_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_pipeline(document, collection, filename, data) -> int:
        pipeline_started.set()
        await release.wait()  # A is embedding a large upload
        return 3

    monkeypatch.setattr(ingestion, "_extract_embed_store", slow_pipeline)

    async def worker_a_upload() -> Document:
        async with sessionmaker_() as session:
            return await ingestion.ingest_document(
                session, collection, "big.md", "text/markdown", MARKDOWN
            )

    upload = asyncio.create_task(worker_a_upload())
    await asyncio.wait_for(pipeline_started.wait(), timeout=5)
    (document_id,) = (await db_session.execute(select(Document.id))).scalars().all()

    # A took the startup recovery lock when the stack booted; it has long expired
    server = fakeredis.FakeServer()
    redis_a = fakeredis.FakeAsyncRedis(server=server)
    assert await LeaderGate(redis_a, instance_id="worker-a").try_lead(
        "ingest-recovery", ttl_seconds=600
    )
    await redis_a.delete("lycosa:leader:ingest-recovery")

    # -- worker B is respawned: the app's real startup runs ------------------
    set_leader_gate(LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-b"))
    async with app.router.lifespan_context(app):
        pass

    document, job = await _states(sessionmaker_, document_id)
    assert document.status == DocumentStatus.UPLOADED
    assert document.error is None
    assert job.status == JobStatus.RUNNING

    # -- A finishes its ingestion, and nothing B did is left on the rows -----
    release.set()
    finished = await asyncio.wait_for(upload, timeout=5)
    assert finished.status == DocumentStatus.EMBEDDED
    document, job = await _states(sessionmaker_, document_id)
    assert document.status == DocumentStatus.EMBEDDED
    assert document.error is None
    assert job.status == JobStatus.SUCCEEDED
    assert job.error is None


async def test_reaps_an_ingestion_orphaned_by_a_crash(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    collection = await make_collection(db_session)
    document, _ = await seed_ingestion(db_session, collection.id)

    assert await reap_stale_ingestions(db_session) == 1

    document, job = await _states(sessionmaker_, document.id)
    assert document.status == DocumentStatus.FAILED
    assert document.error == ORPHANED_INGEST_ERROR
    assert "re-upload" in document.error
    assert job.status == JobStatus.FAILED
    assert job.error == ORPHANED_INGEST_ERROR
    assert job.finished_at is not None

    assert await reap_stale_ingestions(db_session) == 0  # idempotent


async def test_an_ingestion_with_a_live_lease_is_never_reaped(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    # started an hour ago, but its worker renewed the lease seconds ago
    collection = await make_collection(db_session)
    document, _ = await seed_ingestion(db_session, collection.id, beat_age=JUST_NOW)

    assert await reap_stale_ingestions(db_session) == 0
    document, job = await _states(sessionmaker_, document.id)
    assert document.status == DocumentStatus.UPLOADED
    assert job.status == JobStatus.RUNNING


async def test_a_heartbeat_landing_mid_reap_keeps_the_ingestion(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim re-checks the lease, so a worker that renews it right after
    the reaper picked its job as a candidate keeps the ingestion."""
    collection = await make_collection(db_session)
    document, job = await seed_ingestion(db_session, collection.id)
    real_execute = db_session.execute
    renewed: list[bool] = []

    async def execute(statement, *args, **kwargs):
        result = await real_execute(statement, *args, **kwargs)
        if not renewed and getattr(statement, "is_select", False):
            renewed.append(True)  # the ingesting worker's tick, just in time
            await real_execute(
                update(EmbeddingJob)
                .where(EmbeddingJob.id == job.id)
                .values(heartbeat_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
        return result

    monkeypatch.setattr(db_session, "execute", execute)
    assert await reap_stale_ingestions(db_session) == 0
    assert renewed == [True]
    await db_session.commit()
    document, job = await _states(sessionmaker_, document.id)
    assert document.status == DocumentStatus.UPLOADED
    assert job.status == JobStatus.RUNNING


async def test_rows_from_before_the_lease_fall_back_to_started_at(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    collection = await make_collection(db_session)
    legacy, _ = await seed_ingestion(db_session, collection.id, "old.md", beat_age=None)
    young, _ = await seed_ingestion(
        db_session, collection.id, "new.md", beat_age=None, started_age=JUST_NOW
    )

    assert await reap_stale_ingestions(db_session) == 1
    assert (await _states(sessionmaker_, legacy.id))[0].status == DocumentStatus.FAILED
    assert (await _states(sessionmaker_, young.id))[0].status == DocumentStatus.UPLOADED


async def test_heartbeat_renews_only_the_ingestions_this_worker_runs(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    collection = await make_collection(db_session)
    mine, mine_job = await seed_ingestion(db_session, collection.id, "mine.md")
    orphan, _ = await seed_ingestion(db_session, collection.id, "orphan.md")

    with ingestion._ingesting(mine_job.id):
        assert await heartbeat_live_ingestions(db_session) == 1
        assert await reap_stale_ingestions(db_session) == 1

    assert (await _states(sessionmaker_, mine.id))[1].status == JobStatus.RUNNING
    assert (await _states(sessionmaker_, orphan.id))[1].status == JobStatus.FAILED


async def test_an_ingestion_holds_its_lease_only_while_running(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = await make_collection(db_session)
    held: list[set] = []

    async def pipeline(document, collection, filename, data) -> int:
        held.append(set(ingestion._live_jobs))
        return 2

    monkeypatch.setattr(ingestion, "_extract_embed_store", pipeline)
    document = await ingestion.ingest_document(
        db_session, collection, "ok.md", "text/markdown", MARKDOWN
    )
    assert document.status == DocumentStatus.EMBEDDED
    _, job = await _states(sessionmaker_, document.id)
    assert held == [{job.id}]
    assert not ingestion._live_jobs

    # an unhandled error after the pipeline (the terminal commit fails) still
    # drops the lease, so the reaper closes the rows it left behind
    async with sessionmaker_() as session:
        real_commit = session.commit

        async def commit() -> None:
            if len(held) == 2:
                raise RuntimeError("database went away")
            await real_commit()

        monkeypatch.setattr(session, "commit", commit)
        with pytest.raises(RuntimeError):
            await ingestion.ingest_document(
                session, collection, "lost.md", "text/markdown", MARKDOWN
            )
    assert len(held) == 2
    assert not ingestion._live_jobs
    lost = (
        await db_session.execute(select(Document).where(Document.filename == "lost.md"))
    ).scalar_one()
    await db_session.execute(
        update(EmbeddingJob)
        .where(EmbeddingJob.document_id == lost.id)
        .values(heartbeat_at=datetime.now(UTC) - LONG_AGO)
    )
    await db_session.commit()
    assert await reap_stale_ingestions(db_session) == 1
    assert (await _states(sessionmaker_, lost.id))[0].status == DocumentStatus.FAILED


async def test_every_worker_renews_but_only_the_leader_reaps(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, _runtime_sessions
) -> None:
    collection = await make_collection(db_session)
    orphan, _ = await seed_ingestion(db_session, collection.id, "orphan.md")
    mine, mine_job = await seed_ingestion(db_session, collection.id, "mine.md")
    server = fakeredis.FakeServer()
    leader = LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-a")
    follower = LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-b")
    assert await leader.try_lead("ingest-reaper", ttl_seconds=60)

    with ingestion._ingesting(mine_job.id):  # this worker is ingesting `mine`
        set_leader_gate(follower)
        await _ingest_lease_tick()
    assert (await _states(sessionmaker_, orphan.id))[0].status == DocumentStatus.UPLOADED

    set_leader_gate(leader)
    await _ingest_lease_tick()

    assert (await _states(sessionmaker_, orphan.id))[0].status == DocumentStatus.FAILED
    # still uploaded only because the follower's tick renewed its lease
    assert (await _states(sessionmaker_, mine.id))[0].status == DocumentStatus.UPLOADED


def test_stale_threshold_must_cover_three_missed_heartbeats() -> None:
    with pytest.raises(RuntimeError, match="INGEST_STALE_AFTER_SECONDS"):
        enforce_ingest_lease(
            Settings(ingest_heartbeat_interval_seconds=15, ingest_stale_after_seconds=30)
        )
    enforce_ingest_lease(Settings())  # defaults are consistent
