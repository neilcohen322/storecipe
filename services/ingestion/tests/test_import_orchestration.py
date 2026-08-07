from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.models import (
    AttemptState,
    Base,
    DispatchType,
    ImportDispatch,
    ImportInputKind,
    ImportJob,
    ImportStage,
    ImportStatus,
)
from ingestion.orchestration import StaleLease
from ingestion.repositories.imports import ImportRepository


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


async def _new_job(session: AsyncSession) -> UUID:
    job = ImportJob(
        owner_subject="auth0|owner",
        input_kind=ImportInputKind.TEXT,
        request_fingerprint="a" * 64,
    )
    session.add(job)
    session.add(
        ImportDispatch(
            job_id=job.id,
            generation=1,
            dispatch_type=DispatchType.PROCESS,
            due_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return job.id


async def _job(session: AsyncSession, job_id: UUID) -> ImportJob:
    job = await session.get(ImportJob, job_id)
    assert job is not None
    return job


def _as_utc(timestamp: datetime) -> datetime:
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)


def test_postgresql_fenced_paths_use_a_wall_clock_statement() -> None:
    """Changing the shared fence clock to transaction-start `now()` would admit expired leases."""

    statement = ImportRepository._fence_clock_statement()

    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "clock_timestamp()" in compiled
    assert "now()" not in compiled


@pytest.mark.asyncio
async def test_claim_excludes_a_concurrent_worker_and_recovers_with_a_new_generation(
    session: AsyncSession,
) -> None:
    """Removing the unexpired-lease guard would let both workers own one attempt."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)

    first = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1)
    excluded = await orchestration.record_receipt_and_claim(job_id, "worker-b", 1)

    assert first is not None
    assert excluded is None
    assert first.generation == 1
    claimed = await _job(session, job_id)
    assert claimed.attempt_count == 1
    assert claimed.receipt_count == 1

    await session.execute(
        update(ImportJob)
        .where(ImportJob.id == job_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()

    recovered = await orchestration.record_receipt_and_claim(job_id, "worker-b", 1)

    assert recovered is not None
    assert recovered.generation == 2
    assert (await _job(session, job_id)).attempt_count == 2
    assert (await _job(session, job_id)).receipt_count == 1


@pytest.mark.asyncio
async def test_only_a_received_dispatch_closes_the_worker_receipt(session: AsyncSession) -> None:
    """Changing receipt closure to publication would count a publisher action as worker receipt."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)
    dispatch = await session.scalar(select(ImportDispatch).where(ImportDispatch.job_id == job_id))
    assert dispatch is not None
    dispatch.published_at = datetime.now(UTC)
    await session.commit()

    token = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1)

    assert token is not None
    dispatch = await session.scalar(select(ImportDispatch).where(ImportDispatch.job_id == job_id))
    assert dispatch is not None
    assert dispatch.published_at is not None
    assert dispatch.received_at is not None
    assert (await _job(session, job_id)).receipt_count == 1


@pytest.mark.asyncio
async def test_renewal_extends_a_live_lease_without_changing_its_generation(
    session: AsyncSession,
) -> None:
    """Replacing lease renewal with a no-op would make a worker lose its bounded lease early."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)
    token = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1, lease_seconds=10)
    assert token is not None

    renewed = await orchestration.renew_lease(token, lease_seconds=120)

    assert renewed.job_id == token.job_id
    assert renewed.owner == token.owner
    assert renewed.generation == token.generation
    assert _as_utc(renewed.expires_at) > _as_utc(token.expires_at)


@pytest.mark.asyncio
async def test_fenced_mutations_reject_stale_workers_and_terminal_jobs(
    session: AsyncSession,
) -> None:
    """Dropping fences would let stale workers change a completed job."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)
    token = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1)
    assert token is not None

    with pytest.raises(StaleLease):
        await orchestration.advance_stage(
            token.__class__(job_id, "worker-b", token.generation, token.expires_at),
            ImportStage.FETCHING,
            checkpoint_content_hash=None,
        )

    assert await orchestration.finish_terminal(
        token,
        ImportStatus.COMPLETED,
        error_category=None,
        diagnostic_reference=None,
    )
    with pytest.raises(StaleLease):
        await orchestration.renew_lease(token)
    assert (await _job(session, job_id)).status is ImportStatus.COMPLETED


@pytest.mark.asyncio
async def test_stage_checkpoints_are_persisted_with_legal_progression(
    session: AsyncSession,
) -> None:
    """Removing the checkpoint write would lose fetch work after a later retry."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)
    token = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1)
    assert token is not None

    assert await orchestration.advance_stage(
        token, ImportStage.FETCHING, checkpoint_content_hash=None
    )
    assert await orchestration.advance_stage(
        token,
        ImportStage.EXTRACTING,
        checkpoint_content_hash="f" * 64,
    )

    job = await _job(session, job_id)
    assert job.stage is ImportStage.EXTRACTING
    assert job.fetched_content_hash == "f" * 64


@pytest.mark.asyncio
async def test_retry_atomically_fences_the_old_worker_and_creates_the_next_dispatch(
    session: AsyncSession,
) -> None:
    """Separating the retry update from outbox insertion could strand a durable retry."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)
    token = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1)
    assert token is not None
    due_at = datetime.now(UTC) + timedelta(minutes=2)

    assert await orchestration.schedule_retry(token, due_at, error_category="upstream_timeout")

    job = await _job(session, job_id)
    dispatches = list(
        await session.scalars(
            select(ImportDispatch)
            .where(ImportDispatch.job_id == job_id)
            .order_by(ImportDispatch.generation)
        )
    )
    assert job.next_attempt_at is not None
    assert _as_utc(job.next_attempt_at) == due_at
    assert job.dispatch_generation == 2
    assert job.attempt_count == 1
    assert [item.generation for item in dispatches] == [1, 2]
    assert _as_utc(dispatches[1].due_at) == due_at
    with pytest.raises(StaleLease):
        await orchestration.advance_stage(token, ImportStage.FETCHING, checkpoint_content_hash=None)


@pytest.mark.asyncio
async def test_cancellation_blocks_retry_and_catalog_reservation(session: AsyncSession) -> None:
    """Removing cancellation predicates would resurrect a job after cancellation won the race."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)
    token = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1)
    assert token is not None
    await session.execute(
        update(ImportJob)
        .where(ImportJob.id == job_id)
        .values(cancel_requested_at=datetime.now(UTC))
    )
    await session.commit()

    assert not await orchestration.schedule_retry(
        token, datetime.now(UTC) + timedelta(minutes=1), error_category=None
    )
    assert not await orchestration.reserve_catalog_intent(token)


@pytest.mark.asyncio
async def test_catalog_intent_reserves_one_idempotent_attempt(session: AsyncSession) -> None:
    """Removing the catalog-pending guard would reserve duplicate catalog operations."""

    job_id = await _new_job(session)
    orchestration = ImportRepository(session)
    token = await orchestration.record_receipt_and_claim(job_id, "worker-a", 1)
    assert token is not None
    assert await orchestration.advance_stage(
        token, ImportStage.FETCHING, checkpoint_content_hash=None
    )
    assert await orchestration.advance_stage(
        token, ImportStage.EXTRACTING, checkpoint_content_hash=None
    )
    assert await orchestration.advance_stage(
        token, ImportStage.MODEL_EXTRACTING, checkpoint_content_hash=None
    )
    assert await orchestration.advance_stage(
        token, ImportStage.VALIDATING, checkpoint_content_hash=None
    )

    intent = await orchestration.reserve_catalog_intent(token)

    assert intent is not None
    assert intent.ordinal == 1
    assert intent.state is AttemptState.RESERVED
    assert not await orchestration.reserve_catalog_intent(token)
    job = await _job(session, job_id)
    assert job.stage is ImportStage.CATALOG_PENDING
    assert job.catalog_count == 1
