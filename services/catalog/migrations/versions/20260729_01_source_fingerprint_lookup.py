"""Add lookup index for recipe source fingerprints.

Revision ID: 20260729_01
Revises: 20260714_01
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_01"
down_revision: str | None = "20260714_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_recipes_user_source_fingerprint",
        "recipes",
        ["user_id", "source_fingerprint"],
        unique=False,
        schema="catalog",
        postgresql_where=sa.text("source_fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recipes_user_source_fingerprint",
        table_name="recipes",
        schema="catalog",
    )
