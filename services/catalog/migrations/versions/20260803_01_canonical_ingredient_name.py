"""Add required canonical ingredient name.

Revision ID: 20260803_01
Revises: 20260802_01
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_01"
down_revision: str | None = "20260802_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingredients",
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        schema="catalog",
    )
    op.create_index(
        "ix_ingredients_canonical_name_recipe",
        "ingredients",
        ["canonical_name", "recipe_id"],
        unique=False,
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ingredients_canonical_name_recipe",
        table_name="ingredients",
        schema="catalog",
    )
    op.drop_column("ingredients", "canonical_name", schema="catalog")
