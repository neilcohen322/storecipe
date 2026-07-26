"""Durable SQLAlchemy models for the import orchestration workflow."""

from datetime import datetime
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
