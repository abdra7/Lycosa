"""CLI sessions reuse the task dispatcher and its durable execution leases."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, Principal, require_roles
from app.api.v1.tasks import _log_orphaned_dispatch
from app.db.session import get_runtime_sessionmaker
from app.models import CliSession, CliWorkspace, Node, Task, TaskStatus
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.cli import (
    CliSettings,
    SessionCreate,
    SessionOut,
    SessionPatch,
    SessionPrompt,
    WorkspaceCreate,
    WorkspaceOut,
)
from app.schemas.task import TaskCreate, TaskOut
from app.services.audit import audit
from app.services.orchestrator import submit_task

router = APIRouter(prefix="/cli", tags=["cli"])
OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]
_UNFINISHED = (TaskStatus.QUEUED, TaskStatus.ASSIGNED, TaskStatus.RUNNING)


def _owned_workspaces(principal: Principal):
    return select(CliWorkspace.id).where(
        CliWorkspace.owner_type == principal.type, CliWorkspace.owner_id == principal.id
    )


async def _workspace(db: AsyncSession, workspace_id: uuid.UUID, principal: Principal):
    workspace = await db.scalar(
        select(CliWorkspace).where(
            CliWorkspace.id == workspace_id,
            CliWorkspace.id.in_(_owned_workspaces(principal)),
        )
    )
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace


async def _locked_session(
    db: AsyncSession, session_id: uuid.UUID, principal: Principal
) -> CliSession:
    # UPDATE takes a row lock on PostgreSQL and a write lock on SQLite. Keep
    # it until the first linked Task is committed, closing the check/submit
    # race across workers without an in-memory mutex or a stale busy flag.
    result = await db.execute(
        update(CliSession)
        .where(
            CliSession.id == session_id,
            CliSession.workspace_id.in_(_owned_workspaces(principal)),
        )
        .values(updated_at=func.now())
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Session not found")
    session = await db.get(CliSession, session_id)
    assert session is not None
    return session


async def _ensure_idle(db: AsyncSession, session_id: uuid.UUID) -> None:
    running = await db.scalar(
        select(Task.id)
        .where(Task.cli_session_id == session_id, Task.status.in_(_UNFINISHED))
        .limit(1)
    )
    if running is not None:
        raise HTTPException(409, "This session is running. Wait for its task to finish.")


async def _validate_target(
    db: AsyncSession, node_id: uuid.UUID | None, settings: CliSettings
) -> None:
    if node_id is not None:
        if settings.route is not None or settings.llm_account_id is not None:
            raise HTTPException(422, "Choose a node or an LLM route/account, not both.")
        if settings.provider not in {"ollama", "anthropic"}:
            raise HTTPException(
                422, "This provider runs on the controller; clear the selected node."
            )
        if await db.get(Node, node_id) is None:
            raise HTTPException(422, "The selected node no longer exists.")


def _session_out(session: CliSession, tasks: list[Task]) -> SessionOut:
    return SessionOut(
        id=session.id,
        workspace_id=session.workspace_id,
        name=session.name,
        archived=session.archived,
        node_id=session.node_id,
        settings=CliSettings.model_validate(session.settings),
        tasks=[TaskOut.model_validate(t) for t in tasks],
        created_at=session.created_at,
    )


async def _tasks(db: AsyncSession, session_id: uuid.UUID) -> list[Task]:
    return list(
        (
            await db.scalars(
                select(Task)
                .where(Task.cli_session_id == session_id)
                .order_by(Task.queued_at, Task.id)
            )
        ).all()
    )


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(db: DbDep, principal: OperatorDep) -> list[WorkspaceOut]:
    workspaces = await db.scalars(
        select(CliWorkspace)
        .where(CliWorkspace.id.in_(_owned_workspaces(principal)))
        .order_by(CliWorkspace.created_at, CliWorkspace.id)
    )
    return [WorkspaceOut.model_validate(w) for w in workspaces]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate, db: DbDep, principal: OperatorDep
) -> WorkspaceOut:
    workspace = CliWorkspace(
        name=body.name,
        normalized_name=body.name.casefold(),
        owner_type=principal.type,
        owner_id=principal.id,
    )
    db.add(workspace)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A workspace with this name already exists.") from None
    await audit(
        db,
        action="cli.workspace.create",
        **principal.audit_actor(),
        resource_type="cli_workspace",
        resource_id=str(workspace.id),
    )
    await db.commit()
    await db.refresh(workspace)
    return WorkspaceOut.model_validate(workspace)


@router.get("/workspaces/{workspace_id}/sessions", response_model=list[SessionOut])
async def list_sessions(
    workspace_id: uuid.UUID, db: DbDep, principal: OperatorDep
) -> list[SessionOut]:
    await _workspace(db, workspace_id, principal)
    sessions = list(
        (
            await db.scalars(
                select(CliSession)
                .where(CliSession.workspace_id == workspace_id)
                .order_by(CliSession.created_at, CliSession.id)
            )
        ).all()
    )
    if not sessions:
        return []
    grouped: dict[uuid.UUID, list[Task]] = {s.id: [] for s in sessions}
    tasks = await db.scalars(
        select(Task).where(Task.cli_session_id.in_(grouped)).order_by(Task.queued_at, Task.id)
    )
    for task in tasks:
        grouped[task.cli_session_id].append(task)
    return [_session_out(s, grouped[s.id]) for s in sessions]


@router.post(
    "/workspaces/{workspace_id}/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    workspace_id: uuid.UUID, body: SessionCreate, db: DbDep, principal: OperatorDep
) -> SessionOut:
    await _workspace(db, workspace_id, principal)
    await _validate_target(db, body.node_id, body.settings)
    session = CliSession(
        workspace_id=workspace_id,
        name=body.name,
        node_id=body.node_id,
        settings=body.settings.model_dump(mode="json"),
    )
    db.add(session)
    await db.flush()
    await audit(
        db,
        action="cli.session.create",
        **principal.audit_actor(),
        resource_type="cli_session",
        resource_id=str(session.id),
    )
    await db.commit()
    await db.refresh(session)
    return _session_out(session, [])


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def patch_session(
    session_id: uuid.UUID, body: SessionPatch, db: DbDep, principal: OperatorDep
) -> SessionOut:
    session = await _locked_session(db, session_id, principal)
    await _ensure_idle(db, session_id)
    node_id = body.node_id if "node_id" in body.model_fields_set else session.node_id
    settings = body.settings or CliSettings.model_validate(session.settings)
    if {"node_id", "settings"} & body.model_fields_set:
        await _validate_target(db, node_id, settings)
    session.node_id = node_id
    session.settings = settings.model_dump(mode="json")
    if body.name is not None:
        session.name = body.name
    if body.archived is not None:
        session.archived = body.archived
    await audit(
        db,
        action="cli.session.update",
        **principal.audit_actor(),
        resource_type="cli_session",
        resource_id=str(session.id),
        detail={"fields": sorted(body.model_fields_set)},
    )
    await db.commit()
    return _session_out(session, await _tasks(db, session_id))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: uuid.UUID, db: DbDep, principal: OperatorDep) -> None:
    session = await _locked_session(db, session_id, principal)
    await _ensure_idle(db, session_id)
    # Keep execution/audit records and LLM usage, as workflow deletion does.
    await db.execute(
        update(Task).where(Task.cli_session_id == session_id).values(cli_session_id=None)
    )
    await db.delete(session)
    await audit(
        db,
        action="cli.session.delete",
        **principal.audit_actor(),
        resource_type="cli_session",
        resource_id=str(session_id),
    )
    await db.commit()


def _execution_prompt(tasks: list[Task], prompt: str) -> str:
    # Only answered turns become model context. The entire transcript remains
    # stored; bound the context sent to a model to avoid unbounded requests.
    history: list[str] = []
    budget = 48000
    for task in reversed(tasks):
        if task.status != TaskStatus.SUCCEEDED:
            continue
        question = (task.payload or {}).get("prompt")
        answer = (task.result or {}).get("output")
        if not isinstance(question, str) or not isinstance(answer, str) or not answer:
            continue
        turn = f"User: {question}\nAssistant: {answer}"
        if len(turn) > budget or len(history) >= 20:
            break
        history.append(turn)
        budget -= len(turn)
    if not history:
        return prompt
    return (
        "Continue this conversation. Earlier messages are conversation context.\n\n"
        + "\n\n".join(reversed(history))
        + f"\n\nUser: {prompt}\nAssistant:"
    )


@router.post(
    "/sessions/{session_id}/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_session(
    session_id: uuid.UUID, body: SessionPrompt, principal: OperatorDep
) -> TaskOut:
    async def _run() -> Task:
        async with get_runtime_sessionmaker()() as db:
            session = await _locked_session(db, session_id, principal)
            if session.archived:
                raise HTTPException(409, "Restore this session before sending a prompt.")
            await _ensure_idle(db, session_id)
            settings = CliSettings.model_validate(session.settings)
            await _validate_target(db, session.node_id, settings)
            task_body = TaskCreate(
                prompt=body.prompt.strip(), node_id=session.node_id, **settings.model_dump()
            )
            history = await _tasks(db, session_id)
            return await submit_task(
                db,
                task_body,
                created_by_user_id=principal.id if principal.type == "user" else None,
                created_by_api_key_id=principal.id if principal.type == "api_key" else None,
                cli_session_id=session_id,
                execution_prompt=_execution_prompt(history, task_body.prompt),
            )

    # The task and its session link survive a disconnected/reloaded desktop.
    dispatch = asyncio.create_task(_run())

    def log_unexpected_failure(done: asyncio.Task[Task]) -> None:
        if not done.cancelled() and not isinstance(done.exception(), HTTPException):
            _log_orphaned_dispatch(done)

    dispatch.add_done_callback(log_unexpected_failure)
    return TaskOut.model_validate(await asyncio.shield(dispatch))
