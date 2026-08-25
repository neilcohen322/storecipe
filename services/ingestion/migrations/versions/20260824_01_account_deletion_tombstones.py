"""Add durable 90-day account-subject deletion tombstones.

Revision ID: 20260824_01
Revises: 20260815_01
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_01"
down_revision: str | None = "20260815_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ingestion"


def upgrade() -> None:
    op.create_table(
        "account_deletion_tombstones",
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_at > deleted_at",
            name="ck_account_deletion_tombstones_retention_window",
        ),
        sa.PrimaryKeyConstraint("subject"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_account_deletion_tombstones_expires_at",
        "account_deletion_tombstones",
        ["expires_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_deletion_tombstones_expires_at",
        table_name="account_deletion_tombstones",
        schema=SCHEMA,
    )
    op.drop_table("account_deletion_tombstones", schema=SCHEMA)
