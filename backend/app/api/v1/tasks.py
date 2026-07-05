import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbDep, Principal, require_roles
from app.models import Task, TaskStatus
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.task import TaskCreate, TaskOut
from app.services.orchestrator import submit_task

router = APIRouter(prefix="/tasks", tags=["tasks"])

OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, principal: OperatorDep, db: DbDep) -> TaskOut:
    """Submit a task. v1 runs it synchronously (ADR-012): the response is the
    finished task — succeeded with a result, or failed with the reason and
    the full per-node attempt trace."""
    task = await submit_task(
        db,
        body,
        created_by_user_id=principal.id if principal.type == "user" else None,
        created_by_api_key_id=principal.id if principal.type == "api_key" else None,
    )
    return TaskOut.model_validate(task)


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
