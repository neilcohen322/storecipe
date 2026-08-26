"""Require a committed journal marker before claiming deletion jobs.

Revision ID: 20260826_01
Revises: 20260824_02
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_01"
down_revision: str | None = "20260824_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_deletions",
        sa.Column(
            "journal_committed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_column("account_deletions", "journal_committed", schema="catalog")
