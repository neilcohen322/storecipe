from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ingestion.config import get_settings


def create_engine() -> AsyncEngine:
    """Build the process-scoped engine; constructed in the app lifespan, not at import."""

    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=0,
    )
