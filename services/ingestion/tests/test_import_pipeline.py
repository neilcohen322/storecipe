import base64
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import aiohttp
import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.ai_extractor import (
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    AiExtractionError,
    AiExtractionFailureCode,
    AiExtractionResult,
    OpenRouterUsage,
    build_extraction_messages,
    build_response_format,
    serialize_openrouter_request,
)
from ingestion.crypto import PayloadCipher
from ingestion.import_models import (
    FetchedDocument,
    IngredientCandidate,
    ParseError,
    ParseFailureCode,
    RecipeImportCandidate,
    ReviewRecipeCandidate,
)
from ingestion.models import (
    AiDailyUsage,
    AttemptState,
    Base,
    ImportInputKind,
    ImportJob,
    ImportPayload,
    ImportStage,
    ImportStatus,
    LlmInvocation,
    LlmInvocationState,
    ProviderAttempt,
)
from ingestion.pipeline import AiBudgetPolicy, ImportAdapters
from ingestion.pipeline import ImportPipeline as _ImportPipeline
from ingestion.repositories.budgets import AiBudgetRepository
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


def cipher() -> PayloadCipher:
    keyring = base64.b64encode(b"p" * 32).decode()
    return PayloadCipher.from_keyring(active_key_id="current", keyring=f"current={keyring}")


def ImportPipeline(
    repository: ImportRepository,
    payload_cipher: PayloadCipher,
    *,
    budgets: AiBudgetRepository | None = None,
    budget_policy: AiBudgetPolicy | None = None,
) -> _ImportPipeline:
    return _ImportPipeline(
        repository,
        payload_cipher,
        budgets=budgets or AiBudgetRepository(repository.session),
        budget_policy=budget_policy
        or AiBudgetPolicy(
            daily_limit=10_000_000,
            reservation_tokens=275_000,
            provider_name="openrouter",
            model_name="fake-model",
            prompt_version="test-v1",
        ),
    )


def candidate(*, source_url: str | None) -> RecipeImportCandidate:
    return RecipeImportCandidate(
        title="Lentil soup",
        source_url=source_url,
        ingredients=[IngredientCandidate(raw_text="1 cup lentils", name="lentils")],
        instructions=["Cook until tender."],
    )


class RecordingFetcher:
    def __init__(self, html: str = "<script type='application/ld+json'>{}</script>") -> None:
        self.calls: list[str] = []
        self.html = html

    async def fetch(self, url: str) -> FetchedDocument:
        self.calls.append(url)
        return FetchedDocument(
            requested_url=url,
            final_url="https://recipes.example/lentils",
            html=self.html,
            content_type="text/html",
            byte_count=51,
        )


class RecordingDeterministicExtractor:
    def __init__(self, outcome: RecipeImportCandidate | ParseError) -> None:
        self.outcome = outcome
        self.calls: list[FetchedDocument] = []

    async def extract(self, document: FetchedDocument) -> RecipeImportCandidate:
        self.calls.append(document)
        if isinstance(self.outcome, ParseError):
            raise self.outcome
        return self.outcome


class VisibilityCheckingDeterministicExtractor:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], job_id: UUID) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self.observed: tuple[ImportStage | None, str | None, bool] | None = None

    async def extract(self, document: FetchedDocument) -> RecipeImportCandidate:
        async with self._session_factory() as observer:
            visible_job = await observer.get(ImportJob, self._job_id)
            visible_payload = await observer.scalar(
                select(ImportPayload).where(
                    ImportPayload.job_id == self._job_id,
                    ImportPayload.payload_type == "fetched",
                )
            )
            self.observed = (
                visible_job.stage if visible_job is not None else None,
                visible_job.fetched_content_hash if visible_job is not None else None,
                visible_payload is not None,
            )
        return candidate(source_url=document.final_url)


class InterruptingDeterministicExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, document: FetchedDocument) -> RecipeImportCandidate:
        self.calls += 1
        raise RuntimeError("simulated worker death after fetched checkpoint")


class RecordingModelExtractor:
    def __init__(self, outcomes: list[AiExtractionResult | BaseException]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str | None]] = []

    async def extract(
        self, *, source_text: str, trusted_source_url: str | None
    ) -> AiExtractionResult:
        self.calls.append((source_text, trusted_source_url))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ProviderVisibilityModelExtractor:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], job_id: UUID) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self.observed: (
            tuple[ImportStage | None, int | None, AttemptState | None, LlmInvocationState | None]
            | None
        ) = None

    async def extract(
        self, *, source_text: str, trusted_source_url: str | None
    ) -> AiExtractionResult:
        async with self._session_factory() as observer:
            visible_job = await observer.get(ImportJob, self._job_id)
            attempt = await observer.scalar(
                select(ProviderAttempt)
                .where(ProviderAttempt.job_id == self._job_id)
                .order_by(ProviderAttempt.ordinal.desc())
                .limit(1)
            )
            invocation = await observer.scalar(
                select(LlmInvocation).where(LlmInvocation.job_id == self._job_id)
            )
            self.observed = (
                visible_job.stage if visible_job is not None else None,
                visible_job.provider_count if visible_job is not None else None,
                attempt.state if attempt is not None else None,
                invocation.state if invocation is not None else None,
            )
        return model_result()


class CheckpointFailingRepository(ImportRepository):
    async def store_pipeline_payload(
        self,
        token: object,
        payload_type: str,
        plaintext: bytes,
        payload_cipher: PayloadCipher,
        *,
        max_bytes: int = 256 * 1024,
    ) -> str:
        if payload_type == "candidate":
            raise RuntimeError("simulated checkpoint database failure")
        return await super().store_pipeline_payload(  # type: ignore[arg-type]
            token, payload_type, plaintext, payload_cipher, max_bytes=max_bytes
        )


class ExpiringReservationRepository(ImportRepository):
    async def reserve_provider_attempt(
        self,
        token: object,
        *,
        request_deadline_at: datetime,
        operation_id: UUID | None = None,
    ) -> ProviderAttempt | None:
        attempt = await super().reserve_provider_attempt(  # type: ignore[arg-type]
            token, request_deadline_at=request_deadline_at, operation_id=operation_id
        )
        if attempt is not None:
            await self.session.execute(
                update(ProviderAttempt)
                .execution_options(synchronize_session=False)
                .where(ProviderAttempt.id == attempt.id)
                .values(request_deadline_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        return attempt


class StaleFenceClockRepository(ImportRepository):
    async def _database_now(self) -> datetime:
        return datetime.now(UTC) - timedelta(minutes=1)


def model_result() -> AiExtractionResult:
    return AiExtractionResult(
        candidate=candidate(source_url="https://recipes.example/lentils"),
        model="fake-model",
        prompt_version="test-v1",
        usage=OpenRouterUsage(
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            cost=Decimal("0"),
        ),
        latency_ms=1,
    )


async def new_claimed_job(
    session: AsyncSession,
    *,
    input_kind: ImportInputKind,
    plaintext: bytes,
    deadline_at: datetime | None = None,
) -> tuple[ImportRepository, UUID, object]:
    repository = ImportRepository(session)
    job = await repository.create_job(
        owner_subject="auth0|owner",
        input_kind=input_kind,
        request_fingerprint="a" * 64,
        plaintext_input=plaintext,
        payload_cipher=cipher(),
        deadline_at=deadline_at,
    )
    await session.commit()
    token = await repository.record_receipt_and_claim(job.id, "worker-a", 1, lease_seconds=60)
    assert token is not None
    await session.commit()
    return repository, job.id, token


@pytest_asyncio.fixture
async def file_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from pathlib import Path

    database_path = Path("services/ingestion/tests") / f".import-pipeline-{uuid4()}.sqlite"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        execution_options={"schema_translate_map": {"ingestion": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
        for suffix in ("", "-shm", "-wal"):
            database_path.with_name(database_path.name + suffix).unlink(missing_ok=True)


async def redeliver(session: AsyncSession, repository: ImportRepository, job_id: UUID) -> object:
    await session.execute(
        update(ImportJob)
        .where(ImportJob.id == job_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1), next_attempt_at=None)
    )
    job = await session.get(ImportJob, job_id)
    assert job is not None
    token = await repository.record_receipt_and_claim(
        job_id, "worker-b", job.dispatch_generation, lease_seconds=60
    )
    assert token is not None
    await session.commit()
    return token


async def job(session: AsyncSession, job_id: UUID) -> ImportJob:
    result = await session.get(ImportJob, job_id)
    assert result is not None
    return result


def adapters(
    fetcher: RecordingFetcher,
    deterministic: RecordingDeterministicExtractor,
    model: RecordingModelExtractor,
) -> ImportAdapters:
    return ImportAdapters(fetcher=fetcher, deterministic=deterministic, model=model, catalog=None)


@pytest.mark.asyncio
async def test_budget_exhaustion_never_calls_provider(session: AsyncSession) -> None:
    repository, job_id, token = await new_claimed_job(
        session,
        input_kind=ImportInputKind.URL,
        plaintext=b"https://recipes.example/lentils",
    )
    session.add(
        AiDailyUsage(
            owner_subject="auth0|owner",
            budget_date_utc=datetime.now(UTC).date(),
            reserved_tokens=0,
            consumed_tokens=1_100_000,
        )
    )
    await session.commit()
    extractor = RecordingModelExtractor([model_result()])
    pipeline = ImportPipeline(
        repository,
        cipher(),
        budgets=AiBudgetRepository(session),
        budget_policy=AiBudgetPolicy(
            daily_limit=1_100_000,
            reservation_tokens=275_000,
            provider_name="openrouter",
            model_name="openai/gpt-5-nano",
            prompt_version=PROMPT_VERSION,
        ),
    )

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            extractor,
        ),
    )

    stored = await session.get(ImportJob, job_id)
    assert stored is not None
    assert extractor.calls == []
    assert stored.status is ImportStatus.REVIEW_REQUIRED
    assert stored.safe_error_category == "daily_ai_budget_exceeded"


@pytest.mark.asyncio
async def test_deterministic_extraction_bypasses_exhausted_ai_budget(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"structured recipe"
    )
    session.add(
        AiDailyUsage(
            owner_subject="auth0|owner",
            budget_date_utc=datetime.now(UTC).date(),
            consumed_tokens=10_000_000,
        )
    )
    await session.commit()

    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(candidate(source_url=None)),
            RecordingModelExtractor([]),
        ),
    )

    stored = await job(session, job_id)
    assert stored.stage is ImportStage.VALIDATING
    assert await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id)) is None


@pytest.mark.asyncio
async def test_provider_success_settles_actual_usage_and_pinned_provider(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"unstructured recipe"
    )
    policy = AiBudgetPolicy(
        daily_limit=1_000_000,
        reservation_tokens=275_000,
        provider_name="pinned-provider",
        model_name="pinned-model",
        prompt_version=PROMPT_VERSION,
    )

    await ImportPipeline(
        repository,
        cipher(),
        budgets=AiBudgetRepository(session),
        budget_policy=policy,
    ).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([model_result()]),
        ),
    )

    invocation = await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id))
    attempt = await session.scalar(select(ProviderAttempt).where(ProviderAttempt.job_id == job_id))
    usage = await session.get(AiDailyUsage, ("auth0|owner", datetime.now(UTC).date()))
    assert invocation is not None and invocation.state is LlmInvocationState.SUCCEEDED
    assert invocation.total_tokens == 3
    assert usage is not None
    assert usage.reserved_tokens == 0
    assert usage.consumed_tokens == 3
    assert attempt is not None
    assert attempt.provider_name == "pinned-provider"


@pytest.mark.asyncio
async def test_known_precharge_rejection_releases_reservation(session: AsyncSession) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"unstructured recipe"
    )
    failure = AiExtractionError(
        AiExtractionFailureCode.PROVIDER_REQUEST_FAILED,
        status=401,
        provider_request_started=True,
    )
    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([failure]),
        ),
    )

    invocation = await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id))
    usage = await session.get(AiDailyUsage, ("auth0|owner", datetime.now(UTC).date()))
    assert invocation is not None and invocation.state is LlmInvocationState.FAILED
    assert usage is not None and usage.reserved_tokens == 0


@pytest.mark.asyncio
async def test_paid_schema_invalid_completion_settles_actual_usage(session: AsyncSession) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"unstructured recipe"
    )
    failure = AiExtractionError(
        AiExtractionFailureCode.SCHEMA_VALIDATION_FAILED,
        provider_request_started=True,
        usage=OpenRouterUsage(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
            cost=Decimal("0.000123"),
        ),
        model_name="fake-model",
        prompt_version=PROMPT_VERSION,
        latency_ms=9,
    )
    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([failure]),
        ),
    )

    invocation = await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id))
    assert invocation is not None
    assert invocation.state is LlmInvocationState.SUCCEEDED
    assert invocation.total_tokens == 18
    assert invocation.cost_microunits == 123
    assert invocation.latency_ms == 9


@pytest.mark.asyncio
async def test_inconsistent_provider_usage_remains_ambiguous(session: AsyncSession) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"unstructured recipe"
    )
    result = model_result()
    result = AiExtractionResult(
        candidate=result.candidate,
        model=result.model,
        prompt_version=result.prompt_version,
        usage=OpenRouterUsage(
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=30,
            cost=Decimal("0.000123"),
        ),
        latency_ms=result.latency_ms,
    )
    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([result]),
        ),
    )

    invocation = await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id))
    usage = await session.get(AiDailyUsage, ("auth0|owner", datetime.now(UTC).date()))
    assert invocation is not None
    assert invocation.state is LlmInvocationState.AMBIGUOUS
    assert invocation.settled_at is None
    assert usage is not None and usage.reserved_tokens == 275_000


@pytest.mark.asyncio
async def test_malformed_paid_response_without_usage_remains_ambiguous(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"unstructured recipe"
    )
    failure = AiExtractionError(
        AiExtractionFailureCode.INVALID_PROVIDER_RESPONSE,
        provider_request_started=True,
    )
    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([failure]),
        ),
    )

    invocation = await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id))
    assert invocation is not None
    assert invocation.state is LlmInvocationState.AMBIGUOUS
    assert invocation.settled_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_ambiguous_transport_failure_retains_reservation(
    session: AsyncSession, failure: BaseException
) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"unstructured recipe"
    )
    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([failure]),
        ),
    )

    invocation = await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id))
    usage = await session.get(AiDailyUsage, ("auth0|owner", datetime.now(UTC).date()))
    assert invocation is not None and invocation.state is LlmInvocationState.AMBIGUOUS
    assert usage is not None and usage.reserved_tokens == 275_000


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ['"', "\\", "\x00", "\x1f", "א"])
async def test_actual_serialized_request_fits_reserved_token_envelope(
    session: AsyncSession, unit: str
) -> None:
    repository, job_id, token = await new_claimed_job(
        session,
        input_kind=ImportInputKind.TEXT,
        plaintext=(unit * (256 * 1024)).encode("utf-8"),
    )
    extractor = RecordingModelExtractor([model_result()])
    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            extractor,
        ),
    )

    source = extractor.calls[0][0]
    serialized = serialize_openrouter_request(
        model="fake-model",
        messages=build_extraction_messages(source),
        response_format=build_response_format(),
    )
    assert len(serialized) + MAX_OUTPUT_TOKENS <= 275_000


@pytest.mark.asyncio
async def test_undersized_reservation_fails_closed_before_provider_io(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"unstructured recipe"
    )
    extractor = RecordingModelExtractor([model_result()])
    policy = AiBudgetPolicy(
        daily_limit=10_000,
        reservation_tokens=1,
        provider_name="openrouter",
        model_name="fake-model",
        prompt_version=PROMPT_VERSION,
    )

    await ImportPipeline(
        repository,
        cipher(),
        budgets=AiBudgetRepository(session),
        budget_policy=policy,
    ).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            extractor,
        ),
    )

    stored = await job(session, job_id)
    invocation = await session.scalar(select(LlmInvocation).where(LlmInvocation.job_id == job_id))
    usage = await session.get(AiDailyUsage, ("auth0|owner", datetime.now(UTC).date()))
    assert extractor.calls == []
    assert stored.status is ImportStatus.FAILED
    assert stored.safe_error_category == "budget_not_configured"
    assert invocation is not None and invocation.state is LlmInvocationState.FAILED
    assert usage is not None and usage.reserved_tokens == 0


@pytest.mark.asyncio
async def test_deterministic_candidate_is_checkpointed_and_reused_after_redelivery(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Removing the validated-candidate checkpoint would fetch/extract again after redelivery."""
    caplog.set_level(logging.INFO, logger="ingestion.pipeline")

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    fetcher = RecordingFetcher()
    deterministic = RecordingDeterministicExtractor(
        candidate(source_url="https://recipes.example/lentils")
    )
    model = RecordingModelExtractor([])
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(job_id, token, adapters(fetcher, deterministic, model))
    await session.commit()
    first = await job(session, job_id)

    assert first.stage is ImportStage.VALIDATING
    assert first.candidate_content_hash is not None
    assert len(fetcher.calls) == 1
    assert len(deterministic.calls) == 1
    assert model.calls == []
    events = [json.loads(record.message) for record in caplog.records]
    stage_events = [event for event in events if event["event"] == "stage.completed"]
    assert {event["stage"] for event in stage_events} >= {"fetching", "extracting"}
    assert all(event["elapsed_ms"] >= 0 for event in stage_events)

    next_token = await redeliver(session, repository, job_id)
    await pipeline.run(job_id, next_token, adapters(fetcher, deterministic, model))

    assert len(fetcher.calls) == 1
    assert len(deterministic.calls) == 1
    assert model.calls == []


@pytest.mark.asyncio
async def test_disabled_model_extraction_finishes_without_reserving_provider_attempt(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"Soup without structured data"
    )
    deterministic = RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND))

    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        ImportAdapters(RecordingFetcher(), deterministic, None, None),  # type: ignore[arg-type]
    )

    failed = await job(session, job_id)
    assert failed.status is ImportStatus.FAILED
    assert failed.safe_error_category == "model_extraction_disabled"
    assert failed.provider_count == 0


@pytest.mark.asyncio
async def test_incomplete_safe_candidate_finishes_as_review_required(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session,
        input_kind=ImportInputKind.TEXT,
        plaintext=b"partial soup recipe",
    )
    partial = ReviewRecipeCandidate(
        title="Partial soup",
        ingredients=[IngredientCandidate(raw_text="1 cup water", name="water")],
    )
    deterministic = RecordingDeterministicExtractor(
        ParseError(ParseFailureCode.INCOMPLETE_RECIPE, candidate=partial)
    )
    model = RecordingModelExtractor([])

    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        ImportAdapters(RecordingFetcher(), deterministic, model, None),
    )

    review = await job(session, job_id)
    payload = await repository.load_payload(job_id, "candidate", cipher())
    assert review.status is ImportStatus.REVIEW_REQUIRED
    assert review.safe_error_category == "incomplete_extraction"
    assert review.candidate_content_hash is not None
    assert payload is not None
    assert model.calls == []


@pytest.mark.asyncio
async def test_oversized_review_candidate_finishes_with_safe_failure(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session,
        input_kind=ImportInputKind.TEXT,
        plaintext=b"large partial recipe",
    )
    partial = ReviewRecipeCandidate(
        title="Large partial",
        ingredients=[
            IngredientCandidate(raw_text="x" * 4096, name=f"item-{index}") for index in range(256)
        ],
    )
    deterministic = RecordingDeterministicExtractor(
        ParseError(ParseFailureCode.INCOMPLETE_RECIPE, candidate=partial)
    )

    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        ImportAdapters(RecordingFetcher(), deterministic, None, None),
    )

    failed = await job(session, job_id)
    assert failed.status is ImportStatus.FAILED
    assert failed.safe_error_category == "candidate_payload_too_large"


@pytest.mark.asyncio
async def test_fetched_checkpoint_is_visible_before_deterministic_external_work(
    file_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A worker must commit fetch state before the next stage can begin."""

    async with file_session_factory() as session:
        repository = ImportRepository(session)
        created = await repository.create_job(
            owner_subject="auth0|owner",
            input_kind=ImportInputKind.URL,
            request_fingerprint="b" * 64,
            plaintext_input=b"https://recipes.example/lentils",
            payload_cipher=cipher(),
        )
        await session.commit()
        token = await repository.record_receipt_and_claim(
            created.id, "worker-a", 1, lease_seconds=60
        )
        assert token is not None
        await session.commit()

        deterministic = VisibilityCheckingDeterministicExtractor(file_session_factory, created.id)
        await ImportPipeline(repository, cipher()).run(
            created.id,
            token,
            adapters(RecordingFetcher(), deterministic, RecordingModelExtractor([])),
        )

    assert deterministic.observed is not None
    assert deterministic.observed[0] is ImportStage.EXTRACTING
    assert deterministic.observed[1] is not None
    assert deterministic.observed[2] is True


@pytest.mark.asyncio
async def test_provider_reservation_is_visible_before_model_external_work(
    file_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with file_session_factory() as session:
        repository = ImportRepository(session)
        created = await repository.create_job(
            owner_subject="auth0|owner",
            input_kind=ImportInputKind.URL,
            request_fingerprint="e" * 64,
            plaintext_input=b"https://recipes.example/lentils",
            payload_cipher=cipher(),
        )
        await session.commit()
        token = await repository.record_receipt_and_claim(
            created.id, "worker-a", 1, lease_seconds=60
        )
        assert token is not None
        await session.commit()

        model = ProviderVisibilityModelExtractor(file_session_factory, created.id)
        await ImportPipeline(repository, cipher()).run(
            created.id,
            token,
            adapters(
                RecordingFetcher(),
                RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
                model,
            ),
        )

    assert model.observed == (
        ImportStage.MODEL_EXTRACTING,
        1,
        AttemptState.IN_FLIGHT,
        LlmInvocationState.RESERVED,
    )


@pytest.mark.asyncio
async def test_fetched_checkpoint_survives_a_worker_failure_before_extraction(
    session: AsyncSession,
) -> None:
    """Dropping the fetched payload would repeat the URL request after a worker dies."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    fetcher = RecordingFetcher()
    pipeline = ImportPipeline(repository, cipher())

    with pytest.raises(RuntimeError, match="simulated worker death"):
        await pipeline.run(
            job_id,
            token,
            adapters(
                fetcher,
                InterruptingDeterministicExtractor(),
                RecordingModelExtractor([]),
            ),
        )
    await session.commit()
    interrupted = await job(session, job_id)

    assert interrupted.stage is ImportStage.EXTRACTING
    assert interrupted.fetched_content_hash is not None
    assert len(fetcher.calls) == 1

    next_token = await redeliver(session, repository, job_id)
    deterministic = RecordingDeterministicExtractor(
        candidate(source_url="https://recipes.example/lentils")
    )
    await pipeline.run(
        job_id,
        next_token,
        adapters(fetcher, deterministic, RecordingModelExtractor([])),
    )

    assert len(fetcher.calls) == 1
    assert len(deterministic.calls) == 1


@pytest.mark.asyncio
async def test_transient_provider_failure_reserves_only_two_attempts_then_reuses_success(
    session: AsyncSession,
) -> None:
    """Allowing a third reservation would spend unbounded provider calls on one import."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    fetcher = RecordingFetcher()
    deterministic = RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND))
    model = RecordingModelExtractor(
        [
            AiExtractionError(AiExtractionFailureCode.RATE_LIMITED, status=429),
            model_result(),
        ]
    )
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(job_id, token, adapters(fetcher, deterministic, model))
    await session.commit()
    after_first = await job(session, job_id)

    assert after_first.status is ImportStatus.QUEUED
    assert after_first.stage is ImportStage.MODEL_EXTRACTING
    assert after_first.provider_count == 1
    assert after_first.safe_error_category == "provider_rate_limited"

    next_token = await redeliver(session, repository, job_id)
    await pipeline.run(job_id, next_token, adapters(fetcher, deterministic, model))
    await session.commit()
    after_second = await job(session, job_id)
    attempts = list(
        await session.scalars(select(ProviderAttempt).where(ProviderAttempt.job_id == job_id))
    )

    assert after_second.stage is ImportStage.VALIDATING
    assert after_second.provider_count == 2
    assert [attempt.state for attempt in attempts] == [AttemptState.FAILED, AttemptState.SUCCEEDED]
    assert len(model.calls) == 2

    final_token = await redeliver(session, repository, job_id)
    await pipeline.run(job_id, final_token, adapters(fetcher, deterministic, model))
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_invalid_provider_output_is_terminal_and_serializes_no_secret(
    session: AsyncSession,
) -> None:
    """Treating malformed provider output as retryable leaks retries and secret diagnostics."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    secret = "provider-body-private-source"
    model = RecordingModelExtractor(
        [AiExtractionError(AiExtractionFailureCode.SCHEMA_VALIDATION_FAILED)]
    )
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            model,
        ),
    )
    await session.commit()
    failed = await job(session, job_id)

    assert failed.status is ImportStatus.FAILED
    assert failed.provider_count == 1
    assert failed.safe_error_category == "provider_invalid_output"
    assert failed.diagnostic_reference is None
    assert secret not in (failed.safe_error_category or "")


@pytest.mark.asyncio
async def test_transport_failure_keeps_only_a_safe_error_category(session: AsyncSession) -> None:
    """Persisting a provider exception string would retain untrusted provider response content."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    secret = "provider-response-body-that-must-not-be-retained"
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([aiohttp.ClientConnectionError(secret)]),
        ),
    )
    await session.commit()
    retried = await job(session, job_id)

    assert retried.status is ImportStatus.QUEUED
    assert retried.safe_error_category == "provider_transport"
    assert retried.diagnostic_reference is None
    assert secret not in (retried.safe_error_category or "")


@pytest.mark.asyncio
async def test_checkpoint_failure_preserves_provider_success_without_scheduling_a_retry(
    session: AsyncSession,
) -> None:
    """A local checkpoint failure must not spend a second provider reservation."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    pipeline = ImportPipeline(CheckpointFailingRepository(session), cipher())

    with pytest.raises(RuntimeError, match="checkpoint database failure"):
        await pipeline.run(
            job_id,
            token,
            adapters(
                RecordingFetcher(),
                RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
                RecordingModelExtractor([model_result()]),
            ),
        )
    await session.commit()
    interrupted = await job(session, job_id)
    attempts = list(
        await session.scalars(select(ProviderAttempt).where(ProviderAttempt.job_id == job_id))
    )

    assert interrupted.status is ImportStatus.PROCESSING
    assert interrupted.stage is ImportStage.MODEL_EXTRACTING
    assert interrupted.provider_count == 1
    assert interrupted.next_attempt_at is None
    assert [attempt.state for attempt in attempts] == [AttemptState.SUCCEEDED]
    operation_payload = await repository.load_payload(
        job_id,
        f"provider_result:{attempts[0].operation_id}",
        cipher(),
    )
    assert operation_payload is not None


@pytest.mark.asyncio
async def test_provider_success_survives_lease_loss_and_is_adopted_without_another_call(
    session: AsyncSession,
) -> None:
    repository, job_id, stale_token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    assert await repository.advance_stage(
        stale_token, ImportStage.FETCHING, checkpoint_content_hash=None
    )
    document = FetchedDocument(
        "https://recipes.example/lentils",
        "https://recipes.example/lentils",
        "<html>Lentils</html>",
        "text/html",
        21,
    )
    fetched_payload = _ImportPipeline._serialize_document(document)
    fetched_hash = await repository.store_pipeline_payload(
        stale_token,
        "fetched",
        fetched_payload,
        cipher(),
        max_bytes=5 * 1024 * 1024,
    )
    assert await repository.advance_stage(
        stale_token, ImportStage.EXTRACTING, checkpoint_content_hash=fetched_hash
    )
    assert await repository.advance_stage(
        stale_token, ImportStage.MODEL_EXTRACTING, checkpoint_content_hash=None
    )
    attempt = await repository.reserve_provider_attempt(
        stale_token, request_deadline_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    assert attempt is not None
    assert await repository.adopt_provider_attempt(stale_token, attempt.operation_id)
    await session.commit()

    current_token = await redeliver(session, repository, job_id)
    candidate_payload = (
        candidate(source_url="https://recipes.example/lentils").model_dump_json().encode()
    )

    recorded = await repository.record_provider_success(
        attempt.operation_id,
        candidate_payload=candidate_payload,
        payload_cipher=cipher(),
        provider_name="openrouter",
        model_name="fake-model",
        input_tokens=1,
        output_tokens=2,
        cost_microunits=0,
    )
    assert recorded is True
    await session.commit()
    operation_payload = await repository.load_payload(
        job_id, f"provider_result:{attempt.operation_id}", cipher()
    )
    assert operation_payload == candidate_payload

    model = RecordingModelExtractor([])
    await ImportPipeline(repository, cipher()).run(
        job_id,
        current_token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            model,
        ),
    )

    adopted = await job(session, job_id)
    assert adopted.stage is ImportStage.VALIDATING
    assert adopted.candidate_content_hash is not None
    assert model.calls == []


@pytest.mark.asyncio
async def test_adoption_rechecks_database_deadline_and_closes_expired_reservation(
    session: AsyncSession,
) -> None:
    """Using the pre-reservation timestamp can issue provider I/O after the reservation expired."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    model = RecordingModelExtractor([model_result()])
    pipeline = ImportPipeline(ExpiringReservationRepository(session), cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            model,
        ),
    )
    await session.commit()
    expired = await job(session, job_id)
    attempt = await session.scalar(select(ProviderAttempt).where(ProviderAttempt.job_id == job_id))

    assert model.calls == []
    assert expired.status is ImportStatus.PROCESSING
    assert expired.stage is ImportStage.MODEL_EXTRACTING
    assert attempt is not None
    assert attempt.state is AttemptState.FAILED
    assert attempt.outcome_category == "provider_attempt_expired"


@pytest.mark.asyncio
async def test_adoption_deadline_predicate_uses_database_clock_not_fetched_time(
    session: AsyncSession,
) -> None:
    """Binding a stale fetched clock could admit an already-expired reservation."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    assert await repository.advance_stage(token, ImportStage.FETCHING, checkpoint_content_hash=None)
    assert await repository.advance_stage(
        token, ImportStage.EXTRACTING, checkpoint_content_hash="f" * 64
    )
    assert await repository.advance_stage(
        token, ImportStage.MODEL_EXTRACTING, checkpoint_content_hash=None
    )
    attempt = await repository.reserve_provider_attempt(
        token, request_deadline_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    assert attempt is not None
    await session.execute(
        update(ProviderAttempt)
        .execution_options(synchronize_session=False)
        .where(ProviderAttempt.id == attempt.id)
        .values(request_deadline_at=datetime.now(UTC) - timedelta(seconds=2))
    )

    adopted = await StaleFenceClockRepository(session).adopt_provider_attempt(
        token, attempt.operation_id
    )
    closed = await session.scalar(select(ProviderAttempt).where(ProviderAttempt.id == attempt.id))

    assert adopted is None
    assert closed is not None
    assert closed.state is AttemptState.FAILED
    assert closed.outcome_category == "provider_attempt_expired"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, category",
    [
        (TimeoutError(), "provider_timeout"),
        (
            AiExtractionError(AiExtractionFailureCode.PROVIDER_REQUEST_FAILED, status=503),
            "provider_temporary",
        ),
    ],
)
async def test_timeout_and_temporary_5xx_schedule_the_second_reservation(
    session: AsyncSession,
    failure: BaseException,
    category: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Classifying timeout or temporary 5xx as terminal would discard the allowed retry."""

    caplog.set_level(logging.INFO, logger="ingestion.pipeline")
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([failure]),
        ),
    )
    await session.commit()
    retried = await job(session, job_id)

    assert retried.status is ImportStatus.QUEUED
    assert retried.stage is ImportStage.MODEL_EXTRACTING
    assert retried.provider_count == 1
    assert retried.safe_error_category == category
    events = [json.loads(record.message) for record in caplog.records]
    assert any(
        event["event"] == "stage.retry_scheduled" and event["stage"] == "model_extracting"
        for event in events
    )
    assert not any(
        event["event"] == "stage.completed" and event["stage"] == "model_extracting"
        for event in events
    )
    assert any(
        event["event"] == "provider.retry_scheduled" and event["error_category"] == category
        for event in events
    )


@pytest.mark.asyncio
async def test_non_5xx_provider_status_is_terminal_without_a_second_reservation(
    session: AsyncSession,
) -> None:
    """Classifying an invalid HTTP 600 as temporary would spend the final provider attempt."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor(
                [AiExtractionError(AiExtractionFailureCode.PROVIDER_REQUEST_FAILED, status=600)]
            ),
        ),
    )
    await session.commit()
    failed = await job(session, job_id)

    assert failed.status is ImportStatus.FAILED
    assert failed.provider_count == 1
    assert failed.next_attempt_at is None
    assert failed.safe_error_category == "provider_request_failed"


@pytest.mark.asyncio
async def test_model_input_is_capped_by_utf8_bytes_before_provider_io(
    session: AsyncSession,
) -> None:
    """Slicing Unicode characters instead of UTF-8 bytes can exceed the provider retention limit."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    model = RecordingModelExtractor(
        [AiExtractionError(AiExtractionFailureCode.SCHEMA_VALIDATION_FAILED)]
    )
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher("א" * (256 * 1024)),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            model,
        ),
    )

    assert len(model.calls) == 1
    assert len(model.calls[0][0].encode("utf-8")) <= 256 * 1024


@pytest.mark.asyncio
async def test_provider_reservation_cannot_outlive_the_job_deadline(session: AsyncSession) -> None:
    """A nearer job deadline must cap an unresolved provider operation."""

    deadline = datetime.now(UTC) + timedelta(seconds=5)
    repository, job_id, token = await new_claimed_job(
        session,
        input_kind=ImportInputKind.URL,
        plaintext=b"https://recipes.example/lentils",
        deadline_at=deadline,
    )
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor(
                [AiExtractionError(AiExtractionFailureCode.SCHEMA_VALIDATION_FAILED)]
            ),
        ),
    )
    attempts = list(
        await session.scalars(select(ProviderAttempt).where(ProviderAttempt.job_id == job_id))
    )

    assert len(attempts) == 1
    reserved_deadline = attempts[0].request_deadline_at
    if reserved_deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=None)
    assert reserved_deadline <= deadline


@pytest.mark.asyncio
async def test_unresolved_first_provider_attempt_schedules_single_retry_after_its_deadline(
    session: AsyncSession,
) -> None:
    """Starting a second call beside an unresolved one can duplicate a charged provider request."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    assert await repository.advance_stage(token, ImportStage.FETCHING, checkpoint_content_hash=None)
    assert await repository.advance_stage(
        token, ImportStage.EXTRACTING, checkpoint_content_hash="f" * 64
    )
    assert await repository.advance_stage(
        token, ImportStage.MODEL_EXTRACTING, checkpoint_content_hash=None
    )
    reserved = await repository.reserve_provider_attempt(
        token, request_deadline_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    assert reserved is not None
    adopted = await repository.adopt_provider_attempt(token, reserved.operation_id)
    assert adopted is not None
    assert adopted.operation_id == reserved.operation_id
    await session.commit()

    model = RecordingModelExtractor([])
    pipeline = ImportPipeline(repository, cipher())
    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            model,
        ),
    )

    blocked = await job(session, job_id)
    assert blocked.provider_count == 1
    assert model.calls == []

    await session.execute(
        update(ProviderAttempt)
        .where(ProviderAttempt.operation_id == reserved.operation_id)
        .values(request_deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await pipeline.run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            model,
        ),
    )
    expired = await job(session, job_id)
    expired_attempt = await session.scalar(
        select(ProviderAttempt).where(ProviderAttempt.operation_id == reserved.operation_id)
    )

    assert expired.status is ImportStatus.QUEUED
    assert expired.provider_count == 1
    assert expired.dispatch_generation == 2
    assert expired.safe_error_category == "provider_attempt_unresolved"
    assert expired_attempt is not None
    assert expired_attempt.state is AttemptState.AMBIGUOUS


@pytest.mark.asyncio
async def test_unresolved_second_provider_attempt_exhausts_the_budget(
    session: AsyncSession,
) -> None:
    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    assert await repository.advance_stage(token, ImportStage.FETCHING, checkpoint_content_hash=None)
    assert await repository.advance_stage(
        token, ImportStage.EXTRACTING, checkpoint_content_hash="f" * 64
    )
    assert await repository.advance_stage(
        token, ImportStage.MODEL_EXTRACTING, checkpoint_content_hash=None
    )
    first = await repository.reserve_provider_attempt(
        token, request_deadline_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    assert first is not None
    first.state = AttemptState.AMBIGUOUS
    first.completed_at = datetime.now(UTC)
    first.outcome_category = "provider_attempt_unresolved"
    await repository.schedule_retry(
        token,
        datetime.now(UTC),
        error_category="provider_attempt_unresolved",
    )
    await session.commit()

    current_token = await redeliver(session, repository, job_id)
    second = await repository.reserve_provider_attempt(
        current_token, request_deadline_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    assert second is not None
    assert second.ordinal == 2
    assert await repository.adopt_provider_attempt(current_token, second.operation_id)
    await session.execute(
        update(ProviderAttempt)
        .where(ProviderAttempt.operation_id == second.operation_id)
        .values(request_deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    await ImportPipeline(repository, cipher()).run(
        job_id,
        current_token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([]),
        ),
    )

    exhausted = await job(session, job_id)
    assert exhausted.status is ImportStatus.FAILED
    assert exhausted.provider_count == 2
    assert exhausted.safe_error_category == "provider_attempt_unresolved"


@pytest.mark.asyncio
async def test_unattached_candidate_payload_does_not_bypass_provider_operation_fence(
    session: AsyncSession,
) -> None:
    """Treating an orphan payload as success would bypass the operation-token attachment fence."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.URL, plaintext=b"https://recipes.example/lentils"
    )
    assert await repository.advance_stage(token, ImportStage.FETCHING, checkpoint_content_hash=None)
    assert await repository.advance_stage(
        token, ImportStage.EXTRACTING, checkpoint_content_hash="f" * 64
    )
    assert await repository.advance_stage(
        token, ImportStage.MODEL_EXTRACTING, checkpoint_content_hash=None
    )
    attempt = await repository.reserve_provider_attempt(
        token, request_deadline_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    assert attempt is not None
    assert await repository.adopt_provider_attempt(token, attempt.operation_id)
    await repository.store_pipeline_payload(
        token,
        "candidate",
        candidate(source_url="https://recipes.example/lentils").model_dump_json().encode(),
        cipher(),
    )
    await session.commit()

    await ImportPipeline(repository, cipher()).run(
        job_id,
        token,
        adapters(
            RecordingFetcher(),
            RecordingDeterministicExtractor(ParseError(ParseFailureCode.NO_RECIPE_FOUND)),
            RecordingModelExtractor([]),
        ),
    )

    fenced = await job(session, job_id)
    assert fenced.stage is ImportStage.MODEL_EXTRACTING
    assert fenced.candidate_content_hash is None


@pytest.mark.asyncio
async def test_text_deterministic_candidate_has_no_source_url(session: AsyncSession) -> None:
    """Attaching a fabricated URL to a text import would misrepresent its provenance."""

    repository, job_id, token = await new_claimed_job(
        session, input_kind=ImportInputKind.TEXT, plaintext=b"Lentil soup\nCook lentils"
    )
    deterministic = RecordingDeterministicExtractor(candidate(source_url=None))
    pipeline = ImportPipeline(repository, cipher())

    await pipeline.run(
        job_id,
        token,
        adapters(RecordingFetcher(), deterministic, RecordingModelExtractor([])),
    )
    await session.commit()

    stored = await repository.load_payload(job_id, "candidate", cipher())
    assert stored is not None
    assert RecipeImportCandidate.model_validate_json(stored).source_url is None
