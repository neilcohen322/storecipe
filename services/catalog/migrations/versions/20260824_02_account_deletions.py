"""Add durable account-deletion jobs and tombstones.

Revision ID: 20260824_02
Revises: 20260824_01
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_02"
down_revision: str | None = "20260824_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_deletions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("auth_subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("catalog_user_id", sa.Uuid(), nullable=True),
        sa.Column("media_snapshot", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed')",
            name="ck_account_deletions_account_deletions_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("auth_subject", name="uq_account_deletions_auth_subject"),
        schema="catalog",
    )
    op.create_index(
        "ix_account_deletions_due",
        "account_deletions",
        ["status", "next_attempt_at"],
        unique=False,
        schema="catalog",
        postgresql_where=sa.text("status <> 'completed'"),
    )
    op.create_index(
        "ix_account_deletions_expiry",
        "account_deletions",
        ["expires_at"],
        unique=False,
        schema="catalog",
        postgresql_where=sa.text("status = 'completed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_account_deletions_expiry", table_name="account_deletions", schema="catalog")
    op.drop_index("ix_account_deletions_due", table_name="account_deletions", schema="catalog")
    op.drop_table("account_deletions", schema="catalog")
