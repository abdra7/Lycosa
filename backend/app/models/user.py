import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_NODE = "node"
ALL_ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_NODE)


class Role(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str | None] = mapped_column(Text())


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    role: Mapped[Role] = relationship(lazy="joined")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user")


class Session(UUIDPkMixin, TimestampMixin, Base):
    """Server-side record of an issued access token (by hashed JTI).

    Lets us revoke JWTs on logout instead of waiting for expiry.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions", lazy="joined")


class ApiKey(UUIDPkMixin, TimestampMixin, Base):
    """Service-to-service / node credential. Only prefix + SHA-256 hash stored."""

    __tablename__ = "api_keys"

    key_prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nodes.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship(lazy="joined")
