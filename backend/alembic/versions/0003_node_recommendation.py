"""node recommendation: recommended role, confidence, rationale

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VARIANT = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("nodes", sa.Column("recommended_role", sa.String(50), nullable=True))
    op.add_column("nodes", sa.Column("recommendation_confidence", sa.Float(), nullable=True))
    op.add_column("nodes", sa.Column("recommendation_rationale", JSON_VARIANT, nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "recommendation_rationale")
    op.drop_column("nodes", "recommendation_confidence")
    op.drop_column("nodes", "recommended_role")
