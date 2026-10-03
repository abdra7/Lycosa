import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.task import ExecutionStatus, TaskStatus, TaskType
from app.services.provider_registry import validate_provider

LLM_ROUTE_PATTERN = r"^(auto|default|coding|reasoning|vision|cheap|private|offline)$"


def check_llm_target(model: Any) -> None:
    """Shared by tasks and workflow task steps: the universal LLM layer is
    selected with an account (+ model) or a routing purpose, never together
    with a legacy `provider`."""
    account, route = model.llm_account_id, model.route
    if account is not None and route is not None:
        raise ValueError("llm_account_id and route are mutually exclusive")
    # the default provider is tolerated: stored workflow definitions carry it
    if (account is not None or route is not None) and model.provider != "ollama":
        raise ValueError("provider selects a legacy route; omit it with llm_account_id/route")
    if account is not None and not model.model:
        raise ValueError("model is required with llm_account_id")


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1)
    type: TaskType | None = None  # omit to let the classifier decide
    model: str | None = None  # omit to use the chosen node's first available model
    options: dict[str, Any] = {}
    # when set (or for retrieval-type tasks, using the prompt), the Knowledge
    # Router injects retrieved context into the prompt before dispatch
    knowledge_query: str | None = None
    knowledge_collection: str | None = None
    provider: str = "ollama"
    _provider = field_validator("provider")(validate_provider)
    requires_privacy: bool = False
    required_vram_mb: int | None = Field(default=None, ge=0)
    required_ram_mb: int | None = Field(default=None, ge=0)
    allow_cpu_fallback: bool = False
    max_tokens: int = Field(default=4096, ge=1, le=16384)
    temperature: float = Field(default=0.2, ge=0, le=1)
    # universal LLM layer (ADR-031): a provider account + model, or a purpose
    llm_account_id: uuid.UUID | None = None
    route: str | None = Field(default=None, pattern=LLM_ROUTE_PATTERN)

    @model_validator(mode="after")
    def _llm_target(self) -> "TaskCreate":
        check_llm_target(self)
        return self

    @property
    def uses_llm_layer(self) -> bool:
        return self.llm_account_id is not None or self.route is not None


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
