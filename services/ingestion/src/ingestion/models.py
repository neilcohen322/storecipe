"""Durable SQLAlchemy models for the import orchestration workflow."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "ingestion"


class Base(DeclarativeBase):
    """Declarative metadata owned by the ingestion service."""


class ImportStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ImportStage(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    EXTRACTING = "extracting"
    MODEL_EXTRACTING = "model_extracting"
    VALIDATING = "validating"
    CATALOG_PENDING = "catalog_pending"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ImportInputKind(StrEnum):
    URL = "url"
    TEXT = "text"


class DispatchType(StrEnum):
    PROCESS = "process"


class AttemptState(StrEnum):
    RESERVED = "reserved"
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class LlmInvocationState(StrEnum):
    RESERVED = "reserved"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class LlmOperationKind(StrEnum):
    IMPORT_EXTRACTION = "import_extraction"
    INGREDIENT_NORMALIZATION = "ingredient_normalization"


class IngredientNormalizationOperationState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


def _enum(enum: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        schema=SCHEMA,
        native_enum=True,
        create_constraint=True,
        values_callable=lambda enum_type: [item.value for item in enum_type],
    )


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        Index(
            "uq_import_jobs_owner_idempotency_key",
            "owner_subject",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_import_jobs_owner_active_url_fingerprint",
            "owner_subject",
            "request_fingerprint",
            unique=True,
            postgresql_where=text("input_kind = 'url' AND status IN ('queued', 'processing')"),
            sqlite_where=text("input_kind = 'url' AND status IN ('queued', 'processing')"),
        ),
        CheckConstraint(
            "attempt_count >= 0 AND dispatch_count >= 0 AND receipt_count >= 0 "
            "AND fetch_count >= 0 AND provider_count >= 0 AND catalog_count >= 0 "
            "AND lease_generation >= 0 AND dispatch_generation >= 0",
            name="ck_import_jobs_nonnegative_counters",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    input_kind: Mapped[ImportInputKind] = mapped_column(_enum(ImportInputKind, "import_input_kind"))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_content_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_content_hash: Mapped[str | None] = mapped_column(String(64))
    variant_fetch_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    variant_content_hash: Mapped[str | None] = mapped_column(String(64))
    variant_outcome_category: Mapped[str | None] = mapped_column(String(128))
    candidate_content_hash: Mapped[str | None] = mapped_column(String(64))
    model_content_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ImportStatus] = mapped_column(
        _enum(ImportStatus, "import_status"),
        nullable=False,
        default=ImportStatus.QUEUED,
        server_default=text("'queued'"),
    )
    stage: Mapped[ImportStage] = mapped_column(
        _enum(ImportStage, "import_stage"),
        nullable=False,
        default=ImportStage.QUEUED,
        server_default=text("'queued'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    dispatch_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    dispatch_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    receipt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    fetch_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    provider_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    catalog_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    safe_error_category: Mapped[str | None] = mapped_column(String(128))
    diagnostic_reference: Mapped[str | None] = mapped_column(String(128))
    catalog_recipe_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    catalog_pending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    payloads: Mapped[list["ImportPayload"]] = relationship(back_populates="job")
    dispatches: Mapped[list["ImportDispatch"]] = relationship(back_populates="job")
    provider_attempts: Mapped[list["ProviderAttempt"]] = relationship(back_populates="job")
    catalog_attempts: Mapped[list["CatalogAttempt"]] = relationship(back_populates="job")
    llm_invocations: Mapped[list["LlmInvocation"]] = relationship(back_populates="job")

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("id", uuid4())
        kwargs.setdefault("status", ImportStatus.QUEUED)
        kwargs.setdefault("stage", ImportStage.QUEUED)
        kwargs.setdefault("lease_generation", 0)
        kwargs.setdefault("dispatch_generation", 1)
        kwargs.setdefault("attempt_count", 0)
        kwargs.setdefault("dispatch_count", 0)
        kwargs.setdefault("receipt_count", 0)
        kwargs.setdefault("fetch_count", 0)
        kwargs.setdefault("provider_count", 0)
        kwargs.setdefault("catalog_count", 0)
        super().__init__(**kwargs)


class ImportPayload(Base):
    __tablename__ = "import_payloads"
    __table_args__ = (
        UniqueConstraint("job_id", "payload_type", name="uq_import_payloads_job_payload_type"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.import_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    job: Mapped[ImportJob] = relationship(back_populates="payloads")


class ImportDispatch(Base):
    __tablename__ = "import_dispatches"
    __table_args__ = (
        UniqueConstraint("job_id", "generation", name="uq_import_dispatches_job_generation"),
        CheckConstraint(
            "generation > 0 AND publication_attempts >= 0",
            name="ck_import_dispatches_counts",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.import_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    dispatch_type: Mapped[DispatchType] = mapped_column(
        _enum(DispatchType, "import_dispatch_type"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    publication_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped[ImportJob] = relationship(back_populates="dispatches")


class ProviderAttempt(Base):
    __tablename__ = "provider_attempts"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_provider_attempts_operation_id"),
        UniqueConstraint("job_id", "ordinal", name="uq_provider_attempts_job_ordinal"),
        CheckConstraint("ordinal > 0", name="ck_provider_attempts_ordinal"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.import_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, default=uuid4)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[AttemptState] = mapped_column(
        _enum(AttemptState, "provider_attempt_state"), nullable=False
    )
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_category: Mapped[str | None] = mapped_column(String(128))
    provider_name: Mapped[str | None] = mapped_column(String(128))
    model_name: Mapped[str | None] = mapped_column(String(256))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_microunits: Mapped[int | None] = mapped_column(Integer)

    job: Mapped[ImportJob] = relationship(back_populates="provider_attempts")


class IngredientNormalizationOperation(Base):
    __tablename__ = "ingredient_normalization_operations"
    __table_args__ = (
        UniqueConstraint(
            "owner_subject",
            "idempotency_key",
            name="uq_ingredient_normalization_operations_owner_idempotency",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[IngredientNormalizationOperationState] = mapped_column(
        _enum(IngredientNormalizationOperationState, "ingredient_normalization_operation_state"),
        nullable=False,
        default=IngredientNormalizationOperationState.PENDING,
        server_default=text("'pending'"),
    )
    result_encryption_key_id: Mapped[str | None] = mapped_column(String(128))
    result_algorithm: Mapped[str | None] = mapped_column(String(32))
    result_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    result_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    result_content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    attempts: Mapped[list["IngredientNormalizationAttempt"]] = relationship(
        back_populates="operation"
    )


class IngredientNormalizationAttempt(Base):
    __tablename__ = "ingredient_normalization_attempts"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_ingredient_normalization_attempts_operation_id"),
        UniqueConstraint(
            "normalization_operation_id",
            "ordinal",
            name="uq_ingredient_normalization_attempts_operation_ordinal",
        ),
        CheckConstraint("ordinal > 0", name="ck_ingredient_normalization_attempts_ordinal"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    normalization_operation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.ingredient_normalization_operations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, default=uuid4)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[AttemptState] = mapped_column(
        _enum(AttemptState, "provider_attempt_state"), nullable=False
    )
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_category: Mapped[str | None] = mapped_column(String(128))
    provider_name: Mapped[str | None] = mapped_column(String(128))
    model_name: Mapped[str | None] = mapped_column(String(256))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_microunits: Mapped[int | None] = mapped_column(Integer)

    operation: Mapped[IngredientNormalizationOperation] = relationship(back_populates="attempts")


class AiDailyUsage(Base):
    __tablename__ = "ai_daily_usage"
    __table_args__ = (
        CheckConstraint(
            "reserved_tokens >= 0 AND consumed_tokens >= 0",
            name="ck_ai_daily_usage_nonnegative_tokens",
        ),
        {"schema": SCHEMA},
    )

    owner_subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    budget_date_utc: Mapped[date] = mapped_column(primary_key=True)
    reserved_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    consumed_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LlmInvocation(Base):
    __tablename__ = "llm_invocations"
    __table_args__ = (
        UniqueConstraint("provider_operation_id", name="uq_llm_invocations_provider_operation"),
        Index("ix_llm_invocations_owner_budget_date", "owner_subject", "budget_date_utc"),
        Index("ix_llm_invocations_state_request_deadline", "state", "request_deadline_at"),
        CheckConstraint("reserved_tokens >= 0", name="ck_llm_invocations_reserved_tokens"),
        CheckConstraint("input_tokens >= 0", name="ck_llm_invocations_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_llm_invocations_output_tokens"),
        CheckConstraint("total_tokens >= 0", name="ck_llm_invocations_total_tokens"),
        CheckConstraint("cost_microunits >= 0", name="ck_llm_invocations_cost_microunits"),
        CheckConstraint("latency_ms >= 0", name="ck_llm_invocations_latency_ms"),
        CheckConstraint(
            "input_tokens IS NULL OR output_tokens IS NULL OR total_tokens IS NULL "
            "OR total_tokens = input_tokens + output_tokens",
            name="ck_llm_invocations_total_tokens_match",
        ),
        CheckConstraint(
            "(operation_kind = 'import_extraction' AND job_id IS NOT NULL) "
            "OR (operation_kind = 'ingredient_normalization' AND job_id IS NULL)",
            name="ck_llm_invocations_operation_kind_job",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.import_jobs.id", ondelete="CASCADE"),
        nullable=True,
    )
    provider_operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_kind: Mapped[LlmOperationKind] = mapped_column(
        _enum(LlmOperationKind, "llm_operation_kind"),
        nullable=False,
        default=LlmOperationKind.IMPORT_EXTRACTION,
        server_default=text("'import_extraction'"),
    )
    owner_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    budget_date_utc: Mapped[date] = mapped_column(nullable=False)
    state: Mapped[LlmInvocationState] = mapped_column(
        _enum(LlmInvocationState, "llm_invocation_state"), nullable=False
    )
    provider_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_microunits: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_category: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    job: Mapped[ImportJob | None] = relationship(back_populates="llm_invocations")


class CatalogAttempt(Base):
    __tablename__ = "catalog_attempts"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_catalog_attempts_operation_id"),
        UniqueConstraint("job_id", "ordinal", name="uq_catalog_attempts_job_ordinal"),
        CheckConstraint("ordinal > 0", name="ck_catalog_attempts_ordinal"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.import_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, default=uuid4)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[AttemptState] = mapped_column(
        _enum(AttemptState, "catalog_attempt_state"), nullable=False
    )
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_category: Mapped[str | None] = mapped_column(String(128))
    catalog_recipe_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    job: Mapped[ImportJob] = relationship(back_populates="catalog_attempts")
