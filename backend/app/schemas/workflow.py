import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.task import TaskType
from app.models.workflow import RunStatus, StepRunStatus

_STEP_ID = r"^[a-z0-9][a-z0-9_-]*$"
_TEMPLATE_REF_RE = re.compile(r"\{\{\s*steps\.([a-z0-9_-]+)\.output\s*\}\}")


class WhenClause(BaseModel):
    """Conditional execution: run the step only if a previous step's output
    matches. Exactly one of contains/equals."""

    step: str
    contains: str | None = None
    equals: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "WhenClause":
        if (self.contains is None) == (self.equals is None):
            raise ValueError("when: exactly one of 'contains' or 'equals' is required")
        return self


class _BaseStep(BaseModel):
    id: str = Field(pattern=_STEP_ID, max_length=100)
    when: WhenClause | None = None


class TaskStepDef(_BaseStep):
    kind: Literal["task"]
    prompt: str = Field(min_length=1)
    task_type: TaskType | None = None
    model: str | None = None
    knowledge_query: str | None = None
    retries: int = Field(default=0, ge=0, le=5)

    def template_refs(self) -> set[str]:
        text = self.prompt + " " + (self.knowledge_query or "")
        return set(_TEMPLATE_REF_RE.findall(text))


class RetrieveStepDef(_BaseStep):
    kind: Literal["retrieve"]
    query: str = Field(min_length=1)
    collection: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)

    def template_refs(self) -> set[str]:
        return set(_TEMPLATE_REF_RE.findall(self.query))


class ApprovalStepDef(_BaseStep):
    kind: Literal["approval"]
    message: str = ""

    def template_refs(self) -> set[str]:
        return set()


class ParallelStepDef(_BaseStep):
    kind: Literal["parallel"]
    steps: list[TaskStepDef] = Field(min_length=1, max_length=10)

    def template_refs(self) -> set[str]:
        return set().union(*(s.template_refs() for s in self.steps))


StepDef = Annotated[
    TaskStepDef | RetrieveStepDef | ApprovalStepDef | ParallelStepDef,
    Field(discriminator="kind"),
]


class WorkflowDefinition(BaseModel):
    steps: list[StepDef] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _validate_references(self) -> "WorkflowDefinition":
        seen: set[str] = set()
        for step in self.steps:
            ids_here = [step.id] + (
                [s.id for s in step.steps] if isinstance(step, ParallelStepDef) else []
            )
            for step_id in ids_here:
                if step_id in seen:
                    raise ValueError(f"duplicate step id: {step_id!r}")
            # `when` and template refs may only point at *earlier* steps
            refs = step.template_refs()
            if step.when is not None:
                refs = refs | {step.when.step}
            unknown = refs - seen
            if unknown:
                raise ValueError(
                    f"step {step.id!r} references undefined or later step(s): "
                    f"{', '.join(sorted(unknown))}"
                )
            seen.update(ids_here)
        return self


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    description: str | None = None
    definition: WorkflowDefinition


class WorkflowOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    description: str | None
    definition: dict[str, Any]
    created_at: datetime


class RunRequest(BaseModel):
    input: str = Field(min_length=1)


class ApproveRequest(BaseModel):
    approved: bool


class StepRunOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    step_id: str
    kind: str
    status: StepRunStatus
    attempt: int
    task_id: uuid.UUID | None
    output: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class RunOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    workflow_id: uuid.UUID
    status: RunStatus
    input: str
    context: dict[str, Any]
    current_step: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    step_runs: list[StepRunOut] = []
