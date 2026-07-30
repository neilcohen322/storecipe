import base64
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, Index
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.crypto import PayloadCipher
from ingestion.models import (
    Base,
    ImportDispatch,
    ImportJob,
    ImportPayload,
    ImportStage,
    ImportStatus,
)
from ingestion.repositories.imports import ImportRepository
from ingestion.services.imports import ActiveUrlImportExists, ImportService


class RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0

    def add_all(self, instances: list[object]) -> None:
        self.added.extend(instances)

    async def flush(self) -> None:
        self.flushes += 1


class ConcurrentIdempotencyWinnerRepository:
    def __init__(self, winner: ImportJob) -> None:
        self.session = self
        self._winner = winner
        self._idempotency_lookups = 0
        self._active_lookups = 0

    @asynccontextmanager
    async def transaction(self) -> Any:
        yield

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        yield

    async def get_owned_idempotency_job(
        self, owner_subject: str, idempotency_key: str
    ) -> ImportJob | None:
        self._idempotency_lookups += 1
        return self._winner if self._idempotency_lookups > 1 else None

    async def get_owned_active_url_job(
        self, owner_subject: str, request_fingerprint: str, *, for_update: bool = False
    ) -> ImportJob | None:
        self._active_lookups += 1
        return self._winner if self._active_lookups > 1 else None

    async def create_job(self, **kwargs: object) -> ImportJob:
        class AsyncpgUniqueViolation(Exception):
            constraint_name = "uq_import_jobs_owner_active_url_fingerprint"

        class AsyncpgAdapterError(Exception):
            sqlstate = "23505"

        adapter_error = AsyncpgAdapterError()
        adapter_error.__cause__ = AsyncpgUniqueViolation()
        raise IntegrityError("INSERT", {}, adapter_error)


def _cipher() -> PayloadCipher:
    keyring = base64.b64encode(b"c" * 32).decode()
    return PayloadCipher.from_keyring(active_key_id="current", keyring=f"current={keyring}")


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"ingestion": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        yield database_session
    await engine.dispose()


def test_jobs_enforce_unique_owner_idempotency_key_only_when_a_key_is_present() -> None:
    indexes = {index.name: index for index in ImportJob.__table__.indexes}

    index = indexes["uq_import_jobs_owner_idempotency_key"]

    assert isinstance(index, Index)
    assert tuple(column.name for column in index.columns) == (
        "owner_subject",
        "idempotency_key",
    )
    assert str(index.dialect_options["postgresql"]["where"]) == "idempotency_key IS NOT NULL"


def test_jobs_enforce_one_active_url_per_owner_and_fingerprint() -> None:
    indexes = {index.name: index for index in ImportJob.__table__.indexes}

    index = indexes["uq_import_jobs_owner_active_url_fingerprint"]

    assert isinstance(index, Index)
    assert tuple(column.name for column in index.columns) == (
        "owner_subject",
        "request_fingerprint",
    )
    assert str(index.dialect_options["postgresql"]["where"]) == (
        "input_kind = 'url' AND status IN ('queued', 'processing')"
    )
    assert str(index.dialect_options["sqlite"]["where"]) == (
        "input_kind = 'url' AND status IN ('queued', 'processing')"
    )


def test_unique_violation_detection_reads_the_asyncpg_cause_constraint() -> None:
    """Ignoring asyncpg's chained cause would re-raise a recoverable insert race."""

    class AsyncpgUniqueViolation(Exception):
        constraint_name = "uq_import_jobs_owner_active_url_fingerprint"

    class AsyncpgAdapterError(Exception):
        sqlstate = "23505"

    adapter_error = AsyncpgAdapterError()
    adapter_error.__cause__ = AsyncpgUniqueViolation()
    error = IntegrityError("INSERT", {}, adapter_error)

    assert ImportService._is_unique_violation(error, AsyncpgUniqueViolation.constraint_name)


@pytest.mark.asyncio
async def test_concurrent_active_index_loss_replays_a_matching_idempotency_winner(
    session: AsyncSession,
) -> None:
    """The active index reported first must not demote a same-key replay to 409."""

    url = "https://example.com/soup"
    fingerprint = sha256(b"url\0" + url.encode("utf-8")).hexdigest()
    winner = ImportJob(
        owner_subject="auth0|owner",
        input_kind="url",
        request_fingerprint=fingerprint,
        idempotency_key="same-url",
    )
    service = ImportService(session, _cipher())
    service._repository = ConcurrentIdempotencyWinnerRepository(winner)  # type: ignore[assignment]  # noqa: SLF001

    result = await service.submit_url(
        "auth0|owner",
        url,
        idempotency_key="same-url",
    )

    assert result.replayed
    assert result.job is winner


@pytest.mark.asyncio
async def test_same_active_url_without_idempotency_key_returns_existing_job(
    session: AsyncSession,
) -> None:
    """Removing the active URL uniqueness guard would create a second queued job."""

    service = ImportService(session, _cipher())
    first = await service.submit_url("auth0|owner", "https://example.com/soup")

    with pytest.raises(ActiveUrlImportExists) as captured:
        await service.submit_url("auth0|owner", "https://example.com/soup")

    assert captured.value.job.id == first.job.id


@pytest.mark.asyncio
async def test_same_url_for_another_owner_creates_a_distinct_active_job(
    session: AsyncSession,
) -> None:
    """Dropping owner scope would reject another owner's independent import."""

    service = ImportService(session, _cipher())
    first = await service.submit_url("auth0|first", "https://example.com/soup")
    second = await service.submit_url("auth0|second", "https://example.com/soup")

    assert first.job.id != second.job.id


@pytest.mark.asyncio
async def test_terminal_url_job_permits_a_later_submission(session: AsyncSession) -> None:
    """Including terminal statuses in the active guard would block a legitimate retry."""

    service = ImportService(session, _cipher())
    first = await service.submit_url("auth0|owner", "https://example.com/soup")
    first.job.status = ImportStatus.COMPLETED
    first.job.stage = ImportStage.COMPLETED
    await session.commit()

    second = await service.submit_url("auth0|owner", "https://example.com/soup")

    assert second.job.id != first.job.id


@pytest.mark.asyncio
async def test_same_text_without_idempotency_key_creates_distinct_jobs(
    session: AsyncSession,
) -> None:
    """Applying the active URL guard to text would collapse independent text imports."""

    service = ImportService(session, _cipher())
    first = await service.submit_text("auth0|owner", "Lentil soup")
    second = await service.submit_text("auth0|owner", "Lentil soup")

    assert first.job.id != second.job.id


@pytest.mark.asyncio
async def test_creating_a_job_adds_one_safe_initial_dispatch_generation() -> None:
    session = RecordingSession()
    repository = ImportRepository(session)  # type: ignore[arg-type]

    job = await repository.create_job(
        owner_subject="auth0|owner",
        input_kind="url",
        request_fingerprint="a" * 64,
        plaintext_input=b"https://example.com/recipe",
        payload_cipher=_cipher(),
        idempotency_key="same-request",
    )

    dispatches = [item for item in session.added if isinstance(item, ImportDispatch)]
    assert session.flushes == 1
    assert len(dispatches) == 1
    assert dispatches[0].job_id == job.id
    assert dispatches[0].generation == 1
    assert dispatches[0].due_at <= datetime.now(UTC)
    assert {column.name for column in ImportDispatch.__table__.columns}.isdisjoint(
        {"url", "text", "plaintext", "ciphertext", "payload"}
    )


@pytest.mark.asyncio
async def test_creating_same_plaintext_uses_a_stable_pre_encryption_content_hash() -> None:
    plaintext = b"same recipe input"
    first_session = RecordingSession()
    second_session = RecordingSession()

    first_job = await ImportRepository(first_session).create_job(  # type: ignore[arg-type]
        owner_subject="auth0|first",
        input_kind="text",
        request_fingerprint="c" * 64,
        plaintext_input=plaintext,
        payload_cipher=_cipher(),
    )
    second_job = await ImportRepository(second_session).create_job(  # type: ignore[arg-type]
        owner_subject="auth0|second",
        input_kind="text",
        request_fingerprint="d" * 64,
        plaintext_input=plaintext,
        payload_cipher=_cipher(),
    )

    first_payload = next(item for item in first_session.added if isinstance(item, ImportPayload))
    second_payload = next(item for item in second_session.added if isinstance(item, ImportPayload))
    expected_hash = sha256(plaintext).hexdigest()
    assert first_payload.nonce != second_payload.nonce
    assert first_payload.ciphertext != second_payload.ciphertext
    assert first_job.input_content_hash == expected_hash
    assert second_job.input_content_hash == expected_hash


def test_new_jobs_start_queued_with_zero_counters_and_fenced_lease_fields() -> None:
    job = ImportJob(
        owner_subject="auth0|owner",
        input_kind="text",
        request_fingerprint="b" * 64,
    )

    assert job.status is ImportStatus.QUEUED
    assert job.stage is ImportStage.QUEUED
    assert job.attempt_count == 0
    assert job.dispatch_count == 0
    assert job.receipt_count == 0
    assert job.fetch_count == 0
    assert job.provider_count == 0
    assert job.catalog_count == 0
    assert job.lease_generation == 0
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    timestamp_columns = (job.__table__.c.created_at, job.__table__.c.updated_at)
    assert all(column.type.timezone for column in timestamp_columns)
    constraints = [
        constraint
        for constraint in job.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert any(
        constraint.name == "ck_import_jobs_nonnegative_counters" for constraint in constraints
    )
    assert isinstance(job.id, UUID)
