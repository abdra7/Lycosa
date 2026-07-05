"""heartbeat + agent contact info on nodes

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VARIANT = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "nodes", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("nodes", sa.Column("metrics", JSON_VARIANT, nullable=True))
    op.add_column("nodes", sa.Column("agent_url", sa.String(255), nullable=True))
    op.add_column("nodes", sa.Column("agent_token", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "agent_token")
    op.drop_column("nodes", "agent_url")
    op.drop_column("nodes", "metrics")
    op.drop_column("nodes", "last_heartbeat_at")
