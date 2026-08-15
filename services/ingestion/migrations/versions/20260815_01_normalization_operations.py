"""Add governed ingredient-normalization operations and shared LLM budget ownership.

Revision ID: 20260815_01
Revises: 20260809_01
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260815_01"
down_revision: str | None = "20260809_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ingestion"

llm_operation_kind = postgresql.ENUM(
    "import_extraction",
    "ingredient_normalization",
    name="llm_operation_kind",
    schema=SCHEMA,
    create_type=False,
)

ingredient_normalization_operation_state = postgresql.ENUM(
    "pending",
    "completed",
    "failed",
    name="ingredient_normalization_operation_state",
    schema=SCHEMA,
    create_type=False,
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


def upgrade() -> None:
    llm_operation_kind.create(op.get_bind(), checkfirst=True)
    ingredient_normalization_operation_state.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ingredient_normalization_operations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            ingredient_normalization_operation_state,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("result_encryption_key_id", sa.String(length=128), nullable=True),
        sa.Column("result_algorithm", sa.String(length=32), nullable=True),
        sa.Column("result_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("result_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("result_content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_subject",
            "idempotency_key",
            name="uq_ingredient_normalization_operations_owner_idempotency",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "ingredient_normalization_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("normalization_operation_id", sa.UUID(), nullable=False),
        sa.Column("operation_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("state", provider_attempt_state, nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_category", sa.String(length=128), nullable=True),
        sa.Column("provider_name", sa.String(length=128), nullable=True),
        sa.Column("model_name", sa.String(length=256), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_microunits", sa.Integer(), nullable=True),
        sa.CheckConstraint("ordinal > 0", name="ck_ingredient_normalization_attempts_ordinal"),
        sa.ForeignKeyConstraint(
            ["normalization_operation_id"],
            ["ingestion.ingredient_normalization_operations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id", name="uq_ingredient_normalization_attempts_operation_id"
        ),
        sa.UniqueConstraint(
            "normalization_operation_id",
            "ordinal",
            name="uq_ingredient_normalization_attempts_operation_ordinal",
        ),
        schema=SCHEMA,
    )

    op.add_column(
        "llm_invocations",
        sa.Column(
            "operation_kind",
            llm_operation_kind,
            server_default=sa.text("'import_extraction'"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.drop_constraint(
        "llm_invocations_provider_operation_id_fkey",
        "llm_invocations",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.alter_column(
        "llm_invocations",
        "job_id",
        existing_type=sa.UUID(),
        nullable=True,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_llm_invocations_operation_kind_job",
        "llm_invocations",
        "(operation_kind = 'import_extraction' AND job_id IS NOT NULL) "
        "OR (operation_kind = 'ingredient_normalization' AND job_id IS NULL)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_llm_invocations_operation_kind_job",
        "llm_invocations",
        schema=SCHEMA,
        type_="check",
    )
    op.alter_column(
        "llm_invocations",
        "job_id",
        existing_type=sa.UUID(),
        nullable=False,
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "llm_invocations_provider_operation_id_fkey",
        "llm_invocations",
        "provider_attempts",
        ["provider_operation_id"],
        ["operation_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.drop_column("llm_invocations", "operation_kind", schema=SCHEMA)
    op.drop_table("ingredient_normalization_attempts", schema=SCHEMA)
    op.drop_table("ingredient_normalization_operations", schema=SCHEMA)
    ingredient_normalization_operation_state.drop(op.get_bind(), checkfirst=True)
    llm_operation_kind.drop(op.get_bind(), checkfirst=True)
