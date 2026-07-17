import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ingestion.config import get_settings
from ingestion.database import create_engine
from ingestion.problems import install_problem_details


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Built at startup rather than import so configuration is read when the
    # process starts. The nested finally guarantees the engine is disposed even
    # when closing Redis fails (e.g. Redis already stopped during compose down).
    app.state.engine = create_engine()
    app.state.redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield
    finally:
        try:
            await app.state.redis.aclose()
        finally:
            await app.state.engine.dispose()


app = FastAPI(
    title="Storecipe Ingestion API",
    version="0.1.0",
    description="Asynchronous URL and text import boundary.",
    lifespan=lifespan,
)
install_problem_details(app)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().service_name}


@app.get("/health/ready", tags=["health"])
async def readiness(request: Request) -> dict[str, Any]:
    engine: AsyncEngine = request.app.state.engine
    redis_client = request.app.state.redis

    async def check_postgres() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_redis() -> None:
        await redis_client.ping()

    results = await asyncio.gather(check_postgres(), check_redis(), return_exceptions=True)
    dependencies: dict[str, str] = {}
    failed: list[str] = []
    for name, result in zip(("postgres", "redis"), results, strict=True):
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result  # e.g. CancelledError is not a dependency failure
        dependencies[name] = "unavailable" if isinstance(result, Exception) else "ok"
        if isinstance(result, Exception):
            failed.append(name)
    if failed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dependency unavailable: " + ", ".join(failed),
        )
    return {
        "status": "ok",
        "service": get_settings().service_name,
        "dependencies": dependencies,
    }
