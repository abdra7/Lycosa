import enum
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONVariant, TimestampMixin, UUIDPkMixin


class NodeStatus(enum.StrEnum):
    REGISTERED = "registered"
    ONLINE = "online"
    OFFLINE = "offline"


class Node(UUIDPkMixin, TimestampMixin, Base):
    """A device in the fabric. Registration/hardware profile owned by Sprint 2,
    role recommendation by Sprint 3 — this sprint only defines the shape."""

    __tablename__ = "nodes"

    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[NodeStatus] = mapped_column(
        Enum(
            NodeStatus, values_callable=lambda e: [m.value for m in e], native_enum=False, length=20
        ),
        default=NodeStatus.REGISTERED,
    )
    role: Mapped[str | None] = mapped_column(String(50))
    hardware_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)

    agents: Mapped[list["Agent"]] = relationship(back_populates="node")


class Agent(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nodes.id"))
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="registered")
    runtime: Mapped[str | None] = mapped_column(String(50))

    node: Mapped[Node] = relationship(back_populates="agents")
    capabilities: Mapped[list["AgentCapability"]] = relationship(back_populates="agent")


class AgentCapability(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "agent_capabilities"

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    capability: Mapped[str] = mapped_column(String(100))
    # "metadata" is reserved on Declarative classes; attribute is `meta`, column stays "metadata"
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONVariant)

    agent: Mapped[Agent] = relationship(back_populates="capabilities")
