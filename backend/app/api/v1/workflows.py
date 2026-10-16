import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select

from app.api.deps import DbDep, Principal, require_roles
from app.models import RunStatus, Workflow, WorkflowRun, WorkflowStepRun
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.workflow import (
    ApproveRequest,
    RunOut,
    RunRequest,
    WorkflowCreate,
    WorkflowOut,
)
from app.services.audit import audit
from app.services.workflow import resume_run, start_run

router = APIRouter(prefix="/workflows", tags=["workflows"])

OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]


async def _get_workflow(db: DbDep, workflow_id: uuid.UUID) -> Workflow:
    workflow = (
        await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    ).scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return workflow


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate, principal: OperatorDep, request: Request, db: DbDep
) -> WorkflowOut:
    """Create a workflow. The declarative definition is validated here:
    unknown kinds, duplicate ids, and references to later steps are 422s."""
    existing = (
        await db.execute(select(Workflow).where(Workflow.name == body.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow {body.name!r} already exists",
        )
    workflow = Workflow(
        name=body.name,
        description=body.description,
        definition=body.definition.model_dump(mode="json", exclude_none=True),
        created_by_user_id=principal.id if principal.type == "user" else None,
    )
    db.add(workflow)
    await db.flush()
    await audit(
        db,
        action="workflow.create",
        **principal.audit_actor(),
        resource_type="workflow",
        resource_id=str(workflow.id),
        detail={"name": body.name},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(workflow)
    return WorkflowOut.model_validate(workflow)


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(db: DbDep, _principal: OperatorDep) -> list[WorkflowOut]:
    workflows = (await db.execute(select(Workflow).order_by(Workflow.name))).scalars().all()
    return [WorkflowOut.model_validate(w) for w in workflows]


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: uuid.UUID, db: DbDep, _principal: OperatorDep) -> WorkflowOut:
    return WorkflowOut.model_validate(await _get_workflow(db, workflow_id))


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: uuid.UUID, principal: OperatorDep, request: Request, db: DbDep
) -> None:
    """Delete a workflow and its run history (ADR-033). A run that is still
    executing or waiting on an approval blocks the delete (409), so nobody's
    pending decision disappears. Tasks the steps ran are kept: they are the
    task history, and usage rows keep their run id."""
    workflow = await _get_workflow(db, workflow_id)
    run_ids = select(WorkflowRun.id).where(WorkflowRun.workflow_id == workflow_id)
    open_runs = (
        await db.execute(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.status.in_((RunStatus.RUNNING, RunStatus.PAUSED)),
            )
        )
    ).scalar_one()
    if open_runs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Workflow {workflow.name!r} has {open_runs} run(s) still running or "
                "waiting for approval; approve or reject them first"
            ),
        )
    run_count = (
        await db.execute(select(func.count()).select_from(run_ids.subquery()))
    ).scalar_one()
    await db.execute(delete(WorkflowStepRun).where(WorkflowStepRun.run_id.in_(run_ids)))
    await db.execute(delete(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id))
    await db.delete(workflow)
    await audit(
        db,
        action="workflow.delete",
        **principal.audit_actor(),
        resource_type="workflow",
        resource_id=str(workflow_id),
        detail={"name": workflow.name, "runs": run_count},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()


@router.post("/{workflow_id}/run", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def run_workflow(
    workflow_id: uuid.UUID, body: RunRequest, principal: OperatorDep, db: DbDep
) -> RunOut:
    """Execute the workflow synchronously until it finishes or pauses at an
    approval step (ADR-014). The response carries the full step trace."""
    workflow = await _get_workflow(db, workflow_id)
    run = await start_run(
        db,
        workflow,
        body.input,
        user_id=principal.id if principal.type == "user" else None,
        api_key_id=principal.id if principal.type == "api_key" else None,
    )
    return RunOut.model_validate(run)


@router.get("/{workflow_id}/runs/{run_id}", response_model=RunOut)
async def get_run(
    workflow_id: uuid.UUID, run_id: uuid.UUID, db: DbDep, _principal: OperatorDep
) -> RunOut:
    run = (
        await db.execute(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id, WorkflowRun.workflow_id == workflow_id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return RunOut.model_validate(run)


@router.post("/{workflow_id}/runs/{run_id}/approve", response_model=RunOut)
async def approve_run(
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    body: ApproveRequest,
    principal: OperatorDep,
    request: Request,
    db: DbDep,
) -> RunOut:
    """Resolve a paused approval step: approve resumes execution, reject
    fails the run. Audited either way."""
    workflow = await _get_workflow(db, workflow_id)
    run = (
        await db.execute(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id, WorkflowRun.workflow_id == workflow_id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.status != RunStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is {run.status.value}, not paused",
        )
    run = await resume_run(
        db,
        run,
        workflow.definition,
        approved=body.approved,
        user_id=principal.id if principal.type == "user" else None,
        api_key_id=principal.id if principal.type == "api_key" else None,
        ip_address=request.client.host if request.client else None,
    )
    return RunOut.model_validate(run)
