"""Prevent concurrent duplicate active URL imports.

Revision ID: 20260729_01
Revises: 20260725_01
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_01"
down_revision: str | None = "20260725_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ingestion"
INDEX_NAME = "uq_import_jobs_owner_active_url_fingerprint"
ACTIVE_URL_PREDICATE = "input_kind = 'url' AND status IN ('queued', 'processing')"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "import_jobs",
        ["owner_subject", "request_fingerprint"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(ACTIVE_URL_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="import_jobs", schema=SCHEMA)
