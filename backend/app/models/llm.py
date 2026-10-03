"""Universal LLM layer persistence (ADR-031).

Credentials live in their own table so that routine account queries never
load ciphertext; the ciphertext is AES-256-GCM bound to its account id, with
the key outside the database.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONVariant, TimestampMixin, UUIDPkMixin
from app.models.user import User


class LLMProviderAccount(UUIDPkMixin, TimestampMixin, Base):
    """A connection to one provider. owner_user_id NULL = deployment account
    (admin-managed, usable by every operator); otherwise a personal account
    only its owner may use."""

    __tablename__ = "llm_provider_accounts"

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(100))
    auth_method: Mapped[str] = mapped_column(String(20))  # api_key | oauth_pkce | none
    base_url: Mapped[str] = mapped_column(String(255))
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    api_access: Mapped[str] = mapped_column(String(20), default="unknown")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(20))
    last_test_detail: Mapped[str | None] = mapped_column(String(200))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id], lazy="joined")


class LLMCredential(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "llm_credentials"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("llm_provider_accounts.id", ondelete="CASCADE"), unique=True
    )
    store: Mapped[str] = mapped_column(String(20))  # db | keyring
    key_id: Mapped[str | None] = mapped_column(String(32))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)


class LLMRoutingPolicy(UUIDPkMixin, TimestampMixin, Base):
    """Ordered (account, model) chain for a purpose: the first entry is the
    primary, the rest are fallbacks. owner_user_id NULL = deployment default."""

    __tablename__ = "llm_routing_policies"

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(30))
    chain: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant)


class LLMUsage(UUIDPkMixin, Base):
    """One row per provider attempt outcome. No prompt or output content."""

    __tablename__ = "llm_usage"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        index=True,
    )
    request_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_provider_accounts.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str | None] = mapped_column(String(30))
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed
    error_code: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    fallback_index: Mapped[int] = mapped_column(Integer, default=0)
    stream: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Numeric(18, 8))
    cost_source: Mapped[str | None] = mapped_column(String(20))  # provider | configured
    currency: Mapped[str | None] = mapped_column(String(3))
