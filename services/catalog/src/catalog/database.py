from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from catalog.config import get_settings


def create_engine() -> AsyncEngine:
    """Build the process-scoped engine; constructed in the app lifespan, not at import."""

    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=0,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


# Route modules depend on the session through this alias so they need not import
# SQLAlchemy types directly (see tests/test_architecture.py).
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
