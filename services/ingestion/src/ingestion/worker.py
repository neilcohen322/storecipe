import asyncio
import logging
import os
import socket
import time
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import TypedDict
from uuid import UUID

from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ingestion.config import Settings
from ingestion.import_models import FetchedDocument, RecipeImportCandidate
from ingestion.jsonld import parse_recipe_jsonld
from ingestion.orchestration import LeaseToken
from ingestion.pipeline import AiBudgetPolicy, ImportAdapters
from ingestion.repositories.imports import ImportRepository
from ingestion.telemetry import ImportEvent, emit_import_event, queue_import_event


class SmokeResult(TypedDict):
    status: str
    worker: str


celery_app = Celery(
    "storecipe-ingestion",
    broker=os.getenv("INGESTION_CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=None,
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 900},
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
)

ImportRunner = Callable[[UUID, int], Coroutine[object, object, None]]
_import_runner: ImportRunner | None = None
logger = logging.getLogger(__name__)


def _model_if_enabled[Model](enabled: bool, model: Model) -> Model | None:
    return model if enabled else None


def _ai_budget_policy(settings: Settings) -> AiBudgetPolicy:
    from ingestion.ai_extractor import PROMPT_VERSION

    return AiBudgetPolicy(
        daily_limit=settings.ai_daily_token_limit,
        reservation_tokens=settings.ai_invocation_reservation_tokens,
        provider_name="openrouter",
        model_name=settings.openrouter_model,
        prompt_version=PROMPT_VERSION,
    )


def _build_import_adapters(settings: Settings, model: object, catalog: object) -> ImportAdapters:
    from ingestion.fetcher import SafeFetcher

    return ImportAdapters(
        fetcher=SafeFetcher(),
        deterministic=_JsonLdAdapter(),
        model=_model_if_enabled(settings.ai_extraction_enabled, model),  # type: ignore[arg-type]
        catalog=catalog,  # type: ignore[arg-type]
        variant_registry=settings.server_rendered_variant_registry,
    )


async def _record_receipt_and_claim(
    repository: ImportRepository,
    job_id: UUID,
    owner: str,
    dispatch_generation: int,
    *,
    lease_seconds: int = 60,
) -> LeaseToken | None:
    started = time.monotonic()
    token = await repository.record_receipt_and_claim(
        job_id,
        owner,
        dispatch_generation,
        lease_seconds=lease_seconds,
    )
    event = ImportEvent(
        name="worker.received" if token is not None else "worker.stale",
        job_id=str(job_id),
        dispatch_generation=dispatch_generation,
        elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
        error_category=None if token is not None else "claim_rejected",
    )
    if token is None:
        emit_import_event(logger, event)
    else:
        queue_import_event(repository.session, logger, event)
    return token


async def _renew_lease_loop(
    factory: async_sessionmaker[AsyncSession], token: object, *, interval_seconds: float = 20
) -> None:
    """Renew a worker lease independently while a network stage is running."""

    from ingestion.orchestration import LeaseToken, StaleLease
    from ingestion.repositories.imports import ImportRepository

    if not isinstance(token, LeaseToken):
        return
    while True:
        await asyncio.sleep(interval_seconds)
        async with factory() as session:
            try:
                await ImportRepository(session).renew_lease(token, lease_seconds=60)
                await session.commit()
            except StaleLease:
                await session.rollback()
                return


def configure_import_runner(runner: ImportRunner) -> None:
    """Install the process-scoped runtime assembled by the worker entry point."""

    global _import_runner
    _import_runner = runner


def build_import_runner() -> ImportRunner:
    """Assemble the process boundary used by the Celery task.

    Construction is deliberately lazy: importing this module for API tests or
    Celery inspection must not open a database connection or require secrets.
    A fresh async engine is used for each task invocation so Celery's synchronous
    task wrapper can safely create and close its event loop per delivery.
    """

    from ingestion.ai_extractor import AiohttpOpenRouterTransport, AiRecipeExtractor
    from ingestion.catalog_client import build_catalog_client
    from ingestion.config import get_settings
    from ingestion.crypto import PayloadCipher
    from ingestion.database import create_engine
    from ingestion.orchestration import StaleLease
    from ingestion.pipeline import ImportPipeline
    from ingestion.repositories.budgets import AiBudgetRepository
    from ingestion.repositories.imports import ImportRepository

    settings = get_settings()
    cipher = PayloadCipher.from_settings(settings)
    owner = f"{socket.gethostname()}:{os.getpid()}"
    model = AiRecipeExtractor(
        AiohttpOpenRouterTransport(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.openrouter_model,
        )
    )
    catalog = build_catalog_client(settings)

    async def run(job_id: UUID, dispatch_generation: int) -> None:
        engine = create_engine()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as validation_session:
                await ImportRepository(validation_session).assert_payload_keys_available(cipher)
            async with factory() as session:
                repository = ImportRepository(session)
                token = await _record_receipt_and_claim(
                    repository,
                    job_id,
                    owner,
                    dispatch_generation,
                    lease_seconds=60,
                )
                if token is None:
                    return
                await session.commit()
                heartbeat = asyncio.create_task(_renew_lease_loop(factory, token))
                try:
                    await ImportPipeline(
                        repository,
                        cipher,
                        budgets=AiBudgetRepository(session),
                        budget_policy=_ai_budget_policy(settings),
                    ).run(
                        job_id,
                        token,
                        _build_import_adapters(settings, model, catalog),
                    )
                except StaleLease:
                    await session.rollback()
                    emit_import_event(
                        logger,
                        ImportEvent(
                            name="worker.stale",
                            job_id=str(job_id),
                            dispatch_generation=dispatch_generation,
                            error_category="stale_lease",
                        ),
                    )
                    return
                finally:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat
                await session.commit()
        finally:
            await engine.dispose()

    return run


class _JsonLdAdapter:
    async def extract(self, document: FetchedDocument) -> RecipeImportCandidate:
        return parse_recipe_jsonld(document)


@celery_app.task(name="ingestion.smoke")  # type: ignore[untyped-decorator]
def smoke() -> SmokeResult:
    """Verify that the API, Redis, and worker are wired together."""

    return {"status": "ok", "worker": "ingestion"}


@celery_app.task(name="ingestion.process_import")  # type: ignore[untyped-decorator]
def process_import(job_id: str, dispatch_generation: int) -> None:
    """Run one durable dispatch; PostgreSQL remains the result backend."""

    global _import_runner
    if _import_runner is None:
        _import_runner = build_import_runner()
    asyncio.run(_import_runner(UUID(job_id), dispatch_generation))
