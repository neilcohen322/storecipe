"""Leased, replay-safe account deletion owned by the Catalog lifespan."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from catalog.account_deletion_clients import AccountDeletionClients
from catalog.account_deletion_models import AccountDeletion
from catalog.deletion_journal import (
    DeletionJournal,
    DeletionJournalUnavailable,
    journal_entry_from_deletion,
)
from catalog.media.store import ObjectStoreUnavailable, RecipeImageStore
from catalog.models import Recipe, RecipeImage, RecipeTag, Tag, User
from catalog.subject_locks import lock_subject

logger = logging.getLogger(__name__)
_LEASE = timedelta(minutes=5)
_RETENTION = timedelta(days=90)
_LOOP_IDLE_SECONDS = 5.0


async def run_account_deletion_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    clients: AccountDeletionClients,
    redis: object,
    store: RecipeImageStore | None,
    stop: asyncio.Event,
    journal: DeletionJournal | None = None,
) -> None:
    worker_id = str(uuid4())
    while not stop.is_set():
        try:
            await _reconcile_journal(session_factory, journal)
            await _prune_expired(session_factory)
            await _purge_expired_completed_journals(journal)
            await _alert_incomplete(session_factory)
            job_id = await _claim(session_factory, worker_id)
            if job_id is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=_LOOP_IDLE_SECONDS)
                except TimeoutError:
                    continue
                return
            try:
                await _process(
                    session_factory,
                    job_id,
                    clients=clients,
                    redis=redis,
                    store=store,
                    journal=journal,
                )
            except Exception as exc:
                logger.exception("account_deletion.retry", extra={"deletion_id": str(job_id)})
                try:
                    await _retry(session_factory, job_id, type(exc).__name__)
                except Exception:
                    logger.exception(
                        "account_deletion.retry_failed", extra={"deletion_id": str(job_id)}
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("account_deletion.loop_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=_LOOP_IDLE_SECONDS)
            except TimeoutError:
                continue
            return


async def run_supervised_account_deletion_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    clients: AccountDeletionClients,
    redis: object,
    store: RecipeImageStore | None,
    stop: asyncio.Event,
    journal: DeletionJournal | None = None,
) -> None:
    """Restart the deletion worker after an unexpected exit until shutdown."""

    while not stop.is_set():
        try:
            await run_account_deletion_loop(
                session_factory,
                clients=clients,
                redis=redis,
                store=store,
                stop=stop,
                journal=journal,
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("account_deletion.worker_crashed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=_LOOP_IDLE_SECONDS)
            except TimeoutError:
                continue


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _reconcile_journal(
    factory: async_sessionmaker[AsyncSession], journal: DeletionJournal | None
) -> None:
    if journal is None:
        return
    now = datetime.now(UTC)
    await _restore_missing_from_journal(factory, journal)
    async with factory() as session:
        jobs = list(await session.scalars(select(AccountDeletion)))
    for job in jobs:
        if (
            job.status == "completed"
            and job.expires_at is not None
            and _as_utc(job.expires_at) <= now
        ):
            continue
        entry = journal_entry_from_deletion(
            deletion_id=job.id,
            subject=job.auth_subject,
            requested_at=job.created_at,
            request_id=job.request_id,
        )
        try:
            await journal.write(entry)
            await journal.commit(entry)
            await _mark_journal_committed(factory, job.id)
            if job.status == "completed":
                completed_at = _as_utc(job.completed_at) if job.completed_at is not None else now
                await journal.complete(entry, completed_at=completed_at)
        except DeletionJournalUnavailable:
            logger.error(
                "account_deletion.journal_reconcile_failed",
                extra={"deletion_id": str(job.id)},
            )


async def _restore_missing_from_journal(
    factory: async_sessionmaker[AsyncSession], journal: DeletionJournal
) -> None:
    list_active = getattr(journal, "active_entries", None)
    if list_active is None:
        return
    try:
        async for entry in list_active():
            async with factory.begin() as session:
                existing = await session.scalar(
                    select(AccountDeletion.id).where(
                        or_(
                            AccountDeletion.id == entry.deletion_id,
                            AccountDeletion.auth_subject == entry.subject,
                        )
                    )
                )
                if existing is not None:
                    continue
                session.add(
                    AccountDeletion(
                        id=entry.deletion_id,
                        auth_subject=entry.subject,
                        request_id=entry.request_id,
                        status="pending",
                        journal_committed=True,
                        expires_at=entry.expires_at,
                        created_at=entry.requested_at,
                        updated_at=datetime.now(UTC),
                    )
                )
    except DeletionJournalUnavailable:
        logger.error("account_deletion.journal_restore_failed")


async def _purge_expired_completed_journals(journal: DeletionJournal | None) -> None:
    if journal is None:
        return
    try:
        await journal.purge_expired_completed()
    except DeletionJournalUnavailable:
        logger.error("account_deletion.journal_purge_failed")


async def _prune_expired(factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC)
    async with factory.begin() as session:
        await session.execute(
            delete(AccountDeletion).where(
                AccountDeletion.status == "completed",
                AccountDeletion.expires_at.is_not(None),
                AccountDeletion.expires_at <= now,
            )
        )


async def _alert_incomplete(factory: async_sessionmaker[AsyncSession]) -> None:
    cutoff = datetime.now(UTC) - timedelta(minutes=15)
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AccountDeletion)
            .where(
                AccountDeletion.status != "completed",
                AccountDeletion.created_at <= cutoff,
            )
        )
    if count:
        logger.error("account_deletion.incomplete", extra={"incomplete_count": int(count)})


async def _mark_journal_committed(factory: async_sessionmaker[AsyncSession], job_id: UUID) -> None:
    async with factory.begin() as session:
        job = await session.get(AccountDeletion, job_id)
        if job is None or job.journal_committed:
            return
        job.journal_committed = True
        job.updated_at = datetime.now(UTC)


async def _claim(factory: async_sessionmaker[AsyncSession], worker_id: str) -> UUID | None:
    now = datetime.now(UTC)
    async with factory.begin() as session:
        job = await session.scalar(
            select(AccountDeletion)
            .where(
                AccountDeletion.status != "completed",
                AccountDeletion.journal_committed.is_(True),
                AccountDeletion.next_attempt_at <= now,
                or_(
                    AccountDeletion.lease_expires_at.is_(None),
                    AccountDeletion.lease_expires_at <= now,
                ),
            )
            .order_by(AccountDeletion.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "processing"
        job.attempts += 1
        job.lease_owner = worker_id
        job.lease_expires_at = now + _LEASE
        job.updated_at = now
        return job.id


async def _process(
    factory: async_sessionmaker[AsyncSession],
    job_id: UUID,
    *,
    clients: AccountDeletionClients,
    redis: object,
    store: RecipeImageStore | None,
    journal: DeletionJournal | None = None,
) -> None:
    async with factory() as session:
        job = await session.get(AccountDeletion, job_id)
        if job is None or job.status == "completed":
            return
        subject, request_id = job.auth_subject, job.request_id

    await clients.wipe_ingestion(subject, request_id)
    user_id, media = await _erase_catalog(factory, job_id, subject)
    if store is not None:
        for item in media:
            try:
                await store.delete(item["key"], generation=item["generation"])
            except ObjectStoreUnavailable as exc:
                raise RuntimeError("media_delete_failed") from exc
    if user_id is not None:
        await _purge_query_cache(redis, user_id)
    await clients.delete_auth0_user(subject, request_id)

    now = datetime.now(UTC)
    entry = None
    async with factory.begin() as session:
        job = await session.get(AccountDeletion, job_id, with_for_update=True)
        if job is None:
            return
        entry = journal_entry_from_deletion(
            deletion_id=job.id,
            subject=job.auth_subject,
            requested_at=job.created_at,
            request_id=job.request_id,
        )
        job.status = "completed"
        job.completed_at = now
        job.expires_at = now + _RETENTION
        job.updated_at = now
        job.last_error = None
        job.lease_owner = None
        job.lease_expires_at = None
    if journal is not None and entry is not None:
        try:
            await journal.complete(entry, completed_at=now)
        except DeletionJournalUnavailable:
            logger.error(
                "account_deletion.journal_complete_deferred",
                extra={"deletion_id": str(job_id)},
            )


async def _erase_catalog(
    factory: async_sessionmaker[AsyncSession], job_id: UUID, subject: str
) -> tuple[UUID | None, list[dict[str, str]]]:
    async with factory() as session:
        await lock_subject(session, subject)
        job = await session.get(AccountDeletion, job_id)
        if job is None:
            return None, []
        user = await session.scalar(select(User).where(User.auth_subject == subject))
        if user is not None:
            rows = (
                await session.execute(
                    select(RecipeImage.object_key, RecipeImage.object_generation)
                    .join(Recipe, Recipe.id == RecipeImage.recipe_id)
                    .where(Recipe.user_id == user.id)
                )
            ).all()
            job.catalog_user_id = user.id
            job.media_snapshot = [
                {"key": key, "generation": generation} for key, generation in rows
            ]
            await session.delete(user)
            await session.flush()
            await session.execute(
                delete(Tag).where(
                    ~exists(select(RecipeTag.tag_id).where(RecipeTag.tag_id == Tag.id))
                )
            )
        await session.commit()
        return job.catalog_user_id, list(job.media_snapshot or [])


async def _purge_query_cache(redis: object, user_id: UUID) -> None:
    scan_iter = getattr(redis, "scan_iter", None)
    delete_key = getattr(redis, "delete", None)
    if scan_iter is None or delete_key is None:
        return
    async for key in scan_iter(match=f"recipe_queries:v2:{user_id}:*"):
        await delete_key(key)


async def _retry(factory: async_sessionmaker[AsyncSession], job_id: UUID, safe_error: str) -> None:
    now = datetime.now(UTC)
    async with factory.begin() as session:
        job = await session.get(AccountDeletion, job_id, with_for_update=True)
        if job is None or job.status == "completed":
            return
        delay = min(900, 2 ** min(job.attempts, 9))
        job.status = "pending"
        job.last_error = safe_error[:256]
        job.next_attempt_at = now + timedelta(seconds=delay)
        job.updated_at = now
        job.lease_owner = None
        job.lease_expires_at = None
