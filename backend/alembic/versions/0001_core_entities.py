"""core entities: roles, users, sessions, api_keys, nodes, agents, capabilities, audit_logs

Revision ID: 0001
Revises:
Create Date: 2026-07-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VARIANT = sa.JSON().with_variant(JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("hardware_profile", JSON_VARIANT, nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key_prefix", sa.String(16), nullable=False, unique=True, index=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("node_id", sa.Uuid(), sa.ForeignKey("nodes.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("node_id", sa.Uuid(), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("runtime", sa.String(50), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "agent_capabilities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("metadata", JSON_VARIANT, nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_api_key_id", sa.Uuid(), sa.ForeignKey("api_keys.id"), nullable=True),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("detail", JSON_VARIANT, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("agent_capabilities")
    op.drop_table("agents")
    op.drop_table("api_keys")
    op.drop_table("sessions")
    op.drop_table("nodes")
    op.drop_table("users")
    op.drop_table("roles")
