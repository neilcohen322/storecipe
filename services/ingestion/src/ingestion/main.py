from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from ingestion.auth import build_token_verifier
from ingestion.catalog_client import build_catalog_client
from ingestion.config import get_settings
from ingestion.crypto import PayloadCipher
from ingestion.database import create_engine
from ingestion.problems import install_problem_details
from ingestion.rate_limits import RedisBurstLimiter
from ingestion.repositories.imports import ImportRepository
from ingestion.routes.health import router as health_router
from ingestion.routes.imports import router as imports_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Built at startup rather than import so configuration is read when the
    # process starts. The nested finally guarantees the engine is disposed even
    # when closing Redis fails (e.g. Redis already stopped during compose down).
    settings = get_settings()
    # Construct before allocating dependencies so an absent active key fails with
    # no half-started process. Retained key validation requires PostgreSQL.
    cipher = PayloadCipher.from_settings(settings)
    engine = create_engine()
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            await ImportRepository(session).assert_payload_keys_available(cipher)
    except BaseException:
        await engine.dispose()
        raise

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.payload_cipher = cipher
    app.state.import_deadline_seconds = getattr(settings, "import_deadline_seconds", 900)
    app.state.token_verifier = build_token_verifier(settings)
    app.state.source_lookup = build_catalog_client(settings)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.import_burst_limiter = RedisBurstLimiter.from_redis_url(
        settings.redis_url,
        amount=getattr(settings, "import_burst_requests", 5),
        window_seconds=getattr(settings, "import_burst_window_seconds", 60),
    )
    try:
        yield
    finally:
        try:
            await app.state.import_burst_limiter.close()
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
app.include_router(imports_router)
