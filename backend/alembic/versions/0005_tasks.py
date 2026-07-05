"""tasks and task executions

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VARIANT = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("payload", JSON_VARIANT, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", JSON_VARIANT, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("node_id", sa.Uuid(), sa.ForeignKey("nodes.id"), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_by_api_key_id", sa.Uuid(), sa.ForeignKey("api_keys.id"), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "task_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("node_id", sa.Uuid(), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_executions_task_id", "task_executions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_executions_task_id", table_name="task_executions")
    op.drop_table("task_executions")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")
