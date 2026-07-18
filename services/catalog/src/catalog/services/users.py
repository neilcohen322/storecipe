"""User resolution shared by the recipe and rating services."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.models import User


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
