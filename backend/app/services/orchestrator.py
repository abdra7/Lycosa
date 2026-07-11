"""Orchestrator: task lifecycle, dispatch to agents, failover (SDD FR-5).

v1 dispatch is synchronous within the request (ADR-012): the caller gets the
finished task back. Each attempt is persisted as a TaskExecution before the
network call, so the trace survives even a controller crash mid-dispatch.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import get_event_bus
from app.core.logging import task_id_var
from app.core.metrics import TASK_DURATION, TASK_FAILOVERS, TASKS_TOTAL
from app.models import ExecutionStatus, Node, Task, TaskExecution, TaskStatus
from app.models.task import TaskType
from app.schemas.task import TaskCreate
from app.services.audit import audit
from app.services.classifier import classify, preferred_roles
from app.services.knowledge.router import retrieve
from app.services.scheduler import rank_candidates

logger = logging.getLogger("lycosa.orchestrator")

AGENT_TOKEN_HEADER = "X-Agent-Token"

# Grounding (ADR-019): the exact sentence a grounded task returns when the
# retrieved knowledge does not contain the answer. Kept as a constant so the
# refusal is identical whether the LLM produces it (per the instruction below)
# or the orchestrator short-circuits it (no relevant context at all).
GROUNDED_REFUSAL = "I cannot answer this based on the retrieved knowledge."

_GROUNDING_INSTRUCTION = (
    "Answer the task using ONLY the retrieved context below. "
    "Do not use any outside or prior knowledge. If the context does not "
    f'contain the answer, reply with exactly this sentence: "{GROUNDED_REFUSAL}"\n\n'
)


def _grounded_prompt(context_text: str, task_prompt: str) -> str:
    return (
        f"{_GROUNDING_INSTRUCTION}Retrieved context:\n{context_text}\n\n---\n\nTask: {task_prompt}"
    )


def _select_model(node: Node, requested: str | None) -> str | None:
    if requested:
        return requested
    profile = node.hardware_profile or {}
    for runtime in profile.get("runtimes", []):
        if runtime.get("models"):
            return runtime["models"][0]
    return None


async def _dispatch(node: Node, model: str, prompt: str, options: dict[str, Any]) -> dict[str, Any]:
    """POST to the agent's exec API. Raises httpx errors on transport failure."""
    timeout = get_settings().task_dispatch_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{node.agent_url.rstrip('/')}/execute",
            json={"model": model, "prompt": prompt, "options": options},
            headers={AGENT_TOKEN_HEADER: node.agent_token},
        )
        response.raise_for_status()
        return response.json()


async def submit_task(
    db: AsyncSession,
    body: TaskCreate,
    created_by_user_id: uuid.UUID | None,
    created_by_api_key_id: uuid.UUID | None,
) -> Task:
    task_type = classify(body.prompt, body.type)
    task = Task(
        type=task_type,
        payload={
            "prompt": body.prompt,
            "model": body.model,
            "options": body.options,
            "knowledge_query": body.knowledge_query,
        },
        created_by_user_id=created_by_user_id,
        created_by_api_key_id=created_by_api_key_id,
    )
    db.add(task)
    await db.flush()
    task_id_var.set(str(task.id))  # correlation id for every log line below
    get_event_bus().publish("task.started", {"task_id": str(task.id), "type": task_type.value})
    await audit(
        db,
        action="task.submit",
        actor_user_id=created_by_user_id,
        actor_api_key_id=created_by_api_key_id,
        resource_type="task",
        resource_id=str(task.id),
        detail={"type": task_type.value},
    )
    await db.commit()

    # knowledge injection: explicit query wins; retrieval-type tasks use the
    # prompt itself. The agent never learns where knowledge lives (FR-9).
    prompt = body.prompt
    knowledge_query = body.knowledge_query or (
        body.prompt if task_type == TaskType.RETRIEVAL else None
    )
    if knowledge_query:
        retrieval_failed = False
        knowledge = None
        try:
            knowledge = await retrieve(
                db,
                knowledge_query,
                requested_by_user_id=created_by_user_id,
                requested_by_api_key_id=created_by_api_key_id,
                min_score=get_settings().retrieval_min_score,
            )
        except Exception:
            # an infra failure (e.g. Qdrant down) is not an out-of-scope
            # question — degrade to dispatch-without-context rather than refuse
            retrieval_failed = True
            logger.exception("knowledge retrieval failed; dispatching without context")

        if not retrieval_failed:
            if knowledge and knowledge.chunks:
                # ground the answer in the retrieved context (ADR-019)
                prompt = _grounded_prompt(knowledge.context_text, body.prompt)
            else:
                # retrieval succeeded but nothing relevant: return the grounded
                # refusal instead of letting the LLM answer from outside knowledge
                logger.info("no relevant knowledge for query; returning grounded refusal")
                return await _finish(
                    db,
                    task,
                    TaskStatus.SUCCEEDED,
                    result={
                        "output": GROUNDED_REFUSAL,
                        "model": None,
                        "node": None,
                        "grounded": False,
                    },
                )

    candidates = await rank_candidates(db, task_type, model=body.model)
    max_attempts = get_settings().task_max_attempts

    if not candidates:
        roles = ", ".join(preferred_roles(task_type))
        return await _finish(
            db,
            task,
            TaskStatus.FAILED,
            error=(
                f"No compatible online node for task type {task_type.value!r} "
                f"(needs an online node with role in [{roles}] and a reachable agent; "
                "check node status and role assignments)"
            ),
        )

    last_error = "unknown"
    for attempt, node in enumerate(candidates[:max_attempts], start=1):
        task.status = TaskStatus.ASSIGNED
        task.assigned_at = datetime.now(UTC)
        task.node_id = node.id

        if attempt > 1:
            TASK_FAILOVERS.inc()
        execution = TaskExecution(
            task_id=task.id, node_id=node.id, attempt=attempt, status=ExecutionStatus.RUNNING
        )
        db.add(execution)
        task.status = TaskStatus.RUNNING
        if task.started_at is None:
            task.started_at = datetime.now(UTC)
        await db.commit()

        model = _select_model(node, body.model)
        if model is None:
            await _finish_execution(
                db, execution, ExecutionStatus.FAILED, error="node advertises no models"
            )
            last_error = f"node {node.name}: no models available"
            continue

        try:
            outcome = await _dispatch(node, model, prompt, body.options)
        except httpx.HTTPError as exc:
            last_error = f"node {node.name}: {exc}"
            logger.warning("dispatch attempt %d failed: %s", attempt, last_error)
            await _finish_execution(db, execution, ExecutionStatus.FAILED, error=str(exc))
            continue

        if outcome.get("status") == "succeeded":
            await _finish_execution(
                db, execution, ExecutionStatus.SUCCEEDED, output=outcome.get("output")
            )
            return await _finish(
                db,
                task,
                TaskStatus.SUCCEEDED,
                result={"output": outcome.get("output"), "model": model, "node": str(node.id)},
            )

        last_error = f"node {node.name}: {outcome.get('error', 'agent reported failure')}"
        await _finish_execution(db, execution, ExecutionStatus.FAILED, error=outcome.get("error"))

    return await _finish(
        db,
        task,
        TaskStatus.FAILED,
        error=f"all {min(len(candidates), max_attempts)} attempt(s) failed; last: {last_error}",
    )


async def _finish_execution(
    db: AsyncSession,
    execution: TaskExecution,
    status: ExecutionStatus,
    output: str | None = None,
    error: str | None = None,
) -> None:
    execution.status = status
    execution.output = output
    execution.error = error
    execution.finished_at = datetime.now(UTC)
    await db.commit()


async def _finish(
    db: AsyncSession,
    task: Task,
    status: TaskStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> Task:
    task.status = status
    task.result = result
    task.error = error
    task.finished_at = datetime.now(UTC)
    await audit(
        db,
        action="task.finished",
        resource_type="task",
        resource_id=str(task.id),
        detail={"status": status.value},
    )
    await db.commit()
    await db.refresh(task)

    def as_utc(dt: datetime) -> datetime:  # SQLite returns naive datetimes
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    TASKS_TOTAL.labels(task.type.value, status.value).inc()
    TASK_DURATION.labels(task.type.value).observe(
        (as_utc(task.finished_at) - as_utc(task.queued_at)).total_seconds()
    )
    get_event_bus().publish(
        "task.finished",
        {"task_id": str(task.id), "status": status.value, "error": error},
    )
    task_id_var.set(None)
    return task
