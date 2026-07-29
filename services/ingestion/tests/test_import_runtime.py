import base64
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import ingestion.worker as ingestion_worker
from ingestion.crypto import PayloadCipher
from ingestion.dispatcher import OutboxDispatcher
from ingestion.models import (
    Base,
    DispatchType,
    ImportDispatch,
    ImportInputKind,
    ImportJob,
    ImportPayload,
    ImportStage,
    ImportStatus,
)
from ingestion.reconciler import ImportReconciler
from ingestion.repositories.imports import ImportRepository
from ingestion.worker import celery_app


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"ingestion": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as value:
            yield value
    finally:
        await engine.dispose()


def job(now: datetime, **values: object) -> ImportJob:
    return ImportJob(
        owner_subject="auth0|owner",
        input_kind=ImportInputKind.TEXT,
        request_fingerprint="a" * 64,
        created_at=now,
        updated_at=now,
        **values,
    )


@pytest.mark.asyncio
async def test_dispatcher_publishes_due_generation_and_sets_receipt_deadline(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ingestion.dispatcher")
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(now)
    session.add(target)
    await session.flush()
    session.add(
        ImportDispatch(
            job_id=target.id,
            generation=1,
            dispatch_type=DispatchType.PROCESS,
            due_at=now,
        )
    )
    await session.flush()
    published: list[tuple[object, int]] = []

    async def publish(job_id: object, generation: int) -> None:
        published.append((job_id, generation))

    assert await OutboxDispatcher(session, publish).dispatch_due(now=now) == 1
    assert published == [(target.id, 1)]
    assert target.dispatch_count == 1
    assert target.next_attempt_at == now + timedelta(seconds=30)
    assert caplog.records == []
    await session.commit()
    events = [json.loads(record.message) for record in caplog.records]
    assert events[-1]["event"] == "dispatch.published"
    assert events[-1]["job_id"] == str(target.id)
    assert events[-1]["dispatch_generation"] == 1
    assert events[-1]["queue_delay_ms"] == 0


@pytest.mark.asyncio
async def test_dispatcher_logs_publication_retry_without_exposing_exception(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ingestion.dispatcher")
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(now)
    session.add(target)
    await session.flush()
    session.add(
        ImportDispatch(
            job_id=target.id,
            generation=1,
            dispatch_type=DispatchType.PROCESS,
            due_at=now,
        )
    )
    await session.flush()

    async def publish(job_id: object, generation: int) -> None:
        raise RuntimeError("provider secret must not be logged")

    assert await OutboxDispatcher(session, publish).dispatch_due(now=now) == 0
    assert caplog.records == []
    await session.commit()
    events = [json.loads(record.message) for record in caplog.records]
    assert events[-1]["event"] == "dispatch.publish_retry"
    assert events[-1]["error_category"] == "publication_failure"
    assert "provider secret" not in caplog.text


@pytest.mark.asyncio
async def test_worker_logs_receipt_and_duplicate_claim_outcomes(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ingestion.worker")
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(now)
    session.add(target)
    await session.flush()
    session.add(
        ImportDispatch(
            job_id=target.id,
            generation=1,
            dispatch_type=DispatchType.PROCESS,
            due_at=now,
        )
    )
    await session.flush()

    repository = ImportRepository(session)
    first = await ingestion_worker._record_receipt_and_claim(repository, target.id, "worker-a", 1)
    assert caplog.records == []
    await session.commit()
    duplicate = await ingestion_worker._record_receipt_and_claim(
        repository, target.id, "worker-b", 1
    )

    assert first is not None
    assert duplicate is None
    events = [json.loads(record.message) for record in caplog.records]
    assert [event["event"] for event in events[-2:]] == ["worker.received", "worker.stale"]


@pytest.mark.asyncio
async def test_published_current_generation_can_be_claimed_before_receipt_deadline(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    target = job(now)
    session.add(target)
    await session.flush()
    session.add(
        ImportDispatch(
            job_id=target.id,
            generation=1,
            dispatch_type=DispatchType.PROCESS,
            due_at=now,
        )
    )
    await session.flush()

    async def publish(job_id: object, generation: int) -> None:
        assert (job_id, generation) == (target.id, 1)

    assert await OutboxDispatcher(session, publish).dispatch_due(now=now) == 1
    await session.commit()

    token = await ImportRepository(session).record_receipt_and_claim(
        target.id, "worker", 1, lease_seconds=60
    )

    assert token is not None
    await session.refresh(target)
    assert target.last_received_at is not None


@pytest.mark.asyncio
async def test_dispatcher_does_not_republish_historical_unreceived_generations(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(now, dispatch_generation=3, next_attempt_at=now)
    session.add(target)
    await session.flush()
    for generation in (1, 2, 3):
        session.add(
            ImportDispatch(
                job_id=target.id,
                generation=generation,
                dispatch_type=DispatchType.PROCESS,
                due_at=now,
                published_at=now,
            )
        )
    await session.flush()
    published: list[tuple[object, int]] = []

    async def publish(job_id: object, generation: int) -> None:
        published.append((job_id, generation))

    assert await OutboxDispatcher(session, publish).dispatch_due(now=now) == 1
    assert published == [(target.id, 3)]


@pytest.mark.asyncio
async def test_dispatcher_does_not_publish_terminal_job_dispatch(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    target = job(
        now,
        status=ImportStatus.CANCELLED,
        stage=ImportStage.CANCELLED,
        terminal_at=now,
    )
    session.add(target)
    await session.flush()
    session.add(
        ImportDispatch(
            job_id=target.id,
            generation=target.dispatch_generation,
            dispatch_type=DispatchType.PROCESS,
            due_at=now,
        )
    )
    await session.flush()
    published: list[tuple[object, int]] = []

    async def publish(job_id: object, generation: int) -> None:
        published.append((job_id, generation))

    assert await OutboxDispatcher(session, publish).dispatch_due(now=now) == 0
    assert published == []


@pytest.mark.asyncio
async def test_reconciler_requeues_expired_current_receipt_once(session: AsyncSession) -> None:
    """A lost worker receipt creates one current-generation retry and no historical republish."""

    now = datetime(2026, 7, 27, tzinfo=UTC)
    target = job(
        now - timedelta(seconds=31),
        status=ImportStatus.PROCESSING,
        stage=ImportStage.FETCHING,
        next_attempt_at=now - timedelta(seconds=1),
        dispatch_generation=1,
    )
    session.add(target)
    await session.flush()
    session.add(
        ImportDispatch(
            job_id=target.id,
            generation=1,
            dispatch_type=DispatchType.PROCESS,
            due_at=now - timedelta(seconds=31),
            published_at=now - timedelta(seconds=31),
        )
    )
    await session.flush()

    assert await ImportReconciler(session).reconcile(now=now) == 1
    generations = list(
        await session.scalars(
            select(ImportDispatch.generation)
            .where(ImportDispatch.job_id == target.id)
            .order_by(ImportDispatch.generation)
        )
    )
    assert generations == [1, 2]
    assert target.dispatch_generation == 2


@pytest.mark.asyncio
async def test_reconciler_expires_deadline_before_catalog_intent(session: AsyncSession) -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(
        now,
        status=ImportStatus.PROCESSING,
        stage=ImportStage.EXTRACTING,
        deadline_at=now - timedelta(seconds=1),
        next_attempt_at=now,
    )
    session.add(target)
    await session.flush()

    assert await ImportReconciler(session).reconcile(now=now) == 0
    assert target.status is ImportStatus.TIMED_OUT
    assert target.terminal_at == now
    assert target.safe_error_category == "import_deadline_exceeded"


@pytest.mark.asyncio
async def test_reconciler_terminalizes_active_cancellation_after_worker_loss(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(
        now,
        status=ImportStatus.PROCESSING,
        stage=ImportStage.FETCHING,
        lease_expires_at=now - timedelta(seconds=1),
        cancel_requested_at=now - timedelta(seconds=2),
        next_attempt_at=now,
    )
    session.add(target)
    await session.flush()

    assert await ImportReconciler(session).reconcile(now=now) == 0
    assert target.status is ImportStatus.CANCELLED
    assert target.stage is ImportStage.CANCELLED
    assert target.terminal_at == now


@pytest.mark.asyncio
async def test_reconciler_schedules_lost_catalog_pending_work_and_reports_safe_alert(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ingestion.reconciler")
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(
        now - timedelta(minutes=31),
        status=ImportStatus.PROCESSING,
        stage=ImportStage.CATALOG_PENDING,
        catalog_pending_since=now - timedelta(minutes=31),
        next_attempt_at=now,
        catalog_count=4,
        safe_error_category="catalog_timeout",
    )
    session.add(target)
    await session.flush()

    assert await ImportReconciler(session).reconcile(now=now) == 1
    assert caplog.records == []
    await session.commit()
    dispatch = await session.scalar(
        select(ImportDispatch).where(ImportDispatch.job_id == target.id)
    )
    assert dispatch is not None
    assert dispatch.generation == 2
    alerts = await ImportReconciler(session).catalog_pending_alerts(now=now)
    safe_alerts = [
        (item.severity, item.safe_error_category, item.catalog_attempt_count) for item in alerts
    ]
    assert safe_alerts == [("critical", "catalog_timeout", 4)]
    events = [json.loads(record.message) for record in caplog.records]
    recovery = next(event for event in events if event["event"] == "recovery.scheduled")
    assert recovery["job_id"] == str(target.id)
    assert recovery["catalog_pending_age_ms"] == 1_860_000
    pending = next(event for event in events if event["event"] == "catalog.pending")
    assert pending["job_id"] == str(target.id)
    assert pending["catalog_pending_age_ms"] == 1_860_000


@pytest.mark.asyncio
async def test_retention_expires_review_payload_after_thirty_days(session: AsyncSession) -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    target = job(
        now - timedelta(days=31),
        status=ImportStatus.REVIEW_REQUIRED,
        stage=ImportStage.REVIEW_REQUIRED,
        terminal_at=now - timedelta(days=31),
    )
    session.add(target)
    await session.flush()
    encrypted = PayloadCipher.from_keyring(
        active_key_id="current",
        keyring=f"current={base64.b64encode(b'c' * 32).decode()}",
    ).encrypt(b"safe candidate")
    session.add(
        ImportPayload(
            job_id=target.id,
            payload_type="candidate",
            content_hash="a" * 64,
            encryption_key_id=encrypted.key_id,
            algorithm=encrypted.algorithm,
            nonce=encrypted.nonce,
            ciphertext=encrypted.ciphertext,
        )
    )
    await session.flush()

    payloads, metadata = await ImportReconciler(session).sweep_retention(now=now)
    remaining = await session.scalar(select(ImportPayload).where(ImportPayload.job_id == target.id))
    await session.refresh(target)

    assert payloads == 1
    assert metadata == 0
    assert remaining is None
    assert target.safe_error_category == "review_payload_expired"


def test_celery_uses_postgres_for_results_instead_of_a_result_backend() -> None:
    assert celery_app.backend.as_uri() == "disabled://"
    assert celery_app.conf.task_acks_late is True


def test_worker_omits_model_adapter_when_ai_extraction_is_disabled() -> None:
    model = object()

    assert ingestion_worker._model_if_enabled(False, model) is None
    assert ingestion_worker._model_if_enabled(True, model) is model
    assert celery_app.conf.task_ignore_result is True
