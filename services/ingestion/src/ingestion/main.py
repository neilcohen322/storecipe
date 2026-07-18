from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from ingestion.config import get_settings
from ingestion.database import create_engine
from ingestion.problems import install_problem_details
from ingestion.routes.health import router as health_router


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
app.include_router(health_router)
