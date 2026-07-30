"""Add durable daily AI budget and invocation ledger tables.

Revision ID: 20260729_02
Revises: 20260729_01
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_02"
down_revision: str | None = "20260729_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ingestion"

llm_invocation_state = postgresql.ENUM(
    "reserved",
    "succeeded",
    "failed",
    "ambiguous",
    name="llm_invocation_state",
    schema=SCHEMA,
    create_type=False,
)


def upgrade() -> None:
    llm_invocation_state.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "ai_daily_usage",
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("budget_date_utc", sa.Date(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consumed_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "reserved_tokens >= 0 AND consumed_tokens >= 0",
            name="ck_ai_daily_usage_nonnegative_tokens",
        ),
        sa.PrimaryKeyConstraint("owner_subject", "budget_date_utc"),
        schema=SCHEMA,
    )
    op.create_table(
        "llm_invocations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("provider_operation_id", sa.UUID(), nullable=False),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("budget_date_utc", sa.Date(), nullable=False),
        sa.Column("state", llm_invocation_state, nullable=False),
        sa.Column("provider_name", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_microunits", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("request_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_category", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_llm_invocations_reserved_tokens"),
        sa.CheckConstraint("input_tokens >= 0", name="ck_llm_invocations_input_tokens"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_llm_invocations_output_tokens"),
        sa.CheckConstraint("total_tokens >= 0", name="ck_llm_invocations_total_tokens"),
        sa.CheckConstraint("cost_microunits >= 0", name="ck_llm_invocations_cost_microunits"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_llm_invocations_latency_ms"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR output_tokens IS NULL OR total_tokens IS NULL "
            "OR total_tokens = input_tokens + output_tokens",
            name="ck_llm_invocations_total_tokens_match",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion.import_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_operation_id"],
            ["ingestion.provider_attempts.operation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_operation_id", name="uq_llm_invocations_provider_operation"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_invocations_owner_budget_date",
        "llm_invocations",
        ["owner_subject", "budget_date_utc"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_invocations_state_request_deadline",
        "llm_invocations",
        ["state", "request_deadline_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_invocations_state_request_deadline", table_name="llm_invocations", schema=SCHEMA
    )
    op.drop_index(
        "ix_llm_invocations_owner_budget_date", table_name="llm_invocations", schema=SCHEMA
    )
    op.drop_table("llm_invocations", schema=SCHEMA)
    op.drop_table("ai_daily_usage", schema=SCHEMA)
    llm_invocation_state.drop(op.get_bind(), checkfirst=True)
