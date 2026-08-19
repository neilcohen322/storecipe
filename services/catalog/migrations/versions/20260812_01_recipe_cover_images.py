"""Persist one optional cover image per recipe.

Revision ID: 20260812_01
Revises: 20260803_01
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_01"
down_revision: str | None = "20260803_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipe_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("object_generation", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["catalog.recipes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_id", name="uq_recipe_images_recipe_id"),
        sa.UniqueConstraint("object_key", name="uq_recipe_images_object_key"),
        sa.CheckConstraint("content_type = 'image/webp'", name="ck_recipe_images_webp"),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 1572864",
            name="ck_recipe_images_byte_size",
        ),
        sa.CheckConstraint("length(sha256) = 64", name="ck_recipe_images_sha256"),
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_table("recipe_images", schema="catalog")
