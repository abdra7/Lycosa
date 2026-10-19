"""workflow run lease: heartbeat_at on workflow_runs

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-16

Additive only: one nullable column (ADR-034). Workers renew it while they
execute a run; the stale-run reaper fails `running` runs whose lease lapsed.
Rows from before this revision stay NULL and the reaper falls back to their
`started_at`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_column("heartbeat_at")
