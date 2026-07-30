"""Deterministic persistence tests for AI token budget accounting."""
# ruff: noqa: E501

import logging
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.models import (
    AiDailyUsage,
    AttemptState,
    Base,
    ImportInputKind,
    ImportJob,
    ImportStage,
    ImportStatus,
    ProviderAttempt,
)
from ingestion.repositories.budgets import AiBudgetRepository, BudgetExceeded


async def reservation_fixture(session: AsyncSession, owner: str, tokens: int = 100):
    job = ImportJob(
        owner_subject=owner,
        input_kind=ImportInputKind.URL,
        request_fingerprint=owner[-1] * 64,
        status=ImportStatus.PROCESSING,
        stage=ImportStage.MODEL_EXTRACTING,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add(job)
    await session.flush()
    attempt = ProviderAttempt(
        job_id=job.id,
        ordinal=1,
        state=AttemptState.IN_FLIGHT,
        reserved_at=datetime.now(UTC),
        request_deadline_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    session.add(attempt)
    await session.flush()
    budgets = AiBudgetRepository(session)
    reservation = await budgets.reserve(
        job=job,
        provider_attempt=attempt,
        provider_name="openrouter",
        model_name="model",
        prompt_version="test",
        reservation_tokens=tokens,
        daily_limit=200,
    )
    return budgets, reservation, job


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"ingestion": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as database_session:
        yield database_session
    await engine.dispose()


@pytest.mark.asyncio
async def test_reservation_is_idempotent_by_provider_operation(session: AsyncSession) -> None:
    """Catches charging the daily ledger twice for one provider operation."""

    job = ImportJob(
        owner_subject="auth0|owner",
        input_kind=ImportInputKind.URL,
        request_fingerprint="x" * 64,
        status=ImportStatus.PROCESSING,
        stage=ImportStage.MODEL_EXTRACTING,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add(job)
    await session.flush()
    attempt = ProviderAttempt(
        job_id=job.id,
        ordinal=1,
        state=AttemptState.IN_FLIGHT,
        reserved_at=datetime.now(UTC),
        request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    session.add(attempt)
    await session.flush()
    budgets = AiBudgetRepository(session)
    first = await budgets.reserve(
        job=job,
        provider_attempt=attempt,
        provider_name="openrouter",
        model_name="openai/gpt-5-nano",
        prompt_version="test",
        reservation_tokens=275_000,
        daily_limit=1_100_000,
    )
    second = await budgets.reserve(
        job=job,
        provider_attempt=attempt,
        provider_name="openrouter",
        model_name="openai/gpt-5-nano",
        prompt_version="test",
        reservation_tokens=275_000,
        daily_limit=1_100_000,
    )

    assert second == first
    usage = await session.get(AiDailyUsage, (job.owner_subject, first.budget_date))
    assert usage is not None
    assert usage.reserved_tokens == 275_000


@pytest.mark.asyncio
async def test_succeed_is_idempotent_and_rejects_contradictory_usage(
    session: AsyncSession,
) -> None:
    """Catches a retried terminal success charging the ledger twice."""

    job = ImportJob(
        owner_subject="auth0|owner-2",
        input_kind=ImportInputKind.URL,
        request_fingerprint="y" * 64,
        status=ImportStatus.PROCESSING,
        stage=ImportStage.MODEL_EXTRACTING,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add(job)
    await session.flush()
    attempt = ProviderAttempt(
        job_id=job.id,
        ordinal=1,
        state=AttemptState.IN_FLIGHT,
        reserved_at=datetime.now(UTC),
        request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    session.add(attempt)
    await session.flush()
    budgets = AiBudgetRepository(session)
    reservation = await budgets.reserve(
        job=job,
        provider_attempt=attempt,
        provider_name="openrouter",
        model_name="model",
        prompt_version="test",
        reservation_tokens=100,
        daily_limit=200,
    )
    await budgets.succeed(reservation.invocation_id, input_tokens=20, output_tokens=30)
    await budgets.succeed(reservation.invocation_id, input_tokens=20, output_tokens=30)
    usage = await session.get(AiDailyUsage, (job.owner_subject, reservation.budget_date))
    assert usage is not None and (usage.reserved_tokens, usage.consumed_tokens) == (0, 50)
    with pytest.raises(ValueError, match="contradictory"):
        await budgets.succeed(reservation.invocation_id, input_tokens=21, output_tokens=30)
    with pytest.raises(ValueError, match="contradictory"):
        await budgets.mark_ambiguous(reservation.invocation_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement",
    [
        {"input_tokens": 21, "output_tokens": 29, "cost_microunits": 7, "latency_ms": 11},
        {"input_tokens": 20, "output_tokens": 30, "cost_microunits": 8, "latency_ms": 11},
        {"input_tokens": 20, "output_tokens": 30, "cost_microunits": 7, "latency_ms": 12},
    ],
)
async def test_succeed_rejects_changes_to_any_immutable_usage_field(
    session: AsyncSession, replacement: dict[str, int]
) -> None:
    budgets, reservation, _ = await reservation_fixture(session, "auth0|immutable")
    await budgets.succeed(
        reservation.invocation_id,
        input_tokens=20,
        output_tokens=30,
        cost_microunits=7,
        latency_ms=11,
    )

    with pytest.raises(ValueError, match="contradictory immutable usage"):
        await budgets.succeed(reservation.invocation_id, **replacement)


@pytest.mark.asyncio
async def test_fail_releases_and_ambiguity_settles_conservatively(session: AsyncSession) -> None:
    budgets, reservation, job = await reservation_fixture(session, "auth0|3")
    await budgets.fail(reservation.invocation_id)
    usage = await session.get(AiDailyUsage, (job.owner_subject, reservation.budget_date))
    assert usage is not None and (usage.reserved_tokens, usage.consumed_tokens) == (0, 0)
    budgets, reservation, job = await reservation_fixture(session, "auth0|4")
    await budgets.mark_ambiguous(reservation.invocation_id)
    usage = await session.get(AiDailyUsage, (job.owner_subject, reservation.budget_date))
    assert usage is not None and usage.reserved_tokens == 100
    assert await budgets.settle_expired_ambiguities(datetime.now(UTC) + timedelta(minutes=1)) == 1
    assert (usage.reserved_tokens, usage.consumed_tokens) == (0, 100)


@pytest.mark.asyncio
async def test_late_success_replaces_conservative_usage_and_cap_rejects(
    session: AsyncSession,
) -> None:
    budgets, reservation, job = await reservation_fixture(session, "auth0|5")
    await budgets.mark_ambiguous(reservation.invocation_id)
    await budgets.settle_expired_ambiguities(datetime.now(UTC) + timedelta(minutes=1))
    await budgets.succeed(reservation.invocation_id, input_tokens=10, output_tokens=20)
    usage = await session.get(AiDailyUsage, (job.owner_subject, reservation.budget_date))
    assert usage is not None and (usage.reserved_tokens, usage.consumed_tokens) == (0, 30)
    attempt = ProviderAttempt(
        job_id=job.id,
        ordinal=2,
        state=AttemptState.IN_FLIGHT,
        reserved_at=datetime.now(UTC),
        request_deadline_at=datetime.now(UTC),
    )
    session.add(attempt)
    await session.flush()
    with pytest.raises(BudgetExceeded):
        await budgets.reserve(
            job=job,
            provider_attempt=attempt,
            provider_name="x",
            model_name="x",
            prompt_version="x",
            reservation_tokens=201,
            daily_limit=200,
        )


@pytest.mark.asyncio
async def test_actual_usage_over_reservation_records_actual_without_negative_counters(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Provider overages must be accounted for, not silently clamped to the reservation."""

    caplog.set_level(logging.INFO, logger="ingestion.repositories.budgets")
    budgets, reservation, job = await reservation_fixture(session, "auth0|6")
    await budgets.succeed(reservation.invocation_id, input_tokens=80, output_tokens=70)
    usage = await session.get(AiDailyUsage, (job.owner_subject, reservation.budget_date))
    assert usage is not None and (usage.reserved_tokens, usage.consumed_tokens) == (0, 150)
    assert "budget.accounting_anomaly" in caplog.text
