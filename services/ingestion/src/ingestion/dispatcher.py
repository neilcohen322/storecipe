"""Durable outbox publication for import task notifications."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ingestion.models import ImportDispatch, ImportJob, ImportStatus

Publish = Callable[[UUID, int], Awaitable[None]]
TERMINAL_STATUSES = frozenset(
    {
        ImportStatus.COMPLETED,
        ImportStatus.REVIEW_REQUIRED,
        ImportStatus.FAILED,
        ImportStatus.CANCELLED,
        ImportStatus.TIMED_OUT,
    }
)


class OutboxDispatcher:
    def __init__(
        self,
        session: AsyncSession,
        publish: Publish,
        *,
        receipt_timeout: timedelta = timedelta(seconds=30),
        max_backoff: timedelta = timedelta(minutes=5),
    ) -> None:
        self._session = session
        self._publish = publish
        self._receipt_timeout = receipt_timeout
        self._max_backoff = max_backoff

    async def dispatch_due(self, *, now: datetime | None = None, limit: int = 100) -> int:
        current = now or datetime.now(UTC)
        rows = list(
            await self._session.scalars(
                select(ImportDispatch)
                .where(
                    ImportDispatch.due_at <= current,
                    ImportDispatch.job.has(
                        ImportJob.dispatch_generation == ImportDispatch.generation
                    ),
                    ImportDispatch.job.has(ImportJob.status.not_in(TERMINAL_STATUSES)),
                    ImportDispatch.received_at.is_(None),
                    or_(
                        ImportDispatch.published_at.is_(None),
                        ImportDispatch.job.has(ImportJob.next_attempt_at <= current),
                    ),
                )
                .order_by(ImportDispatch.due_at, ImportDispatch.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        published = 0
        for dispatch in rows:
            dispatch.publication_attempts += 1
            try:
                await self._publish(dispatch.job_id, dispatch.generation)
            except Exception:
                delay = min(2 ** min(dispatch.publication_attempts - 1, 8), 300)
                dispatch.due_at = current + min(timedelta(seconds=delay), self._max_backoff)
                continue
            dispatch.published_at = current
            job = await self._session.get(ImportJob, dispatch.job_id, with_for_update=True)
            if job is not None:
                job.dispatch_count += 1
                job.last_published_at = current
                job.next_attempt_at = current + self._receipt_timeout
            published += 1
        await self._session.flush()
        return published


async def run_forever(*, interval_seconds: float = 1.0) -> None:
    from ingestion.database import create_engine
    from ingestion.worker import celery_app

    engine = create_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def publish(job_id: UUID, generation: int) -> None:
        celery_app.send_task("ingestion.process_import", args=[str(job_id), generation])

    try:
        while True:
            async with factory.begin() as session:
                await OutboxDispatcher(session, publish).dispatch_due()
            await asyncio.sleep(interval_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_forever())
