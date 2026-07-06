import asyncio
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbDep, Principal, require_roles
from app.db.session import get_runtime_sessionmaker
from app.models import Task, TaskStatus
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.task import TaskCreate, TaskOut
from app.services.orchestrator import submit_task

logger = logging.getLogger("lycosa.tasks")

router = APIRouter(prefix="/tasks", tags=["tasks"])

OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, principal: OperatorDep) -> TaskOut:
    """Submit a task. v1 runs it synchronously (ADR-012): the response is the
    finished task — succeeded with a result, or failed with the reason and
    the full per-node attempt trace.

    Dispatch runs shielded, on its own DB session: if the caller times out or
    disconnects mid-run, the task still finishes and its terminal state is
    recorded (Ticket #102) — it stays visible in history instead of hanging
    in 'running' forever."""
    user_id = principal.id if principal.type == "user" else None
    api_key_id = principal.id if principal.type == "api_key" else None

    async def _run() -> Task:
        async with get_runtime_sessionmaker()() as session:
            return await submit_task(
                session, body, created_by_user_id=user_id, created_by_api_key_id=api_key_id
            )

    dispatch = asyncio.create_task(_run())
    dispatch.add_done_callback(_log_orphaned_dispatch)
    task = await asyncio.shield(dispatch)
    return TaskOut.model_validate(task)


def _log_orphaned_dispatch(dispatch: "asyncio.Task[Task]") -> None:
    """A dispatch that outlives its request has nobody awaiting it; surface
    unexpected crashes in the log instead of a silent 'never retrieved'."""
    if not dispatch.cancelled() and dispatch.exception() is not None:
        logger.error("task dispatch failed after client disconnect", exc_info=dispatch.exception())


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    db: DbDep,
    _principal: OperatorDep,
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TaskOut]:
    query = select(Task).order_by(Task.queued_at.desc()).limit(limit)
    if status_filter is not None:
        query = query.where(Task.status == status_filter)
    tasks = (await db.execute(query)).scalars().all()
    return [TaskOut.model_validate(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, db: DbDep, _principal: OperatorDep) -> TaskOut:
    """Task detail including the execution trace (one entry per attempt)."""
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskOut.model_validate(task)
