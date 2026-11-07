"""Persistent CLI workspaces, sessions, and task transcript links.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        "cli_workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_type", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("normalized_name", sa.String(180), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("owner_type", "owner_id", "normalized_name"),
    )
    op.create_index("ix_cli_workspaces_owner_id", "cli_workspaces", ["owner_id"])
    op.create_table(
        "cli_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("cli_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("settings", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_cli_sessions_workspace_id", "cli_sessions", ["workspace_id"])
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("cli_session_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_cli_session_id_cli_sessions",
            "cli_sessions",
            ["cli_session_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_tasks_cli_session_id", ["cli_session_id"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_cli_session_id")
        batch.drop_constraint("fk_tasks_cli_session_id_cli_sessions", type_="foreignkey")
        batch.drop_column("cli_session_id")
    op.drop_index("ix_cli_sessions_workspace_id", table_name="cli_sessions")
    op.drop_table("cli_sessions")
    op.drop_index("ix_cli_workspaces_owner_id", table_name="cli_workspaces")
    op.drop_table("cli_workspaces")
