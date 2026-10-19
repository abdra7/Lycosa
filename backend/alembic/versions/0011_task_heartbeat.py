"""task lease: heartbeat_at on tasks

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19

Additive only: one nullable column (ADR-035). Workers renew it while they
execute a task; the stale-task reaper fails unfinished tasks whose lease
lapsed. Rows from before this revision stay NULL and the reaper falls back
to their `queued_at`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("heartbeat_at")
