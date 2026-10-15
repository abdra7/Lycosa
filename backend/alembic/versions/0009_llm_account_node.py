"""node-run LLM accounts: bind an account to the node whose sign-in runs it

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15

Additive only: one nullable column on llm_provider_accounts (ADR-032).
Removing a node clears the binding; the account then reports the node as
unavailable instead of disappearing.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_llm_provider_accounts_node_id_nodes"
INDEX_NAME = "ix_llm_provider_accounts_node_id"


def upgrade() -> None:
    # batch mode so SQLite (tests, local runs) can add the foreign key too
    with op.batch_alter_table("llm_provider_accounts") as batch:
        batch.add_column(sa.Column("node_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(FK_NAME, "nodes", ["node_id"], ["id"], ondelete="SET NULL")
        batch.create_index(INDEX_NAME, ["node_id"])


def downgrade() -> None:
    with op.batch_alter_table("llm_provider_accounts") as batch:
        batch.drop_index(INDEX_NAME)
        batch.drop_constraint(FK_NAME, type_="foreignkey")
        batch.drop_column("node_id")
