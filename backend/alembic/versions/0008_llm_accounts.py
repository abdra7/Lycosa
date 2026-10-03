"""universal LLM layer: provider accounts, encrypted credentials, routing, usage

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02

Additive only: four new tables, no change to existing ones (ADR-031).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
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
        "llm_provider_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("auth_method", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column("is_local", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("api_access", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(20), nullable=True),
        sa.Column("last_test_detail", sa.String(200), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_llm_provider_accounts_owner_user_id", "llm_provider_accounts", ["owner_user_id"]
    )

    op.create_table(
        "llm_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("llm_provider_accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("store", sa.String(20), nullable=False),
        sa.Column("key_id", sa.String(32), nullable=True),
        sa.Column("nonce", sa.LargeBinary(), nullable=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "llm_routing_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("chain", JSON_VARIANT, nullable=False),
        *_timestamps(),
    )
    op.create_index(
        "ix_llm_routing_policies_owner_user_id", "llm_routing_policies", ["owner_user_id"]
    )

    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("request_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), sa.ForeignKey("api_keys.id"), nullable=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("llm_provider_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("fallback_index", sa.Integer(), nullable=False),
        sa.Column("stream", sa.Boolean(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("cost_source", sa.String(20), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index("ix_llm_usage_request_id", "llm_usage", ["request_id"])
    op.create_index("ix_llm_usage_user_id", "llm_usage", ["user_id"])
    op.create_index("ix_llm_usage_account_id", "llm_usage", ["account_id"])


def downgrade() -> None:
    op.drop_table("llm_usage")
    op.drop_table("llm_routing_policies")
    op.drop_table("llm_credentials")
    op.drop_table("llm_provider_accounts")
