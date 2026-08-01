"""User resolution shared by the recipe and rating services."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.models import User


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
    """Return the user for ``subject``, creating it on first sight.

    Recovers from a concurrent insert racing the same auth subject.
    """
    user = await session.scalar(select(User).where(User.auth_subject == subject))
    if user is not None:
        return user

    user = User(auth_subject=subject)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(User).where(User.auth_subject == subject))
        if existing is None:
            raise
        return existing
    return user
