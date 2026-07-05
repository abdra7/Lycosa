import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONVariant, TimestampMixin, UUIDPkMixin


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    PAUSED = "paused"  # waiting on a human approval step
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"  # `when` condition not met
    PENDING_APPROVAL = "pending_approval"


def _enum(enum_cls: type[enum.StrEnum]) -> Enum:
    return Enum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=20,
    )


class Workflow(UUIDPkMixin, TimestampMixin, Base):
    """A declarative multi-step definition (SDD FR-8). Validated at creation."""

    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text())
    definition: Mapped[dict[str, Any]] = mapped_column(JSONVariant)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class WorkflowRun(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus), default=RunStatus.RUNNING)
    input: Mapped[str] = mapped_column(Text())
    context: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    current_step: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    step_runs: Mapped[list["WorkflowStepRun"]] = relationship(
        back_populates="run", lazy="selectin", order_by="WorkflowStepRun.started_at"
    )


class WorkflowStepRun(UUIDPkMixin, Base):
    """One execution attempt of one step; retries create additional rows."""

    __tablename__ = "workflow_step_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[StepRunStatus] = mapped_column(_enum(StepRunStatus))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    output: Mapped[str | None] = mapped_column(Text())
    error: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[WorkflowRun] = relationship(back_populates="step_runs")
