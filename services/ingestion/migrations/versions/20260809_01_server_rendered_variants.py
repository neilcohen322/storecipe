"""Add durable server-rendered variant fetch metadata.

Revision ID: 20260809_01
Revises: 20260729_02
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_01"
down_revision: str | None = "20260729_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ingestion"


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column("variant_fetch_attempted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_jobs",
        sa.Column("variant_content_hash", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_jobs",
        sa.Column("variant_outcome_category", sa.String(length=128), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "variant_outcome_category", schema=SCHEMA)
    op.drop_column("import_jobs", "variant_content_hash", schema=SCHEMA)
    op.drop_column("import_jobs", "variant_fetch_attempted_at", schema=SCHEMA)
