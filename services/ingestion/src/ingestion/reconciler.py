"""Broker-loss recovery, deadline enforcement, and import retention."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ingestion.crypto import EncryptedPayload, PayloadCipher
from ingestion.models import (
    DispatchType,
    ImportDispatch,
    ImportJob,
    ImportPayload,
    ImportStage,
    ImportStatus,
)
from ingestion.repositories.budgets import AiBudgetRepository
from ingestion.repositories.imports import ImportRepository
from ingestion.telemetry import ImportEvent, emit_import_event, queue_import_event

TERMINAL = {
    ImportStatus.COMPLETED,
    ImportStatus.REVIEW_REQUIRED,
    ImportStatus.FAILED,
    ImportStatus.CANCELLED,
    ImportStatus.TIMED_OUT,
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogPendingAlert:
    job_id: str
    severity: str
    age_seconds: int
    safe_error_category: str | None
    catalog_attempt_count: int
    next_attempt_at: datetime | None


class ImportReconciler:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reconcile(self, *, now: datetime | None = None, limit: int = 100) -> int:
        current = now or datetime.now(UTC)
        settled = await AiBudgetRepository(self._session).settle_expired_ambiguities(now=current)
        if settled:
            await self._session.commit()
            emit_import_event(
                logger,
                ImportEvent(name="budget.ambiguity_settled", status="completed"),
            )
        jobs = list(
            await self._session.scalars(
                select(ImportJob)
                .where(
                    ImportJob.status.not_in(TERMINAL),
                    or_(ImportJob.next_attempt_at.is_(None), ImportJob.next_attempt_at <= current),
                    or_(
                        ImportJob.lease_expires_at.is_(None),
                        ImportJob.lease_expires_at <= current,
                    ),
                )
                .order_by(ImportJob.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        scheduled = 0
        events: list[ImportEvent] = []
        scheduled_events: list[ImportEvent] = []
        for job in jobs:
            if job.cancel_requested_at is not None and job.catalog_pending_since is None:
                job.status = ImportStatus.CANCELLED
                job.stage = ImportStage.CANCELLED
                job.terminal_at = current
                job.next_attempt_at = None
                job.lease_owner = None
                job.lease_expires_at = None
                events.append(
                    ImportEvent(
                        name="recovery.cancelled",
                        job_id=str(job.id),
                        dispatch_generation=job.dispatch_generation,
                        status=job.status.value,
                    )
                )
                continue
            if (
                job.deadline_at is not None
                and job.deadline_at <= current
                and job.catalog_pending_since is None
            ):
                job.status = ImportStatus.TIMED_OUT
                job.stage = ImportStage.TIMED_OUT
                job.terminal_at = current
                job.next_attempt_at = None
                job.safe_error_category = "import_deadline_exceeded"
                events.append(
                    ImportEvent(
                        name="recovery.timed_out",
                        job_id=str(job.id),
                        dispatch_generation=job.dispatch_generation,
                        error_category=job.safe_error_category,
                        status=job.status.value,
                    )
                )
                continue
            has_open_dispatch = await self._session.scalar(
                select(
                    exists().where(
                        ImportDispatch.job_id == job.id,
                        ImportDispatch.generation == job.dispatch_generation,
                        ImportDispatch.received_at.is_(None),
                        ImportDispatch.published_at.is_(None),
                    )
                )
            )
            if has_open_dispatch:
                continue
            job.dispatch_generation += 1
            job.next_attempt_at = current
            self._session.add(
                ImportDispatch(
                    job_id=job.id,
                    generation=job.dispatch_generation,
                    dispatch_type=DispatchType.PROCESS,
                    due_at=current,
                )
            )
            scheduled += 1
            scheduled_events.append(
                ImportEvent(
                    name="recovery.scheduled",
                    job_id=str(job.id),
                    dispatch_generation=job.dispatch_generation,
                    catalog_pending_age_ms=self._catalog_pending_age_ms(job, current),
                )
            )
        await self._session.flush()
        for event in events:
            queue_import_event(self._session, logger, event)
        for event in scheduled_events:
            queue_import_event(self._session, logger, event)
        return scheduled

    async def catalog_pending_alerts(
        self, *, now: datetime | None = None
    ) -> list[CatalogPendingAlert]:
        current = now or datetime.now(UTC)
        jobs = list(
            await self._session.scalars(
                select(ImportJob).where(
                    ImportJob.status == ImportStatus.PROCESSING,
                    ImportJob.stage == ImportStage.CATALOG_PENDING,
                    ImportJob.catalog_pending_since.is_not(None),
                    ImportJob.catalog_pending_since <= current - timedelta(minutes=5),
                )
            )
        )
        alerts: list[CatalogPendingAlert] = []
        for job in jobs:
            age_ms = self._catalog_pending_age_ms(job, current) or 0
            alert = CatalogPendingAlert(
                job_id=str(job.id),
                severity=(
                    "critical"
                    if job.catalog_pending_since is not None
                    and job.catalog_pending_since <= current - timedelta(minutes=30)
                    else "warning"
                ),
                age_seconds=(
                    int((current - job.catalog_pending_since).total_seconds())
                    if job.catalog_pending_since is not None
                    else 0
                ),
                safe_error_category=job.safe_error_category,
                catalog_attempt_count=job.catalog_count,
                next_attempt_at=job.next_attempt_at,
            )
            alerts.append(alert)
            emit_import_event(
                logger,
                ImportEvent(
                    name="catalog.pending",
                    job_id=str(job.id),
                    dispatch_generation=job.dispatch_generation,
                    stage=job.stage.value,
                    attempt=job.catalog_count,
                    catalog_pending_age_ms=age_ms,
                    error_category=job.safe_error_category,
                    status=job.status.value,
                ),
            )
        return alerts

    @staticmethod
    def _catalog_pending_age_ms(job: ImportJob, current: datetime) -> int | None:
        if job.catalog_pending_since is None:
            return None
        return max(0, int((current - job.catalog_pending_since).total_seconds() * 1000))

    async def sweep_retention(self, *, now: datetime | None = None) -> tuple[int, int]:
        current = now or datetime.now(UTC)
        review_cutoff = current - timedelta(days=30)
        normal_cutoff = current - timedelta(days=7)
        payload_job_ids = select(ImportJob.id).where(
            or_(
                (
                    (ImportJob.status == ImportStatus.REVIEW_REQUIRED)
                    & (ImportJob.terminal_at <= review_cutoff)
                ),
                (
                    ImportJob.status.in_(
                        {
                            ImportStatus.COMPLETED,
                            ImportStatus.FAILED,
                            ImportStatus.CANCELLED,
                            ImportStatus.TIMED_OUT,
                        }
                    )
                    & (ImportJob.terminal_at <= normal_cutoff)
                ),
            )
        )
        await self._session.execute(
            update(ImportJob)
            .execution_options(synchronize_session="fetch")
            .where(
                ImportJob.status == ImportStatus.REVIEW_REQUIRED,
                ImportJob.terminal_at <= review_cutoff,
                or_(
                    ImportJob.safe_error_category.is_(None),
                    ImportJob.safe_error_category != "review_payload_expired",
                ),
            )
            .values(safe_error_category="review_payload_expired")
        )
        payload_result = await self._session.execute(
            delete(ImportPayload).where(ImportPayload.job_id.in_(payload_job_ids))
        )
        metadata_result = await self._session.execute(
            delete(ImportJob).where(
                ImportJob.status.in_(TERMINAL),
                ImportJob.terminal_at <= current - timedelta(days=90),
            )
        )
        queue_import_event(
            self._session,
            logger,
            ImportEvent(name="retention.swept", status="completed"),
        )
        return (
            int(payload_result.rowcount or 0) if isinstance(payload_result, CursorResult) else 0,
            int(metadata_result.rowcount or 0) if isinstance(metadata_result, CursorResult) else 0,
        )

    async def reencrypt_payloads(self, cipher: PayloadCipher, *, limit: int = 100) -> int:
        """Rewrite retained payloads encrypted under an older configured key."""

        payloads = list(
            await self._session.scalars(
                select(ImportPayload)
                .where(ImportPayload.encryption_key_id != cipher.active_key_id)
                .order_by(ImportPayload.updated_at, ImportPayload.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        rewritten = 0
        for payload in payloads:
            plaintext = cipher.decrypt(
                EncryptedPayload(
                    key_id=payload.encryption_key_id,
                    algorithm=payload.algorithm,
                    nonce=payload.nonce,
                    ciphertext=payload.ciphertext,
                )
            )
            encrypted = cipher.encrypt(plaintext)
            payload.encryption_key_id = encrypted.key_id
            payload.algorithm = encrypted.algorithm
            payload.nonce = encrypted.nonce
            payload.ciphertext = encrypted.ciphertext
            rewritten += 1
        await self._session.flush()
        return rewritten


async def run_forever(*, interval_seconds: float = 10.0) -> None:
    from ingestion.config import get_settings
    from ingestion.crypto import PayloadCipher
    from ingestion.database import create_engine

    engine = create_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = PayloadCipher.from_settings(get_settings())
    try:
        async with factory() as validation_session:
            await ImportRepository(validation_session).assert_payload_keys_available(cipher)
        while True:
            async with factory.begin() as session:
                reconciler = ImportReconciler(session)
                await reconciler.reconcile()
                await reconciler.reencrypt_payloads(cipher)
                await reconciler.sweep_retention()
            await asyncio.sleep(interval_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_forever())
