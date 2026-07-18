"""Readiness checks for ingestion dependencies."""

import asyncio

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def check_dependencies(engine: AsyncEngine, redis: Redis) -> tuple[dict[str, str], list[str]]:
    """Probe Postgres and Redis concurrently.

    Returns a ``{dependency: status}`` map and the list of failed dependencies.
    A non-``Exception`` ``BaseException`` (e.g. ``CancelledError``) is re-raised
    rather than reported as a dependency failure.
    """

    async def check_postgres() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_redis() -> None:
        await redis.ping()

    results = await asyncio.gather(check_postgres(), check_redis(), return_exceptions=True)
    dependencies: dict[str, str] = {}
    failed: list[str] = []
    for name, result in zip(("postgres", "redis"), results, strict=True):
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result  # e.g. CancelledError is not a dependency failure
        dependencies[name] = "unavailable" if isinstance(result, Exception) else "ok"
        if isinstance(result, Exception):
            failed.append(name)
    return dependencies, failed
