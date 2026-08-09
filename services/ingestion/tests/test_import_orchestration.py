import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.crypto import PayloadCipher
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
from ingestion.orchestration import LeaseToken, StaleLease
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


def cipher() -> PayloadCipher:
    return PayloadCipher(
        active_key_id="test",
        keys={"test": base64.b64decode("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")},
    )


async def claimed_extracting_job(
    session: AsyncSession,
) -> tuple[ImportRepository, LeaseToken, ImportJob]:
    repository = ImportRepository(session)
    target = await repository.create_job(
        owner_subject="auth0|owner",
        input_kind=ImportInputKind.URL,
        request_fingerprint="a" * 64,
        plaintext_input=b"https://www.publisher.test/recipe/a",
        payload_cipher=cipher(),
    )
    await session.commit()
    token = await repository.record_receipt_and_claim(target.id, "worker", 1, lease_seconds=60)
    assert token is not None
    assert await repository.advance_stage(token, ImportStage.FETCHING, checkpoint_content_hash=None)
    assert await repository.advance_stage(
        token, ImportStage.EXTRACTING, checkpoint_content_hash="f" * 64
    )
    await session.commit()
    return repository, token, target


@pytest.mark.asyncio
async def test_variant_fetch_can_be_reserved_only_once_in_extracting(
    session: AsyncSession,
) -> None:
    """Dropping the attempted-at guard would repeat the variant fetch after redelivery."""

    repository, token, target = await claimed_extracting_job(session)

    assert await repository.reserve_variant_fetch(token) is True
    await session.commit()
    assert await repository.reserve_variant_fetch(token) is False
    await session.refresh(target)
    assert target.variant_fetch_attempted_at is not None


@pytest.mark.asyncio
async def test_stale_lease_cannot_record_variant_success(
    session: AsyncSession,
) -> None:
    """Dropping the lease fence would let a displaced worker record a variant result."""

    repository, token, _ = await claimed_extracting_job(session)
    assert await repository.reserve_variant_fetch(token)
    await session.commit()
    stale = token.__class__(token.job_id, "other", token.generation, token.expires_at)

    with pytest.raises(StaleLease):
        await repository.record_variant_fetch_success(stale, "a" * 64)


@pytest.mark.asyncio
async def test_variant_reservation_rejects_text_jobs_and_other_stages(
    session: AsyncSession,
) -> None:
    """Removing the input and stage predicates would reserve an irrelevant fetch."""

    repository = ImportRepository(session)
    text_job = await repository.create_job(
        owner_subject="auth0|owner",
        input_kind=ImportInputKind.TEXT,
        request_fingerprint="b" * 64,
        plaintext_input=b"Recipe text",
        payload_cipher=cipher(),
    )
    await session.commit()
    text_token = await repository.record_receipt_and_claim(
        text_job.id, "worker", 1, lease_seconds=60
    )
    assert text_token is not None
    assert await repository.advance_stage(
        text_token, ImportStage.FETCHING, checkpoint_content_hash=None
    )
    assert await repository.advance_stage(
        text_token, ImportStage.EXTRACTING, checkpoint_content_hash="f" * 64
    )
    assert await repository.reserve_variant_fetch(text_token) is False

    _, url_token, target = await claimed_extracting_job(session)
    target.stage = ImportStage.FETCHING
    await session.commit()
    assert await repository.reserve_variant_fetch(url_token) is False


@pytest.mark.asyncio
async def test_variant_result_requires_reservation_and_closed_safe_category(
    session: AsyncSession,
) -> None:
    """Removing result guards would retain unsafe metadata or invent a result without an attempt."""

    repository, token, target = await claimed_extracting_job(session)

    assert await repository.record_variant_fetch_success(token, "a" * 64) is False
    with pytest.raises(ValueError, match="variant outcome category"):
        await repository.record_variant_fetch_failure(token, "https://private.example/secret")
    assert await repository.reserve_variant_fetch(token) is True
    assert await repository.record_variant_fetch_failure(token, "alternate_shell") is True
    await session.refresh(target)
    assert target.variant_content_hash is None
    assert target.variant_outcome_category == "alternate_shell"
    assert await repository.record_variant_fetch_failure(token, "invalid_candidate_url") is False


@pytest.mark.asyncio
async def test_variant_fetch_success_is_durable_and_terminal_or_cancelled_jobs_cannot_mutate(
    session: AsyncSession,
) -> None:
    """Removing terminal or cancellation guards would change a stopped job."""

    repository, token, target = await claimed_extracting_job(session)
    assert await repository.reserve_variant_fetch(token)
    assert await repository.record_variant_fetch_success(token, "a" * 64)
    await session.refresh(target)
    assert target.variant_content_hash == "a" * 64
    assert target.variant_outcome_category == "succeeded"

    assert await repository.finish_terminal(
        token,
        ImportStatus.COMPLETED,
        error_category=None,
        diagnostic_reference=None,
    )
    with pytest.raises(StaleLease):
        await repository.record_variant_fetch_failure(token, "invalid_candidate_url")

    repository, token, target = await claimed_extracting_job(session)
    await session.execute(
        update(ImportJob)
        .where(ImportJob.id == target.id)
        .values(cancel_requested_at=datetime.now(UTC))
    )
    await session.commit()
    assert await repository.reserve_variant_fetch(token) is False
    assert await repository.record_variant_fetch_failure(token, "invalid_candidate_url") is False
    await session.refresh(target)
    assert target.variant_fetch_attempted_at is None
    assert target.variant_outcome_category is None


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
