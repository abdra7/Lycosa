"""ingestion lease: heartbeat_at on embedding_jobs

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-19

Additive only: one nullable column (ADR-036). Workers renew it while they
ingest a document; the stuck-ingestion reaper fails `running` jobs whose lease
lapsed, and their `uploaded` documents. Rows from before this revision stay
NULL and the reaper falls back to their `started_at`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "embedding_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("embedding_jobs") as batch:
        batch.drop_column("heartbeat_at")
