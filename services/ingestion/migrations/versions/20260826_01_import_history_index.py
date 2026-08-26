"""Add owner history pagination index for import jobs.

Revision ID: 20260826_01
Revises: 20260824_01
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260826_01"
down_revision: str | None = "20260824_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ingestion"


def upgrade() -> None:
    op.create_index(
        "ix_import_jobs_owner_history",
        "import_jobs",
        ["owner_subject", "created_at", "id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_import_jobs_owner_history",
        table_name="import_jobs",
        schema=SCHEMA,
    )
