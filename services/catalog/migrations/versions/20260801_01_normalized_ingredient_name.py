"""Persist normalized ingredient names.

Revision ID: 20260801_01
Revises: 20260729_01
Create Date: 2026-08-01
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_01"
down_revision: str | None = "20260729_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_query_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split()).casefold()
    return unicodedata.normalize("NFC", normalized)


def upgrade() -> None:
    op.add_column(
        "ingredients",
        sa.Column("normalized_name", sa.String(length=200), nullable=True),
        schema="catalog",
    )
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, name FROM catalog.ingredients")).mappings().all()
    for row in rows:
        connection.execute(
            sa.text(
                "UPDATE catalog.ingredients SET normalized_name = :normalized_name WHERE id = :id"
            ),
            {
                "id": row["id"],
                "normalized_name": _normalize_query_text(row["name"]),
            },
        )
    op.alter_column(
        "ingredients",
        "normalized_name",
        nullable=False,
        schema="catalog",
    )
    op.create_index(
        "ix_ingredients_normalized_name_recipe",
        "ingredients",
        ["normalized_name", "recipe_id"],
        unique=False,
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ingredients_normalized_name_recipe",
        table_name="ingredients",
        schema="catalog",
    )
    op.drop_column("ingredients", "normalized_name", schema="catalog")
