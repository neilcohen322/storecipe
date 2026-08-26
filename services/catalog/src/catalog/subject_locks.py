"""Transaction-scoped serialization for work owned by an Auth0 subject."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_subject(session: AsyncSession, subject: str) -> None:
    """Serialize tombstones and subject-owned writes for this transaction.

    PostgreSQL computes the signed 64-bit key itself, avoiding differences in
    application hash implementations. Non-PostgreSQL unit-test databases rely
    on their own transaction serialization.
    """

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with session.no_autoflush:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
            {"subject": subject},
        )
