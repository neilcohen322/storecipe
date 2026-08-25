"""Persistence and PostgreSQL serialization for deleted account subjects."""

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.models import AccountDeletionTombstone

_LOCK_NAMESPACE = b"storecipe:ingestion:account-subject:v1\0"


def subject_advisory_lock_key(subject: str) -> int:
    """Return a stable signed 64-bit PostgreSQL advisory-lock key."""

    digest = sha256(_LOCK_NAMESPACE + subject.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class AccountDeletionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def acquire_subject_lock(self, subject: str) -> None:
        """Serialize subject writes until the current transaction ends."""

        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql":
            # Unit tests use SQLite; production admission is PostgreSQL-only.
            return
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": subject_advisory_lock_key(subject)},
        )

    async def is_tombstoned(
        self,
        subject: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        checked_at = now or datetime.now(UTC)
        marker = await self.session.scalar(
            select(AccountDeletionTombstone.subject).where(
                AccountDeletionTombstone.subject == subject,
                AccountDeletionTombstone.expires_at > checked_at,
            )
        )
        return marker is not None

    async def upsert(
        self,
        subject: str,
        *,
        deleted_at: datetime,
        expires_at: datetime,
    ) -> AccountDeletionTombstone:
        tombstone = await self.session.get(AccountDeletionTombstone, subject)
        if tombstone is None:
            tombstone = AccountDeletionTombstone(
                subject=subject,
                deleted_at=deleted_at,
                expires_at=expires_at,
            )
            self.session.add(tombstone)
        else:
            tombstone.deleted_at = deleted_at
            tombstone.expires_at = expires_at
        await self.session.flush()
        return tombstone
