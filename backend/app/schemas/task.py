import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.task import ExecutionStatus, TaskStatus, TaskType


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    type: TaskType | None = None  # omit to let the classifier decide
    model: str | None = None  # omit to use the chosen node's first available model
    options: dict[str, Any] = {}


class TaskExecutionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    node_id: uuid.UUID
    attempt: int
    status: ExecutionStatus
    output: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class TaskOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    type: TaskType
    status: TaskStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    node_id: uuid.UUID | None
    queued_at: datetime
    assigned_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    executions: list[TaskExecutionOut] = []
