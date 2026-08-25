"""Account-deletion tombstone admission and recording."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.models import (
    AccountDeletionTombstone,
    AiDailyUsage,
    ImportJob,
    IngredientNormalizationAttempt,
    IngredientNormalizationOperation,
    LlmInvocation,
)
from ingestion.repositories.account_deletions import AccountDeletionRepository

ACCOUNT_TOMBSTONE_RETENTION = timedelta(days=90)


class AccountDeleted(Exception):
    """Raised when a subject is denied by an active deletion tombstone."""


class AccountDeletionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = AccountDeletionRepository(session)

    async def is_deleted(self, subject: str) -> bool:
        """Cheap admission check in its own short read transaction."""

        async with self._session.begin():
            return await self._repository.is_tombstoned(subject)

    async def tombstone(self, subject: str) -> AccountDeletionTombstone:
        """Record the marker and erase retained subject data atomically."""

        deleted_at = datetime.now(UTC)
        expires_at = deleted_at + ACCOUNT_TOMBSTONE_RETENTION
        async with self._session.begin():
            await self._repository.acquire_subject_lock(subject)
            tombstone = await self._repository.upsert(
                subject,
                deleted_at=deleted_at,
                expires_at=expires_at,
            )
            # Delete dependent ledgers before their parent jobs/operations.
            await self._session.execute(
                delete(LlmInvocation).where(LlmInvocation.owner_subject == subject)
            )
            await self._session.execute(
                delete(IngredientNormalizationAttempt).where(
                    IngredientNormalizationAttempt.normalization_operation_id.in_(
                        select(IngredientNormalizationOperation.id).where(
                            IngredientNormalizationOperation.owner_subject == subject
                        )
                    )
                )
            )
            await self._session.execute(
                delete(IngredientNormalizationOperation).where(
                    IngredientNormalizationOperation.owner_subject == subject
                )
            )
            await self._session.execute(
                delete(AiDailyUsage).where(AiDailyUsage.owner_subject == subject)
            )
            await self._session.execute(delete(ImportJob).where(ImportJob.owner_subject == subject))
            return tombstone
