"""Persistent, principal-owned workspaces for the desktop CLI view."""

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONVariant, TimestampMixin, UUIDPkMixin


class CliWorkspace(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "cli_workspaces"
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", "normalized_name"),)

    owner_type: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    name: Mapped[str] = mapped_column(String(60))
    normalized_name: Mapped[str] = mapped_column(String(180))


class CliSession(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "cli_sessions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cli_workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Preserve an unavailable node's identity instead of silently unpinning
    # the session and running its next prompt on another computer.
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
