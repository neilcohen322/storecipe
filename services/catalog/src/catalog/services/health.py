"""Health/readiness checks for catalog dependencies."""

import asyncio

from redis.asyncio import Redis
from redis.exceptions import RedisError
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


async def check_redis(redis: Redis, *, timeout_seconds: float = 1.0) -> bool:
    """Return whether the optional recipe-query cache is available."""
    try:
        async with asyncio.timeout(timeout_seconds):
            await redis.ping()
    except (RedisError, TimeoutError, ConnectionError, OSError):
        return False
    return True
