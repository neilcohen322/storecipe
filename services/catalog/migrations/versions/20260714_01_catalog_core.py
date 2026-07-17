"""Create the catalog-owned core tables.

Revision ID: 20260714_01
Revises:
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("auth_subject", sa.String(length=255), nullable=False),
        sa.Column("catalog_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("auth_subject", name="uq_users_auth_subject"),
        schema="catalog",
    )
    op.create_table(
        "recipes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("servings", sa.Integer(), nullable=True),
        sa.Column("prep_minutes", sa.Integer(), nullable=True),
        sa.Column("cook_minutes", sa.Integer(), nullable=True),
        sa.Column("total_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cook_minutes IS NULL OR cook_minutes >= 0", name="ck_recipes_nonnegative_cook"
        ),
        sa.CheckConstraint(
            "prep_minutes IS NULL OR prep_minutes >= 0", name="ck_recipes_nonnegative_prep"
        ),
        sa.CheckConstraint("servings IS NULL OR servings > 0", name="ck_recipes_positive_servings"),
        sa.CheckConstraint(
            "total_minutes IS NULL OR total_minutes >= 0", name="ck_recipes_nonnegative_total"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["catalog.users.id"], name="fk_recipes_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recipes"),
        sa.UniqueConstraint("user_id", "import_job_id", name="uq_recipes_user_import_job"),
        schema="catalog",
    )
    op.create_index("ix_recipes_user_id", "recipes", ["user_id"], unique=False, schema="catalog")

    op.create_table(
        "ingredients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.CheckConstraint("position >= 0", name="ck_ingredients_nonnegative_position"),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0", name="ck_ingredients_nonnegative_quantity"
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["catalog.recipes.id"],
            name="fk_ingredients_recipe_id_recipes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingredients"),
        sa.UniqueConstraint("recipe_id", "position", name="uq_ingredients_recipe_position"),
        schema="catalog",
    )
    op.create_index(
        "ix_ingredients_recipe_id", "ingredients", ["recipe_id"], unique=False, schema="catalog"
    )

    op.create_table(
        "instructions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_instructions_nonnegative_position"),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["catalog.recipes.id"],
            name="fk_instructions_recipe_id_recipes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_instructions"),
        sa.UniqueConstraint("recipe_id", "position", name="uq_instructions_recipe_position"),
        schema="catalog",
    )
    op.create_index(
        "ix_instructions_recipe_id",
        "instructions",
        ["recipe_id"],
        unique=False,
        schema="catalog",
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint("name", name="uq_tags_name"),
        schema="catalog",
    )

    op.create_table(
        "recipe_tags",
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["catalog.recipes.id"],
            name="fk_recipe_tags_recipe_id_recipes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["catalog.tags.id"],
            name="fk_recipe_tags_tag_id_tags",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("recipe_id", "tag_id", name="pk_recipe_tags"),
        schema="catalog",
    )

    op.create_table(
        "ratings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("value BETWEEN 1 AND 5", name="ck_ratings_value_range"),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["catalog.recipes.id"],
            name="fk_ratings_recipe_id_recipes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["catalog.users.id"],
            name="fk_ratings_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "recipe_id", name="pk_ratings"),
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_table("ratings", schema="catalog")
    op.drop_table("recipe_tags", schema="catalog")
    op.drop_table("tags", schema="catalog")
    op.drop_index("ix_instructions_recipe_id", table_name="instructions", schema="catalog")
    op.drop_table("instructions", schema="catalog")
    op.drop_index("ix_ingredients_recipe_id", table_name="ingredients", schema="catalog")
    op.drop_table("ingredients", schema="catalog")
    op.drop_index("ix_recipes_user_id", table_name="recipes", schema="catalog")
    op.drop_table("recipes", schema="catalog")
    op.drop_table("users", schema="catalog")
