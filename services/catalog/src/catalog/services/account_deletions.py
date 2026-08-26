"""Account-deletion admission and durable tombstone creation."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.account_deletion_models import AccountDeletion
from catalog.deletion_journal import (
    DeletionJournal,
    DeletionJournalEntry,
    DeletionJournalUnavailable,
    journal_entry_from_deletion,
)
from catalog.subject_locks import lock_subject

logger = logging.getLogger(__name__)
_RETENTION = timedelta(days=90)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def request_account_deletion(
    session: AsyncSession,
    subject: str,
    request_id: str,
    *,
    journal: DeletionJournal | None,
) -> AccountDeletion:
    await lock_subject(session, subject)
    deletion = await session.scalar(
        select(AccountDeletion).where(AccountDeletion.auth_subject == subject)
    )
    if (
        deletion is not None
        and deletion.status == "completed"
        and deletion.expires_at is not None
        and _as_utc(deletion.expires_at) <= datetime.now(UTC)
    ):
        await session.delete(deletion)
        await session.flush()
        deletion = None
    entry: DeletionJournalEntry | None = None
    try:
        if deletion is None:
            if journal is None:
                raise DeletionJournalUnavailable()
            requested_at = datetime.now(UTC)
            expires_at = requested_at + _RETENTION
            deletion = AccountDeletion(
                id=uuid4(),
                auth_subject=subject,
                request_id=request_id[:128],
                expires_at=expires_at,
                created_at=requested_at,
                updated_at=requested_at,
            )
            entry = DeletionJournalEntry(
                deletion_id=deletion.id,
                subject=subject,
                requested_at=requested_at,
                expires_at=expires_at,
                request_id=deletion.request_id,
            )
            await journal.write(entry)
            session.add(deletion)
            await session.flush()
        await session.commit()
    except Exception:
        if journal is not None and entry is not None:
            try:
                await journal.abort(entry)
            except DeletionJournalUnavailable:
                logger.error(
                    "account_deletion.journal_abort_failed",
                    extra={"deletion_id": str(entry.deletion_id)},
                )
        raise
    if journal is not None:
        if entry is None:
            entry = journal_entry_from_deletion(
                deletion_id=deletion.id,
                subject=subject,
                requested_at=_as_utc(deletion.created_at),
                request_id=deletion.request_id,
            )
        try:
            await journal.commit(entry)
        except DeletionJournalUnavailable:
            logger.error(
                "account_deletion.journal_commit_deferred",
                extra={"deletion_id": str(deletion.id)},
            )
            return deletion
        if not deletion.journal_committed:
            deletion.journal_committed = True
            deletion.updated_at = datetime.now(UTC)
            await session.commit()
    return deletion
