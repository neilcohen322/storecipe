"""Health/readiness checks for catalog dependencies."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


async def check_postgres(engine: AsyncEngine) -> bool:
    """Return whether a trivial query against Postgres succeeds."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True
