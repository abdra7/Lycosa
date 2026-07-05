"""node registry: normalized hardware columns for scheduling

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("cpu_cores", sa.Integer(), nullable=True))
    op.add_column("nodes", sa.Column("ram_gb", sa.Float(), nullable=True))
    op.add_column("nodes", sa.Column("gpu_count", sa.Integer(), nullable=True))
    op.add_column("nodes", sa.Column("gpu_vram_gb", sa.Float(), nullable=True))
    op.add_column("nodes", sa.Column("storage_gb", sa.Float(), nullable=True))
    op.add_column("nodes", sa.Column("os_name", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "os_name")
    op.drop_column("nodes", "storage_gb")
    op.drop_column("nodes", "gpu_vram_gb")
    op.drop_column("nodes", "gpu_count")
    op.drop_column("nodes", "ram_gb")
    op.drop_column("nodes", "cpu_cores")
