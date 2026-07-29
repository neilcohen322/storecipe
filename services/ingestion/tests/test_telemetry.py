import json
import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ingestion.telemetry import ImportEvent, emit_import_event, queue_import_event


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine)
    try:
        async with factory() as value:
            yield value
    finally:
        await engine.dispose()


def test_emit_import_event_logs_only_declared_safe_fields(caplog) -> None:
    logger = logging.getLogger("test-import")
    caplog.set_level(logging.INFO, logger=logger.name)

    emit_import_event(
        logger,
        ImportEvent(
            name="dispatch.published",
            job_id="job-1",
            dispatch_generation=2,
            attempt=1,
            elapsed_ms=14,
        ),
    )

    record = json.loads(caplog.records[-1].message)
    assert record == {
        "event": "dispatch.published",
        "job_id": "job-1",
        "dispatch_generation": 2,
        "attempt": 1,
        "elapsed_ms": 14,
    }
    assert "url" not in record
    assert "payload" not in record


def test_emit_import_event_does_not_propagate_logging_failures() -> None:
    class BrokenLogger:
        def info(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("logging is unavailable")

    emit_import_event(BrokenLogger(), ImportEvent(name="worker.received"))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_queued_import_event_emits_only_after_commit(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("test-import-commit")
    caplog.set_level(logging.INFO, logger=logger.name)
    await session.execute(text("SELECT 1"))

    queue_import_event(
        session,
        logger,
        ImportEvent(name="dispatch.published", job_id="job-1"),
    )

    assert caplog.records == []
    await session.commit()
    assert json.loads(caplog.records[-1].message) == {
        "event": "dispatch.published",
        "job_id": "job-1",
    }


@pytest.mark.asyncio
async def test_queued_import_event_is_discarded_on_rollback(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("test-import-rollback")
    caplog.set_level(logging.INFO, logger=logger.name)
    await session.execute(text("SELECT 1"))
    queue_import_event(
        session,
        logger,
        ImportEvent(name="recovery.scheduled", job_id="job-1"),
    )

    await session.rollback()

    assert caplog.records == []
