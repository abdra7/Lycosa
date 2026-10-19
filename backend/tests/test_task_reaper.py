"""Stale task reaper (ADR-035).

A task executes inside the request that submitted it (ADR-012), so a
controller crash or restart strands it in `queued` (mid-retrieval or routing)
or `running` (mid-dispatch) forever, with its last dispatch attempt still
`running`. The ADR-034 lease applies: every worker renews `heartbeat_at` on
the tasks it is executing, and the leader fails unfinished tasks whose lease
lapsed. A task that another worker is still executing keeps a fresh lease,
however long it takes, and is never touched.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import fakeredis
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.leader import LeaderGate, set_leader_gate
from app.core.metrics import TASK_DURATION, TASKS_TOTAL
from app.db.session import set_sessionmaker_override
from app.main import _lease_tick
from app.models import AuditLog, ExecutionStatus, Task, TaskExecution, TaskStatus, TaskType
from app.schemas.task import TaskCreate
from app.services import orchestrator
from app.services.orchestrator import (
    GROUNDED_REFUSAL,
    STALE_TASK_ERROR,
    heartbeat_live_tasks,
    reap_stale_tasks,
    submit_task,
)
from tests.conftest import ADMIN_EMAIL, bearer, login, make_node

LONG_AGO = timedelta(hours=1)
JUST_NOW = timedelta(seconds=5)


async def _task(
    db: AsyncSession,
    *,
    status: TaskStatus = TaskStatus.RUNNING,
    beat_age: timedelta | None = LONG_AGO,
    queued_age: timedelta = LONG_AGO,
    executions: tuple[ExecutionStatus, ...] = (),
) -> Task:
    """A task as a worker left it: `beat_age` is how long since its lease was
    last renewed (None = a row from before the lease existed); `executions`
    are its dispatch attempts, in order."""
    now = datetime.now(UTC)
    node = await make_node(db, f"node-{now.timestamp()}") if executions else None
    task = Task(
        type=TaskType.GENERAL,
        payload={"prompt": "x"},
        status=status,
        node_id=node.id if node else None,
        queued_at=now - queued_age,
        started_at=now - queued_age if executions else None,
    )
    db.add(task)
    await db.flush()
    for attempt, execution_status in enumerate(executions, start=1):
        running = execution_status == ExecutionStatus.RUNNING
        db.add(
            TaskExecution(
                task_id=task.id,
                node_id=node.id,
                attempt=attempt,
                status=execution_status,
                error=None if running else "connection refused",
                started_at=now - queued_age,
                finished_at=None if running else now - queued_age,
            )
        )
    await db.commit()
    beat = None if beat_age is None else now - beat_age
    await db.execute(update(Task).where(Task.id == task.id).values(heartbeat_at=beat))
    await db.commit()
    return task


async def _reload(sessionmaker_: async_sessionmaker, task_id) -> Task:
    async with sessionmaker_() as db:
        return (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()


async def _audit(db: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
    )


async def test_reaps_a_task_orphaned_mid_dispatch(
    client: AsyncClient,
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker,
    users: dict,
) -> None:
    # attempt 1 failed over to attempt 2, then the controller died
    task = await _task(db_session, executions=(ExecutionStatus.FAILED, ExecutionStatus.RUNNING))
    failed_before = TASKS_TOTAL.labels("general", "failed")._value.get()
    duration_before = TASK_DURATION.labels("general")._sum.get()

    assert await reap_stale_tasks(db_session) == 1

    reaped = await _reload(sessionmaker_, task.id)
    assert reaped.status == TaskStatus.FAILED
    assert reaped.error == STALE_TASK_ERROR
    assert "crash or restart" in reaped.error
    assert reaped.finished_at is not None
    first, second = reaped.executions
    assert (first.status, first.error) == (ExecutionStatus.FAILED, "connection refused")
    assert second.status == ExecutionStatus.FAILED
    assert second.error == STALE_TASK_ERROR
    assert second.finished_at is not None

    (entry,) = await _audit(db_session, "task.reaped")
    assert entry.resource_id == str(task.id)
    assert entry.detail["status"] == "running"
    assert entry.detail["node_id"] == str(task.node_id)
    assert entry.detail["stale_after_seconds"] == 120
    (finished,) = await _audit(db_session, "task.finished")
    assert finished.detail == {"status": "failed"}
    assert TASKS_TOTAL.labels("general", "failed")._value.get() == failed_before + 1
    # the hour it sat orphaned is downtime, not task wall time
    assert TASK_DURATION.labels("general")._sum.get() == duration_before

    token = await login(client, ADMIN_EMAIL)
    shown = (await client.get(f"/api/v1/tasks/{task.id}", headers=bearer(token))).json()
    assert shown["status"] == "failed"
    assert shown["error"] == STALE_TASK_ERROR
    assert [e["status"] for e in shown["executions"]] == ["failed", "failed"]

    assert await reap_stale_tasks(db_session) == 0  # idempotent


@pytest.mark.parametrize("status", [TaskStatus.QUEUED, TaskStatus.ASSIGNED])
async def test_a_task_stranded_before_dispatch_is_reaped(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, status: TaskStatus
) -> None:
    # e.g. the crash hit during knowledge retrieval or routing
    task = await _task(db_session, status=status)

    assert await reap_stale_tasks(db_session) == 1
    reaped = await _reload(sessionmaker_, task.id)
    assert reaped.status == TaskStatus.FAILED
    assert reaped.error == STALE_TASK_ERROR
    (entry,) = await _audit(db_session, "task.reaped")
    assert entry.detail["status"] == status.value
    assert entry.detail["node_id"] is None


async def test_a_task_with_a_live_lease_is_never_reaped(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    # submitted an hour ago, but some worker renewed its lease seconds ago
    task = await _task(db_session, beat_age=JUST_NOW, executions=(ExecutionStatus.RUNNING,))

    assert await reap_stale_tasks(db_session) == 0
    kept = await _reload(sessionmaker_, task.id)
    assert kept.status == TaskStatus.RUNNING
    assert kept.executions[0].status == ExecutionStatus.RUNNING


async def test_a_heartbeat_landing_mid_reap_keeps_the_task(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim re-checks the lease, so a worker that renews it right after
    the reaper picked its task as a candidate keeps the task."""
    task = await _task(db_session)
    real_execute = db_session.execute
    renewed: list[bool] = []

    async def execute(statement, *args, **kwargs):
        result = await real_execute(statement, *args, **kwargs)
        if not renewed and getattr(statement, "is_select", False):
            renewed.append(True)  # the executing worker's tick, just in time
            await real_execute(
                update(Task)
                .where(Task.id == task.id)
                .values(heartbeat_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
        return result

    monkeypatch.setattr(db_session, "execute", execute)
    assert await reap_stale_tasks(db_session) == 0
    assert renewed == [True]
    await db_session.commit()
    assert (await _reload(sessionmaker_, task.id)).status == TaskStatus.RUNNING


async def test_finished_tasks_are_left_alone(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    done = await _task(db_session, status=TaskStatus.SUCCEEDED)
    failed = await _task(db_session, status=TaskStatus.FAILED)

    assert await reap_stale_tasks(db_session) == 0
    assert (await _reload(sessionmaker_, done.id)).status == TaskStatus.SUCCEEDED
    assert (await _reload(sessionmaker_, failed.id)).error is None


async def test_rows_from_before_the_lease_fall_back_to_queued_at(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    legacy = await _task(db_session, beat_age=None)
    young = await _task(db_session, beat_age=None, queued_age=JUST_NOW)

    assert await reap_stale_tasks(db_session) == 1
    assert (await _reload(sessionmaker_, legacy.id)).status == TaskStatus.FAILED
    assert (await _reload(sessionmaker_, young.id)).status == TaskStatus.RUNNING


async def test_heartbeat_renews_only_the_unfinished_tasks_this_worker_executes(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker
) -> None:
    mine = await _task(db_session)
    finishing = await _task(db_session, status=TaskStatus.SUCCEEDED)
    orphan = await _task(db_session)

    with orchestrator._executing(mine.id), orchestrator._executing(finishing.id):
        assert await heartbeat_live_tasks(db_session) == 1
        assert await reap_stale_tasks(db_session) == 1

    assert (await _reload(sessionmaker_, mine.id)).status == TaskStatus.RUNNING
    assert (await _reload(sessionmaker_, orphan.id)).status == TaskStatus.FAILED


async def test_a_task_holds_its_lease_only_while_executing(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[set] = []

    async def no_knowledge(*args, **kwargs):
        held.append(set(orchestrator._live_tasks))
        return SimpleNamespace(chunks=[], context_text="")

    monkeypatch.setattr(orchestrator, "retrieve", no_knowledge)
    task = await submit_task(db_session, TaskCreate(prompt="q", knowledge_query="q"), None, None)
    assert task.result["output"] == GROUNDED_REFUSAL
    assert held == [{task.id}]
    assert task.id not in orchestrator._live_tasks

    async def routing_crashes(*args, **kwargs):
        raise RuntimeError("bug mid-dispatch")

    monkeypatch.setattr(orchestrator, "route_candidates", routing_crashes)
    with pytest.raises(RuntimeError):
        await submit_task(db_session, TaskCreate(prompt="hello"), None, None)
    # the lease is dropped, so the reaper will close the task this left behind
    assert not orchestrator._live_tasks
    async with sessionmaker_() as db:
        await db.execute(
            update(Task)
            .where(Task.status == TaskStatus.QUEUED)
            .values(heartbeat_at=datetime.now(UTC) - LONG_AGO)
        )
        await db.commit()
        assert await reap_stale_tasks(db) == 1


@pytest.fixture
def _runtime_sessions(sessionmaker_: async_sessionmaker):
    set_sessionmaker_override(sessionmaker_)
    yield
    set_sessionmaker_override(None)
    set_leader_gate(None)


async def test_every_worker_renews_but_only_the_leader_reaps(
    db_session: AsyncSession, sessionmaker_: async_sessionmaker, _runtime_sessions
) -> None:
    orphan = await _task(db_session)
    mine = await _task(db_session)
    server = fakeredis.FakeServer()
    leader = LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-a")
    follower = LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-b")
    assert await leader.try_lead("lease-reaper", ttl_seconds=60)

    with orchestrator._executing(mine.id):  # this worker executes `mine`
        set_leader_gate(follower)
        await _lease_tick()
    assert (await _reload(sessionmaker_, orphan.id)).status == TaskStatus.RUNNING

    set_leader_gate(leader)
    await _lease_tick()

    assert (await _reload(sessionmaker_, orphan.id)).status == TaskStatus.FAILED
    # still running only because the follower's tick renewed its lease
    assert (await _reload(sessionmaker_, mine.id)).status == TaskStatus.RUNNING
