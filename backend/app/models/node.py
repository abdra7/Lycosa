import enum
import uuid
from typing import Any

from sqlalchemy import Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONVariant, TimestampMixin, UUIDPkMixin


class NodeStatus(enum.StrEnum):
    REGISTERED = "registered"
    ONLINE = "online"
    OFFLINE = "offline"


class NodeRole(enum.StrEnum):
    AI_COMPUTE = "ai_compute"
    HYBRID = "hybrid"
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    VISION = "vision"
    STORAGE = "storage"


class Node(UUIDPkMixin, TimestampMixin, Base):
    """A device in the fabric.

    `hardware_profile` holds the raw registration payload; the normalized
    columns are denormalized from it for scheduler queries (Sprint 5).
    """

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

    # recommendation engine output, recomputed on every (re-)registration;
    # `role` above stays operator-assigned and is never touched by the engine
    recommended_role: Mapped[str | None] = mapped_column(String(50))
    recommendation_confidence: Mapped[float | None] = mapped_column(Float)
    recommendation_rationale: Mapped[list[str] | None] = mapped_column(JSONVariant)

    # normalized from hardware_profile at registration time
    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    ram_gb: Mapped[float | None] = mapped_column(Float)
    gpu_count: Mapped[int | None] = mapped_column(Integer)
    gpu_vram_gb: Mapped[float | None] = mapped_column(Float)  # max VRAM of any single card
    storage_gb: Mapped[float | None] = mapped_column(Float)
    os_name: Mapped[str | None] = mapped_column(String(50))

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
