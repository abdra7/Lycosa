"""Stale workflow run reaper (ADR-034).

Runs execute inside the request that started (or approved) them, so a
controller crash or restart strands them in `running` forever: the status is
wrong, and since ADR-033 the run blocks deleting its workflow. Every worker
renews a lease (`heartbeat_at`) on the runs it is executing; the leader fails
`running` runs whose lease lapsed. A run that another worker is still
executing keeps a fresh lease, however long it has been running, and is never
touched.
"""

import uuid
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, enforce_workflow_lease
from app.core.leader import LeaderGate, set_leader_gate
from app.core.metrics import WORKFLOW_RUNS, WORKFLOW_STEPS
from app.db.session import set_sessionmaker_override
from app.main import _lease_tick
from app.models import AuditLog, RunStatus, StepRunStatus, Workflow, WorkflowRun, WorkflowStepRun
from app.services import workflow as workflow_service
from app.services.workflow import (
    STALE_RUN_ERROR,
    heartbeat_live_runs,
    reap_stale_runs,
    resume_run,
    start_run,
)
from tests.conftest import ADMIN_EMAIL, bearer, login
from tests.test_workflow_approval import STEPS
from tests.test_workflow_run import create_workflow

LONG_AGO = timedelta(hours=1)
JUST_NOW = timedelta(seconds=5)
ONE_STEP = [{"id": "build", "kind": "task", "prompt": "Build: {{input}}"}]


async def _workflow(db: AsyncSession, name: str = "wf", steps: list[dict] = ONE_STEP) -> Workflow:
    workflow = Workflow(name=name, definition={"steps": steps})
    db.add(workflow)
    await db.commit()
    return workflow


async def _run(
    db: AsyncSession,
    workflow: Workflow,
    *,
    status: RunStatus = RunStatus.RUNNING,
    beat_age: timedelta | None = LONG_AGO,
    started_age: timedelta = LONG_AGO,
    step_status: StepRunStatus = StepRunStatus.RUNNING,
) -> WorkflowRun:
    """A run as a worker left it: `beat_age` is how long since its lease was
    last renewed (None = a row from before the lease existed)."""
    now = datetime.now(UTC)
    run = WorkflowRun(
        workflow_id=workflow.id,
        input="x",
        context={"steps": {}},
        status=status,
        current_step="build",
        started_at=now - started_age,
    )
    db.add(run)
    await db.flush()
    db.add(
        WorkflowStepRun(
            run_id=run.id,
            step_id="build",
            kind="task",
            status=step_status,
            started_at=now - started_age,
        )
    )
    await db.commit()
    beat = None if beat_age is None else now - beat_age
    await db.execute(update(WorkflowRun).where(WorkflowRun.id == run.id).values(heartbeat_at=beat))
    await db.commit()
    return run


async def _reload(sessionmaker_: async_sessionmaker, run_id) -> WorkflowRun:
    async with sessionmaker_() as db:
        return (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()


async def _audit(db: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
    )


async def test_reaps_a_run_orphaned_by_a_crash(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    run = await _run(db_session, await _workflow(db_session))
    runs_failed = WORKFLOW_RUNS.labels("failed")._value.get()
    steps_failed = WORKFLOW_STEPS.labels("task", "failed")._value.get()

    assert await reap_stale_runs(db_session) == 1

    reaped = await _reload(sessionmaker_, run.id)
    assert reaped.status == RunStatus.FAILED
    assert reaped.error == STALE_RUN_ERROR
    assert "crash or restart" in reaped.error
    assert reaped.finished_at is not None
    (step,) = reaped.step_runs
    assert step.status == StepRunStatus.FAILED
    assert step.error == STALE_RUN_ERROR
    assert step.finished_at is not None

    (entry,) = await _audit(db_session, "workflow.run.reaped")
    assert entry.resource_id == str(run.id)
    assert entry.detail["step"] == "build"
    assert entry.detail["stale_after_seconds"] == 120
    (finished,) = await _audit(db_session, "workflow.run.finished")
    assert finished.detail == {"status": "failed"}
    assert WORKFLOW_RUNS.labels("failed")._value.get() == runs_failed + 1
    assert WORKFLOW_STEPS.labels("task", "failed")._value.get() == steps_failed + 1

    assert await reap_stale_runs(db_session) == 0  # idempotent


async def test_a_run_with_a_live_lease_is_never_reaped(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    # running for an hour, but some worker renewed its lease seconds ago
    run = await _run(db_session, await _workflow(db_session), beat_age=JUST_NOW)

    assert await reap_stale_runs(db_session) == 0
    kept = await _reload(sessionmaker_, run.id)
    assert kept.status == RunStatus.RUNNING
    assert kept.step_runs[0].status == StepRunStatus.RUNNING


async def test_a_heartbeat_landing_mid_reap_keeps_the_run(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim re-checks the lease, so a worker that renews it right after
    the reaper picked its run as a candidate keeps the run."""
    run = await _run(db_session, await _workflow(db_session))
    real_execute = db_session.execute
    renewed: list[bool] = []

    async def execute(statement, *args, **kwargs):
        result = await real_execute(statement, *args, **kwargs)
        if not renewed and getattr(statement, "is_select", False):
            renewed.append(True)  # the executing worker's tick, just in time
            await real_execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run.id)
                .values(heartbeat_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
        return result

    monkeypatch.setattr(db_session, "execute", execute)
    assert await reap_stale_runs(db_session) == 0
    assert renewed == [True]
    await db_session.commit()
    assert (await _reload(sessionmaker_, run.id)).status == RunStatus.RUNNING


async def test_paused_and_finished_runs_are_left_alone(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    workflow = await _workflow(db_session)
    paused = await _run(
        db_session, workflow, status=RunStatus.PAUSED, step_status=StepRunStatus.PENDING_APPROVAL
    )
    done = await _run(
        db_session, workflow, status=RunStatus.SUCCEEDED, step_status=StepRunStatus.SUCCEEDED
    )
    failed = await _run(
        db_session, workflow, status=RunStatus.FAILED, step_status=StepRunStatus.FAILED
    )

    assert await reap_stale_runs(db_session) == 0
    assert (await _reload(sessionmaker_, paused.id)).status == RunStatus.PAUSED
    assert (await _reload(sessionmaker_, done.id)).status == RunStatus.SUCCEEDED
    assert (await _reload(sessionmaker_, failed.id)).error is None


async def test_rows_from_before_the_lease_fall_back_to_started_at(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    workflow = await _workflow(db_session)
    legacy = await _run(db_session, workflow, beat_age=None)
    young = await _run(db_session, workflow, beat_age=None, started_age=JUST_NOW)

    assert await reap_stale_runs(db_session) == 1
    assert (await _reload(sessionmaker_, legacy.id)).status == RunStatus.FAILED
    assert (await _reload(sessionmaker_, young.id)).status == RunStatus.RUNNING


async def test_heartbeat_renews_only_the_runs_this_worker_executes(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    workflow = await _workflow(db_session)
    mine = await _run(db_session, workflow)
    orphan = await _run(db_session, workflow)

    with workflow_service._executing(mine.id):
        assert await heartbeat_live_runs(db_session) == 1
        assert await reap_stale_runs(db_session) == 1

    assert (await _reload(sessionmaker_, mine.id)).status == RunStatus.RUNNING
    assert (await _reload(sessionmaker_, orphan.id)).status == RunStatus.FAILED


async def test_a_run_holds_its_lease_only_while_executing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = await _workflow(db_session)
    held: list[bool] = []

    async def step_ok(db, step, run, user_id, api_key_id):
        held.append(run.id in workflow_service._live_runs)
        return "built", None, None

    monkeypatch.setattr(workflow_service, "_run_task", step_ok)
    run = await start_run(db_session, workflow, "the thing", None, None)
    assert run.status == RunStatus.SUCCEEDED
    assert held == [True]
    assert run.id not in workflow_service._live_runs

    async def step_crashes(db, step, run, user_id, api_key_id):
        raise RuntimeError("bug mid-step")

    monkeypatch.setattr(workflow_service, "_run_task", step_crashes)
    with pytest.raises(RuntimeError):
        await start_run(db_session, workflow, "again", None, None)
    # the lease is dropped, so the reaper will close the run this left behind
    assert not workflow_service._live_runs


async def test_approving_a_long_paused_run_renews_its_lease(
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = await _workflow(db_session, "gated", STEPS)
    reaped_mid_step: list[int] = []

    async def step(db, step, run, user_id, api_key_id):
        if step.id == "publish":
            async with sessionmaker_() as reaper_db:  # the leader, on another worker
                reaped_mid_step.append(await reap_stale_runs(reaper_db))
        return f"{step.id} done", None, None

    monkeypatch.setattr(workflow_service, "_run_task", step)
    run = await start_run(db_session, workflow, "release notes", None, None)
    assert run.status == RunStatus.PAUSED

    # nobody decided for an hour
    old = datetime.now(UTC) - LONG_AGO
    await db_session.execute(
        update(WorkflowRun).where(WorkflowRun.id == run.id).values(started_at=old, heartbeat_at=old)
    )
    await db_session.commit()
    async with sessionmaker_() as request_db:  # the approve request's session
        run = (
            await request_db.execute(select(WorkflowRun).where(WorkflowRun.id == run.id))
        ).scalar_one()
        resumed = await resume_run(request_db, run, workflow.definition, True, None)

    assert reaped_mid_step == [0]
    assert resumed.status == RunStatus.SUCCEEDED


async def test_a_reaped_orphan_no_longer_blocks_deleting_its_workflow(
    client: AsyncClient,
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker,
    users: dict,
) -> None:
    token = await login(client, ADMIN_EMAIL)
    created = await create_workflow(client, token, "crashed", ONE_STEP)
    workflow = await db_session.get(Workflow, uuid.UUID(created["id"]))
    await _run(db_session, workflow)
    path = f"/api/v1/workflows/{created['id']}"

    assert (await client.delete(path, headers=bearer(token))).status_code == 409

    async with sessionmaker_() as reaper_db:
        assert await reap_stale_runs(reaper_db) == 1
    response = await client.delete(path, headers=bearer(token))
    assert response.status_code == 204, response.text


@pytest.fixture
def _runtime_sessions(sessionmaker_: async_sessionmaker):
    set_sessionmaker_override(sessionmaker_)
    yield
    set_sessionmaker_override(None)
    set_leader_gate(None)


async def test_every_worker_renews_but_only_the_leader_reaps(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, _runtime_sessions
) -> None:
    workflow = await _workflow(db_session)
    orphan = await _run(db_session, workflow)
    mine = await _run(db_session, workflow)
    server = fakeredis.FakeServer()
    leader = LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-a")
    follower = LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-b")
    assert await leader.try_lead("lease-reaper", ttl_seconds=60)

    with workflow_service._executing(mine.id):  # this worker executes `mine`
        set_leader_gate(follower)
        await _lease_tick()
    assert (await _reload(sessionmaker_, orphan.id)).status == RunStatus.RUNNING

    set_leader_gate(leader)
    await _lease_tick()

    assert (await _reload(sessionmaker_, orphan.id)).status == RunStatus.FAILED
    # still running only because the follower's tick renewed its lease
    assert (await _reload(sessionmaker_, mine.id)).status == RunStatus.RUNNING


def test_stale_threshold_must_cover_three_missed_heartbeats() -> None:
    with pytest.raises(RuntimeError, match="WORKFLOW_STALE_AFTER_SECONDS"):
        enforce_workflow_lease(
            Settings(workflow_heartbeat_interval_seconds=15, workflow_stale_after_seconds=30)
        )
    enforce_workflow_lease(Settings())  # defaults are consistent
