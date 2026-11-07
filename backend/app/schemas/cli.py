import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.task import TaskType
from app.schemas.task import LLM_ROUTE_PATTERN, NonBlankText, TaskCreate, TaskOut

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class CliSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "ollama"
    route: str | None = Field(default=None, pattern=LLM_ROUTE_PATTERN)
    llm_account_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=255)
    type: TaskType | None = None
    knowledge_query: str | None = Field(default=None, max_length=10000)
    knowledge_collection: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def valid_target(self) -> "CliSettings":
        TaskCreate(prompt="Validate session settings", **self.model_dump())
        return self


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    created_at: datetime


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    node_id: uuid.UUID | None = None
    settings: CliSettings = Field(default_factory=CliSettings)


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    archived: bool | None = None
    node_id: uuid.UUID | None = None
    settings: CliSettings | None = None

    @model_validator(mode="after")
    def non_null_fields(self) -> "SessionPatch":
        for field in ("name", "archived", "settings"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class SessionOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    archived: bool
    node_id: uuid.UUID | None
    settings: CliSettings
    tasks: list[TaskOut]
    created_at: datetime


class SessionPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: NonBlankText = Field(max_length=10000)
