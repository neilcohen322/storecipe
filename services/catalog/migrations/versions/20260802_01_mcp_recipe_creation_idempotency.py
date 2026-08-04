"""Create durable MCP recipe creation idempotency records.

Revision ID: 20260802_01
Revises: 20260801_02
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_01"
down_revision: str | None = "20260801_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipe_creation_idempotency",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["catalog.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipe_id"], ["catalog.recipes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "idempotency_key"),
        sa.UniqueConstraint("recipe_id"),
        sa.CheckConstraint("length(payload_hash) = 64", name="payload_hash_length"),
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_table("recipe_creation_idempotency", schema="catalog")
