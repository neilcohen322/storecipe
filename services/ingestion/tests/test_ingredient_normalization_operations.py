"""Persistence tests for ingredient-normalization operations and shared budget."""

# ruff: noqa: E501

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.models import (
    AiDailyUsage,
    AttemptState,
    Base,
    ImportInputKind,
    ImportJob,
    ImportStage,
    ImportStatus,
    IngredientNormalizationAttempt,
    LlmInvocation,
    LlmInvocationState,
    LlmOperationKind,
    ProviderAttempt,
)
from ingestion.repositories.budgets import AiBudgetRepository, BudgetExceeded
from ingestion.repositories.ingredient_normalizations import (
    IdempotencyKeyConflict,
    IngredientNormalizationRepository,
)


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


async def _import_reservation(
    session: AsyncSession,
    *,
    owner: str,
    tokens: int,
    daily_limit: int,
    budget_date: datetime | None = None,
) -> tuple[AiBudgetRepository, object, ProviderAttempt, ImportJob]:
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
        request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    session.add(attempt)
    await session.flush()
    budgets = AiBudgetRepository(session)
    day = (budget_date or datetime.now(UTC)).date()
    reservation = await budgets.reserve(
        owner_subject=owner,
        provider_operation_id=attempt.operation_id,
        operation_kind=LlmOperationKind.IMPORT_EXTRACTION,
        job_id=job.id,
        request_deadline_at=attempt.request_deadline_at,
        provider_name="openrouter",
        model_name="model",
        prompt_version="test",
        reservation_tokens=tokens,
        daily_limit=daily_limit,
        budget_date=day,
    )
    return budgets, reservation, attempt, job


async def _normalization_reservation(
    session: AsyncSession,
    *,
    owner: str,
    tokens: int,
    daily_limit: int,
    provider_operation_id=None,
) -> tuple[AiBudgetRepository, object, IngredientNormalizationAttempt]:
    operations = IngredientNormalizationRepository(session)
    operation, _ = await operations.get_or_create_operation(
        owner_subject=owner,
        idempotency_key="normalize-key",
        request_hash="a" * 64,
    )
    attempt = await operations.create_attempt(
        operation=operation,
        request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        provider_operation_id=provider_operation_id,
    )
    budgets = AiBudgetRepository(session)
    reservation = await budgets.reserve(
        owner_subject=owner,
        provider_operation_id=attempt.operation_id,
        operation_kind=LlmOperationKind.INGREDIENT_NORMALIZATION,
        request_deadline_at=attempt.request_deadline_at,
        provider_name="openrouter",
        model_name="model",
        prompt_version="ingredient-normalization-v1",
        reservation_tokens=tokens,
        daily_limit=daily_limit,
    )
    return budgets, reservation, attempt


@pytest.mark.asyncio
async def test_normalization_invocation_exists_without_import_job(session: AsyncSession) -> None:
    """Normalization telemetry must not require an ImportJob row."""

    _, reservation, _ = await _normalization_reservation(
        session, owner="auth0|normalize-only", tokens=64_000, daily_limit=1_100_000
    )
    invocation = await session.get(LlmInvocation, reservation.invocation_id)
    assert invocation is not None
    assert invocation.job_id is None
    assert invocation.operation_kind is LlmOperationKind.INGREDIENT_NORMALIZATION
    assert invocation.state is LlmInvocationState.RESERVED


@pytest.mark.asyncio
async def test_operation_idempotency_locks_owner_key(session: AsyncSession) -> None:
    repository = IngredientNormalizationRepository(session)
    first, created = await repository.get_or_create_operation(
        owner_subject="auth0|owner",
        idempotency_key="same-key",
        request_hash="b" * 64,
    )
    assert created is True
    second, created = await repository.get_or_create_operation(
        owner_subject="auth0|owner",
        idempotency_key="same-key",
        request_hash="b" * 64,
    )
    assert created is False
    assert second.id == first.id
    with pytest.raises(IdempotencyKeyConflict):
        await repository.get_or_create_operation(
            owner_subject="auth0|owner",
            idempotency_key="same-key",
            request_hash="c" * 64,
        )


@pytest.mark.asyncio
async def test_provider_attempt_job_id_stays_required(session: AsyncSession) -> None:
    job = ImportJob(
        owner_subject="auth0|provider",
        input_kind=ImportInputKind.URL,
        request_fingerprint="p" * 64,
    )
    session.add(job)
    await session.flush()
    session.add(
        ProviderAttempt(
            job_id=job.id,
            ordinal=1,
            state=AttemptState.RESERVED,
            reserved_at=datetime.now(UTC),
            request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    )
    with pytest.raises(IntegrityError):
        session.add(
            ProviderAttempt(
                job_id=None,
                ordinal=2,
                state=AttemptState.RESERVED,
                reserved_at=datetime.now(UTC),
                request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
        await session.flush()


@pytest.mark.asyncio
async def test_import_and_normalization_share_owner_daily_budget(session: AsyncSession) -> None:
    owner = "auth0|shared-budget"
    await _import_reservation(session, owner=owner, tokens=600_000, daily_limit=1_100_000)
    with pytest.raises(BudgetExceeded):
        await _normalization_reservation(
            session, owner=owner, tokens=600_000, daily_limit=1_100_000
        )
    usage = await session.get(AiDailyUsage, (owner, datetime.now(UTC).date()))
    assert usage is not None
    assert usage.reserved_tokens == 600_000


@pytest.mark.asyncio
async def test_normalization_reserve_is_idempotent_by_provider_operation(
    session: AsyncSession,
) -> None:
    operation_id = uuid4()
    budgets, first, _ = await _normalization_reservation(
        session,
        owner="auth0|replay",
        tokens=50_000,
        daily_limit=1_100_000,
        provider_operation_id=operation_id,
    )
    second = await budgets.reserve(
        owner_subject="auth0|replay",
        provider_operation_id=operation_id,
        operation_kind=LlmOperationKind.INGREDIENT_NORMALIZATION,
        request_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        provider_name="openrouter",
        model_name="model",
        prompt_version="ingredient-normalization-v1",
        reservation_tokens=50_000,
        daily_limit=1_100_000,
    )
    assert second == first
    usage = await session.get(AiDailyUsage, ("auth0|replay", first.budget_date))
    assert usage is not None
    assert usage.reserved_tokens == 50_000


@pytest.mark.asyncio
async def test_normalization_settlement_rejects_contradictory_usage(session: AsyncSession) -> None:
    budgets, reservation, _ = await _normalization_reservation(
        session, owner="auth0|settle", tokens=100, daily_limit=200
    )
    await budgets.succeed(reservation.invocation_id, input_tokens=10, output_tokens=20)
    with pytest.raises(ValueError, match="contradictory"):
        await budgets.succeed(reservation.invocation_id, input_tokens=11, output_tokens=20)
    with pytest.raises(ValueError, match="contradictory"):
        await budgets.fail(reservation.invocation_id)


@pytest.mark.asyncio
async def test_normalization_ambiguity_expiry_consumes_reserved_tokens(
    session: AsyncSession,
) -> None:
    budgets, reservation, _ = await _normalization_reservation(
        session, owner="auth0|ambiguous", tokens=100, daily_limit=200
    )
    await budgets.mark_ambiguous(reservation.invocation_id)
    usage = await session.get(AiDailyUsage, ("auth0|ambiguous", reservation.budget_date))
    assert usage is not None and usage.reserved_tokens == 100
    assert await budgets.settle_expired_ambiguities(datetime.now(UTC) + timedelta(minutes=1)) == 1
    assert (usage.reserved_tokens, usage.consumed_tokens) == (0, 100)


@pytest.mark.asyncio
async def test_llm_invocation_defaults_to_import_extraction(session: AsyncSession) -> None:
    assert (
        LlmInvocation.__table__.c.operation_kind.server_default is not None
        and "import_extraction" in str(LlmInvocation.__table__.c.operation_kind.server_default.arg)
    )


@pytest.mark.asyncio
async def test_llm_invocation_provider_operation_has_no_provider_attempt_fk(
    session: AsyncSession,
) -> None:
    foreign_keys = {
        fk.target_fullname
        for fk in inspect(LlmInvocation).mapper.columns["provider_operation_id"].foreign_keys
    }
    assert foreign_keys == set()


def test_migration_revision_targets_normalization_operations() -> None:
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260815_01_normalization_operations.py"
    )
    source = migration.read_text(encoding="utf-8")
    assert 'revision: str = "20260815_01"' in source
    assert 'down_revision: str | None = "20260809_01"' in source
    assert "llm_invocations_provider_operation_id_fkey" in source
    assert "server_default=sa.text(\"'import_extraction'\")" in source
    assert "ingredient_normalization_operations" in source
    assert "ingredient_normalization_attempts" in source
