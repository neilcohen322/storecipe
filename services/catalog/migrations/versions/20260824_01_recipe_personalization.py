"""Add recipe personalization fields and favorite lookup index.

Revision ID: 20260824_01
Revises: 20260812_01
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_01"
down_revision: str | None = "20260812_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("favorite", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="catalog",
    )
    op.add_column(
        "recipes",
        sa.Column("personal_notes", sa.Text(), nullable=True),
        schema="catalog",
    )
    op.add_column(
        "recipes",
        sa.Column("last_cooked_at", sa.DateTime(timezone=True), nullable=True),
        schema="catalog",
    )
    op.create_check_constraint(
        "ck_recipes_personal_notes_length",
        "recipes",
        "personal_notes IS NULL OR length(personal_notes) <= 5000",
        schema="catalog",
    )
    op.create_index(
        "ix_recipes_user_favorite",
        "recipes",
        ["user_id"],
        unique=False,
        schema="catalog",
        postgresql_where=sa.text("favorite IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_recipes_user_favorite", table_name="recipes", schema="catalog")
    op.drop_constraint(
        "ck_recipes_personal_notes_length",
        "recipes",
        type_="check",
        schema="catalog",
    )
    op.drop_column("recipes", "last_cooked_at", schema="catalog")
    op.drop_column("recipes", "personal_notes", schema="catalog")
    op.drop_column("recipes", "favorite", schema="catalog")
