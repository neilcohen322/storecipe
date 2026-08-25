"""User resolution shared by the recipe and rating services."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.account_deletion_models import AccountDeletion
from catalog.models import User
from catalog.services.errors import AccountDeleted
from catalog.subject_locks import lock_subject


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def advance_catalog_version(session: AsyncSession, user_id: UUID) -> int:
    """Atomically advance and return a user's recipe-query cache version."""
    with session.no_autoflush:
        version = await session.scalar(
            update(User)
            .where(User.id == user_id)
            .values(catalog_version=User.catalog_version + 1)
            .returning(User.catalog_version)
        )
    if version is None:
        raise RuntimeError("Cannot advance the catalog version for a missing user")
    return version


async def resolve_user(session: AsyncSession, subject: str) -> User:
    """Resolve or stage a user while holding the subject's transaction lock.

    The owning workflow must commit. That keeps tombstone admission and the
    later subject-owned write in one atomic serialization boundary.
    """
    await lock_subject(session, subject)
    deletion = await session.scalar(
        select(AccountDeletion).where(AccountDeletion.auth_subject == subject)
    )
    if deletion is not None:
        # Pending/processing jobs never expire. Completed tombstones do.
        if deletion.status != "completed":
            raise AccountDeleted()
        if deletion.expires_at is None or _as_utc(deletion.expires_at) > datetime.now(UTC):
            raise AccountDeleted()

    user = await session.scalar(select(User).where(User.auth_subject == subject))
    if user is not None:
        return user

    user = User(auth_subject=subject)
    session.add(user)
    await session.flush()
    return user
