"""Add durable import orchestration persistence.

Revision ID: 20260725_01
Revises:
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260725_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ingestion"

import_status = postgresql.ENUM(
    "queued",
    "processing",
    "completed",
    "review_required",
    "failed",
    "cancelled",
    "timed_out",
    name="import_status",
    schema=SCHEMA,
    create_type=False,
)
import_stage = postgresql.ENUM(
    "queued",
    "fetching",
    "extracting",
    "model_extracting",
    "validating",
    "catalog_pending",
    "completed",
    "review_required",
    "failed",
    "cancelled",
    "timed_out",
    name="import_stage",
    schema=SCHEMA,
    create_type=False,
)
import_input_kind = postgresql.ENUM(
    "url", "text", name="import_input_kind", schema=SCHEMA, create_type=False
)
import_dispatch_type = postgresql.ENUM(
    "process", name="import_dispatch_type", schema=SCHEMA, create_type=False
)
provider_attempt_state = postgresql.ENUM(
    "reserved",
    "in_flight",
    "succeeded",
    "failed",
    "ambiguous",
    name="provider_attempt_state",
    schema=SCHEMA,
    create_type=False,
)
catalog_attempt_state = postgresql.ENUM(
    "reserved",
    "in_flight",
    "succeeded",
    "failed",
    "ambiguous",
    name="catalog_attempt_state",
    schema=SCHEMA,
    create_type=False,
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    bind = op.get_bind()
    for enum in (
        import_status,
        import_stage,
        import_input_kind,
        import_dispatch_type,
        provider_attempt_state,
        catalog_attempt_state,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("input_kind", import_input_kind, nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_content_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_content_hash", sa.String(length=64), nullable=True),
        sa.Column("candidate_content_hash", sa.String(length=64), nullable=True),
        sa.Column("model_content_hash", sa.String(length=64), nullable=True),
        sa.Column("status", import_status, server_default=sa.text("'queued'"), nullable=False),
        sa.Column("stage", import_stage, server_default=sa.text("'queued'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dispatch_generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dispatch_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("receipt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fetch_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("provider_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("catalog_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("safe_error_category", sa.String(length=128), nullable=True),
        sa.Column("diagnostic_reference", sa.String(length=128), nullable=True),
        sa.Column("catalog_recipe_id", sa.UUID(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("catalog_pending_since", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0 AND dispatch_count >= 0 AND receipt_count >= 0 "
            "AND fetch_count >= 0 AND provider_count >= 0 AND catalog_count >= 0 "
            "AND lease_generation >= 0 AND dispatch_generation >= 0",
            name="ck_import_jobs_nonnegative_counters",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_import_jobs_owner_idempotency_key",
        "import_jobs",
        ["owner_subject", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "import_payloads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("payload_type", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion.import_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "payload_type", name="uq_import_payloads_job_payload_type"),
        schema=SCHEMA,
    )
    op.create_table(
        "import_dispatches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("dispatch_type", import_dispatch_type, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "publication_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "generation > 0 AND publication_attempts >= 0", name="ck_import_dispatches_counts"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion.import_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "generation", name="uq_import_dispatches_job_generation"),
        schema=SCHEMA,
    )
    for table_name, state_type in (
        ("provider_attempts", provider_attempt_state),
        ("catalog_attempts", catalog_attempt_state),
    ):
        extra_columns: list[sa.Column[object]] = []
        if table_name == "provider_attempts":
            extra_columns = [
                sa.Column("provider_name", sa.String(length=128), nullable=True),
                sa.Column("model_name", sa.String(length=256), nullable=True),
                sa.Column("input_tokens", sa.Integer(), nullable=True),
                sa.Column("output_tokens", sa.Integer(), nullable=True),
                sa.Column("cost_microunits", sa.Integer(), nullable=True),
            ]
        else:
            extra_columns = [sa.Column("catalog_recipe_id", sa.UUID(), nullable=True)]
        op.create_table(
            table_name,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("job_id", sa.UUID(), nullable=False),
            sa.Column("operation_id", sa.UUID(), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("state", state_type, nullable=False),
            sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("request_deadline_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome_category", sa.String(length=128), nullable=True),
            *extra_columns,
            sa.CheckConstraint("ordinal > 0", name=f"ck_{table_name}_ordinal"),
            sa.ForeignKeyConstraint(["job_id"], ["ingestion.import_jobs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("operation_id", name=f"uq_{table_name}_operation_id"),
            sa.UniqueConstraint("job_id", "ordinal", name=f"uq_{table_name}_job_ordinal"),
            schema=SCHEMA,
        )


def downgrade() -> None:
    op.drop_table("catalog_attempts", schema=SCHEMA)
    op.drop_table("provider_attempts", schema=SCHEMA)
    op.drop_table("import_dispatches", schema=SCHEMA)
    op.drop_table("import_payloads", schema=SCHEMA)
    op.drop_index("uq_import_jobs_owner_idempotency_key", table_name="import_jobs", schema=SCHEMA)
    op.drop_table("import_jobs", schema=SCHEMA)
    bind = op.get_bind()
    for enum in (
        catalog_attempt_state,
        provider_attempt_state,
        import_dispatch_type,
        import_input_kind,
        import_stage,
        import_status,
    ):
        enum.drop(bind, checkfirst=True)
