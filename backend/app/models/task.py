import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONVariant, TimestampMixin, UUIDPkMixin


class TaskType(enum.StrEnum):
    CODING = "coding"
    RETRIEVAL = "retrieval"
    TOOL = "tool"
    VISION = "vision"
    GENERAL = "general"


class TaskStatus(enum.StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExecutionStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum(enum_cls: type[enum.StrEnum], length: int = 20) -> Enum:
    return Enum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=length,
    )


class Task(UUIDPkMixin, TimestampMixin, Base):
    """A unit of work submitted to the fabric (SDD FR-5)."""

    __tablename__ = "tasks"

    type: Mapped[TaskType] = mapped_column(_enum(TaskType))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONVariant)  # prompt, model?, options?
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus), default=TaskStatus.QUEUED)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
    error: Mapped[str | None] = mapped_column(Text())
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nodes.id"))  # winning node

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by_api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id"))

    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    executions: Mapped[list["TaskExecution"]] = relationship(
        back_populates="task", lazy="selectin", order_by="TaskExecution.attempt"
    )


class TaskExecution(UUIDPkMixin, Base):
    """One dispatch attempt of a task on a node. Failover = multiple rows."""

    __tablename__ = "task_executions"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nodes.id"))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[ExecutionStatus] = mapped_column(_enum(ExecutionStatus))
    output: Mapped[str | None] = mapped_column(Text())
    error: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="executions")
