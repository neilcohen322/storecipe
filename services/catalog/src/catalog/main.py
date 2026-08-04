from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from catalog.auth import Auth0TokenVerifier
from catalog.config import get_settings
from catalog.database import create_engine, create_session_factory
from catalog.problems import PROBLEM_TYPE_BASE, install_problem_details, problem_response
from catalog.recipe_query_cache import RecipeQueryCache, create_redis_client
from catalog.routes.health import router as health_router
from catalog.routes.internal_recipes import router as internal_recipes_router
from catalog.routes.ratings import router as ratings_router
from catalog.routes.recipes import router as recipes_router
from catalog.services.errors import (
    CatalogError,
    IdempotencyConflict,
    InvalidCursor,
    InvalidFilter,
    RecipeNotFound,
    StaleRecipeQueryCursor,
)

settings = get_settings()
token_verifier = Auth0TokenVerifier(settings)


def _status_for(exc: CatalogError) -> int:
    if isinstance(exc, RecipeNotFound):
        return status.HTTP_404_NOT_FOUND
    if isinstance(exc, StaleRecipeQueryCursor):
        return status.HTTP_409_CONFLICT
    if isinstance(exc, IdempotencyConflict):
        return status.HTTP_409_CONFLICT
    if isinstance(exc, InvalidCursor | InvalidFilter):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_400_BAD_REQUEST


async def catalog_error(request: Request, exc: Exception) -> JSONResponse:
    """Translate framework-independent domain errors into problem responses."""
    if not isinstance(exc, CatalogError):  # pragma: no cover - handler is typed
        raise exc
    detail = "Recipe not found." if isinstance(exc, RecipeNotFound) else str(exc)
    problem_type = (
        f"{PROBLEM_TYPE_BASE}/stale_recipe_query_cursor"
        if isinstance(exc, StaleRecipeQueryCursor)
        else f"{PROBLEM_TYPE_BASE}/idempotency_conflict"
        if isinstance(exc, IdempotencyConflict)
        else None
    )
    extra: dict[str, object] | None = None
    if isinstance(exc, StaleRecipeQueryCursor):
        extra = {"errorCategory": "stale_recipe_query_cursor"}
    elif isinstance(exc, IdempotencyConflict):
        extra = {"errorCategory": "idempotency_conflict"}
    return problem_response(
        request,
        _status_for(exc),
        detail=detail,
        problem_type=problem_type,
        extra=extra,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Built at startup rather than import so configuration is read when the
    # process starts, and disposed even if shutdown is interrupted.
    app.state.engine = create_engine()
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.token_verifier = token_verifier
    runtime_settings = get_settings()
    app.state.redis_timeout_seconds = runtime_settings.redis_timeout_seconds
    app.state.redis = create_redis_client(
        runtime_settings.redis_url,
        timeout_seconds=runtime_settings.redis_timeout_seconds,
    )
    app.state.recipe_query_cache = RecipeQueryCache(
        app.state.redis,
        ttl_seconds=runtime_settings.recipe_query_cache_ttl_seconds,
        redis_timeout_seconds=runtime_settings.redis_timeout_seconds,
    )
    try:
        yield
    finally:
        try:
            await app.state.redis.aclose()
        finally:
            await app.state.engine.dispose()


app = FastAPI(
    title="Storecipe Catalog API",
    version="0.3.0",
    description="Recipe catalog and deterministic recipe query contract.",
    lifespan=lifespan,
)


@app.middleware("http")
async def reject_oversized_query(request: Request, call_next: RequestResponseEndpoint) -> Response:
    if len(request.scope["query_string"]) > 6144:
        return problem_response(
            request,
            status.HTTP_414_URI_TOO_LONG,
            detail="Query string exceeds the 6144-byte limit.",
        )
    return await call_next(request)


install_problem_details(app)
app.add_exception_handler(CatalogError, catalog_error)
app.include_router(recipes_router)
app.include_router(ratings_router)
app.include_router(internal_recipes_router)
app.include_router(health_router)
