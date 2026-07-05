"""Workflow executor (SDD FR-8): sequential steps with templated context
propagation, conditional branching, retries, parallel fan-out, and human
approval checkpoints.

Execution is synchronous per request (ADR-012/ADR-014): a run proceeds until
it finishes or hits an approval step; approval resumes it inside the approve
request. Task steps reuse the Phase 5 orchestrator wholesale, so scheduling,
failover, and knowledge injection apply per step.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import get_event_bus
from app.core.logging import workflow_run_id_var
from app.core.metrics import WORKFLOW_RUNS, WORKFLOW_STEPS
from app.db.session import get_runtime_sessionmaker
from app.models import (
    RunStatus,
    StepRunStatus,
    TaskStatus,
    Workflow,
    WorkflowRun,
    WorkflowStepRun,
)
from app.schemas.task import TaskCreate
from app.schemas.workflow import (
    ApprovalStepDef,
    ParallelStepDef,
    RetrieveStepDef,
    TaskStepDef,
    WhenClause,
    WorkflowDefinition,
)
from app.services.audit import audit
from app.services.knowledge.router import retrieve
from app.services.orchestrator import submit_task

_TEMPLATE_RE = re.compile(r"\{\{\s*(input|steps\.[a-z0-9_-]+\.output)\s*\}\}")


class _StepFailed(Exception):
    pass


def render(template: str, run_input: str, context: dict) -> str:
    steps = context.get("steps", {})

    def replace(match: re.Match) -> str:
        ref = match.group(1)
        if ref == "input":
            return run_input
        step_id = ref.split(".")[1]
        return str(steps.get(step_id, {}).get("output") or "")

    return _TEMPLATE_RE.sub(replace, template)


def _condition_met(when: WhenClause, context: dict) -> bool:
    output = str(context.get("steps", {}).get(when.step, {}).get("output") or "")
    if when.contains is not None:
        return when.contains in output
    return output == when.equals


def _record_output(run: WorkflowRun, step_id: str, output: str) -> None:
    steps = dict(run.context.get("steps", {}))
    steps[step_id] = {"output": output, "status": "succeeded"}
    run.context = {**run.context, "steps": steps}  # reassign: JSONB change detection


async def start_run(
    db: AsyncSession,
    workflow: Workflow,
    run_input: str,
    user_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None,
) -> WorkflowRun:
    definition = WorkflowDefinition.model_validate(workflow.definition)
    run = WorkflowRun(
        workflow_id=workflow.id, input=run_input, context={"steps": {}}, status=RunStatus.RUNNING
    )
    db.add(run)
    await db.flush()
    workflow_run_id_var.set(str(run.id))  # log correlation for the whole run
    get_event_bus().publish(
        "workflow.started",
        {"run_id": str(run.id), "workflow_id": str(workflow.id), "name": workflow.name},
    )
    await audit(
        db,
        action="workflow.run.started",
        actor_user_id=user_id,
        actor_api_key_id=api_key_id,
        resource_type="workflow_run",
        resource_id=str(run.id),
        detail={"workflow": workflow.name},
    )
    await db.commit()
    return await _execute_from(db, run, definition, 0, user_id, api_key_id)


async def resume_run(
    db: AsyncSession,
    run: WorkflowRun,
    definition_raw: dict,
    approved: bool,
    user_id: uuid.UUID | None,
) -> WorkflowRun:
    """Resolve the pending approval step, then continue (or fail) the run."""
    definition = WorkflowDefinition.model_validate(definition_raw)
    index = next(i for i, s in enumerate(definition.steps) if s.id == run.current_step)

    pending = next(
        sr
        for sr in run.step_runs
        if sr.step_id == run.current_step and sr.status == StepRunStatus.PENDING_APPROVAL
    )
    pending.finished_at = datetime.now(UTC)
    await audit(
        db,
        action="workflow.run.approved" if approved else "workflow.run.rejected",
        actor_user_id=user_id,
        resource_type="workflow_run",
        resource_id=str(run.id),
        detail={"step": run.current_step},
    )

    if not approved:
        pending.status = StepRunStatus.FAILED
        pending.error = "rejected by operator"
        return await _finish(db, run, RunStatus.FAILED, error=f"step {run.current_step!r} rejected")

    pending.status = StepRunStatus.SUCCEEDED
    _record_output(run, run.current_step, "approved")
    run.status = RunStatus.RUNNING
    await db.commit()
    return await _execute_from(db, run, definition, index + 1, user_id, None)


async def _execute_from(
    db: AsyncSession,
    run: WorkflowRun,
    definition: WorkflowDefinition,
    start_index: int,
    user_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None,
) -> WorkflowRun:
    for index in range(start_index, len(definition.steps)):
        step = definition.steps[index]
        run.current_step = step.id
        await db.commit()

        if step.when is not None and not _condition_met(step.when, run.context):
            db.add(
                WorkflowStepRun(
                    run_id=run.id,
                    step_id=step.id,
                    kind=step.kind,
                    status=StepRunStatus.SKIPPED,
                    finished_at=datetime.now(UTC),
                )
            )
            await db.commit()
            continue

        if isinstance(step, ApprovalStepDef):
            db.add(
                WorkflowStepRun(
                    run_id=run.id,
                    step_id=step.id,
                    kind=step.kind,
                    status=StepRunStatus.PENDING_APPROVAL,
                    output=render(step.message, run.input, run.context) or None,
                )
            )
            run.status = RunStatus.PAUSED
            await audit(
                db,
                action="workflow.run.paused",
                resource_type="workflow_run",
                resource_id=str(run.id),
                detail={"step": step.id},
            )
            await db.commit()
            await db.refresh(run)
            WORKFLOW_STEPS.labels(step.kind, "pending_approval").inc()
            get_event_bus().publish("workflow.paused", {"run_id": str(run.id), "step": step.id})
            return run

        try:
            output = await _run_step_with_retries(db, run, step, user_id, api_key_id)
        except _StepFailed as exc:
            WORKFLOW_STEPS.labels(step.kind, "failed").inc()
            return await _finish(db, run, RunStatus.FAILED, error=f"step {step.id!r} failed: {exc}")
        _record_output(run, step.id, output)
        await db.commit()
        WORKFLOW_STEPS.labels(step.kind, "succeeded").inc()
        get_event_bus().publish(
            "workflow.step.completed",
            {"run_id": str(run.id), "step": step.id, "kind": step.kind},
        )

    return await _finish(db, run, RunStatus.SUCCEEDED)


async def _run_step_with_retries(
    db: AsyncSession,
    run: WorkflowRun,
    step: TaskStepDef | RetrieveStepDef | ParallelStepDef,
    user_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None,
) -> str:
    retries = step.retries if isinstance(step, TaskStepDef) else 0
    last_error = "unknown"
    for attempt in range(1, retries + 2):
        step_run = WorkflowStepRun(
            run_id=run.id,
            step_id=step.id,
            kind=step.kind,
            status=StepRunStatus.RUNNING,
            attempt=attempt,
        )
        db.add(step_run)
        await db.commit()

        output: str | None = None
        error: str | None = None
        if isinstance(step, TaskStepDef):
            output, error, task_id = await _run_task(db, step, run, user_id, api_key_id)
            step_run.task_id = task_id
        elif isinstance(step, RetrieveStepDef):
            try:
                result = await retrieve(
                    db,
                    render(step.query, run.input, run.context),
                    collection_name=step.collection,
                    top_k=step.top_k,
                    requested_by_user_id=user_id,
                    requested_by_api_key_id=api_key_id,
                )
                output = result.context_text
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        else:  # parallel
            output, error = await _run_parallel(run, step, user_id, api_key_id)

        step_run.finished_at = datetime.now(UTC)
        if error is None:
            step_run.status = StepRunStatus.SUCCEEDED
            step_run.output = output
            await db.commit()
            return output or ""
        step_run.status = StepRunStatus.FAILED
        step_run.error = error
        last_error = error
        await db.commit()

    raise _StepFailed(last_error)


async def _run_task(
    db: AsyncSession,
    step: TaskStepDef,
    run: WorkflowRun,
    user_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None,
) -> tuple[str | None, str | None, uuid.UUID | None]:
    body = TaskCreate(
        prompt=render(step.prompt, run.input, run.context),
        type=step.task_type,
        model=step.model,
        knowledge_query=(
            render(step.knowledge_query, run.input, run.context) if step.knowledge_query else None
        ),
    )
    task = await submit_task(db, body, user_id, api_key_id)
    if task.status == TaskStatus.SUCCEEDED:
        return (task.result or {}).get("output", ""), None, task.id
    return None, task.error or "task failed", task.id


async def _run_parallel(
    run: WorkflowRun,
    step: ParallelStepDef,
    user_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None,
) -> tuple[str | None, str | None]:
    """Fan out task substeps concurrently, each on its own DB session.
    Substep outputs land in the run context under their own ids."""
    run_input, context = run.input, dict(run.context)

    async def one(sub: TaskStepDef) -> tuple[str, str | None, str | None]:
        async with get_runtime_sessionmaker()() as sub_db:
            snapshot_run = WorkflowRun(input=run_input, context=context)  # detached, render-only
            output, error, _ = await _run_task(sub_db, sub, snapshot_run, user_id, api_key_id)
            return sub.id, output, error

    results = await asyncio.gather(*(one(sub) for sub in step.steps))

    failures = [f"{sub_id}: {error}" for sub_id, _, error in results if error is not None]
    if failures:
        return None, "; ".join(failures)

    for sub_id, output, _ in results:
        _record_output(run, sub_id, output or "")
    return "\n\n".join(output or "" for _, output, _ in results), None


async def _finish(
    db: AsyncSession, run: WorkflowRun, status: RunStatus, error: str | None = None
) -> WorkflowRun:
    run.status = status
    run.error = error
    run.finished_at = datetime.now(UTC)
    await audit(
        db,
        action="workflow.run.finished",
        resource_type="workflow_run",
        resource_id=str(run.id),
        detail={"status": status.value},
    )
    await db.commit()
    await db.refresh(run)

    WORKFLOW_RUNS.labels(status.value).inc()
    bus = get_event_bus()
    bus.publish(
        "workflow.finished",
        {"run_id": str(run.id), "status": status.value, "error": error},
    )
    if status == RunStatus.FAILED:
        bus.publish(
            "alert.created",
            {
                "severity": "warning",
                "message": f"Workflow run {run.id} failed: {error}",
                "run_id": str(run.id),
            },
        )
    workflow_run_id_var.set(None)
    return run
