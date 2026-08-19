import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from catalog.auth import build_token_verifier
from catalog.config import get_settings
from catalog.cover_upload_limit import CoverUploadBodyLimitMiddleware
from catalog.database import create_engine, create_session_factory
from catalog.problems import PROBLEM_TYPE_BASE, install_problem_details, problem_response
from catalog.rate_limits import RedisBurstLimiter
from catalog.recipe_query_cache import RecipeQueryCache, create_redis_client
from catalog.routes.health import router as health_router
from catalog.routes.internal_recipes import router as internal_recipes_router
from catalog.routes.ratings import router as ratings_router
from catalog.routes.recipe_facets import router as recipe_facets_router
from catalog.routes.recipe_images import router as recipe_images_router
from catalog.routes.recipes import router as recipes_router
from catalog.services.errors import (
    CatalogError,
    CoverImageNotFound,
    IdempotencyConflict,
    ImageTooLarge,
    InvalidCursor,
    InvalidFilter,
    InvalidImage,
    MediaUnavailable,
    MutationRateLimited,
    MutationRateLimitUnavailable,
    RecipeNotFound,
    StaleRecipeFacetCursor,
    StaleRecipeQueryCursor,
    UnstableCatalogSnapshot,
)
from storecipe_auth.body_limit import CATALOG_MAX_REQUEST_BYTES, RequestBodyLimitMiddleware

settings = get_settings()
token_verifier = build_token_verifier(settings)


def _status_for(exc: CatalogError) -> int:
    if isinstance(exc, RecipeNotFound | CoverImageNotFound):
        return status.HTTP_404_NOT_FOUND
    if isinstance(exc, MediaUnavailable | UnstableCatalogSnapshot | MutationRateLimitUnavailable):
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if isinstance(exc, MutationRateLimited):
        return status.HTTP_429_TOO_MANY_REQUESTS
    if isinstance(exc, StaleRecipeQueryCursor | StaleRecipeFacetCursor):
        return status.HTTP_409_CONFLICT
    if isinstance(exc, IdempotencyConflict):
        return status.HTTP_409_CONFLICT
    if isinstance(exc, ImageTooLarge):
        return status.HTTP_413_CONTENT_TOO_LARGE
    if isinstance(exc, InvalidCursor | InvalidFilter | InvalidImage):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_400_BAD_REQUEST


async def catalog_error(request: Request, exc: Exception) -> JSONResponse:
    """Translate framework-independent domain errors into problem responses."""
    if not isinstance(exc, CatalogError):  # pragma: no cover - handler is typed
        raise exc
    detail = "Recipe not found." if isinstance(exc, RecipeNotFound) else str(exc)
    problem_type = (
        f"{PROBLEM_TYPE_BASE}/stale_recipe_facet_cursor"
        if isinstance(exc, StaleRecipeFacetCursor)
        else f"{PROBLEM_TYPE_BASE}/stale_recipe_query_cursor"
        if isinstance(exc, StaleRecipeQueryCursor)
        else f"{PROBLEM_TYPE_BASE}/idempotency_conflict"
        if isinstance(exc, IdempotencyConflict)
        else f"{PROBLEM_TYPE_BASE}/catalog-rate-limited"
        if isinstance(exc, MutationRateLimited)
        else f"{PROBLEM_TYPE_BASE}/rate-limit-unavailable"
        if isinstance(exc, MutationRateLimitUnavailable)
        else f"{PROBLEM_TYPE_BASE}/image_too_large"
        if isinstance(exc, ImageTooLarge)
        else f"{PROBLEM_TYPE_BASE}/invalid_image"
        if isinstance(exc, InvalidImage)
        else f"{PROBLEM_TYPE_BASE}/cover_image_not_found"
        if isinstance(exc, CoverImageNotFound)
        else f"{PROBLEM_TYPE_BASE}/media_unavailable"
        if isinstance(exc, MediaUnavailable)
        else None
    )
    extra: dict[str, object] | None = None
    headers = None
    if isinstance(exc, StaleRecipeFacetCursor):
        extra = {"errorCategory": "stale_recipe_facet_cursor"}
    elif isinstance(exc, StaleRecipeQueryCursor):
        extra = {"errorCategory": "stale_recipe_query_cursor"}
    elif isinstance(exc, IdempotencyConflict):
        extra = {"errorCategory": "idempotency_conflict"}
    elif isinstance(exc, MutationRateLimited):
        extra = {"errorCategory": "catalog_rate_limited"}
        headers = exc.headers
    elif isinstance(exc, MutationRateLimitUnavailable):
        extra = {"errorCategory": "rate_limit_unavailable"}
        headers = exc.headers
    elif isinstance(exc, ImageTooLarge):
        extra = {"errorCategory": "image_too_large"}
    elif isinstance(exc, InvalidImage):
        extra = {"errorCategory": "invalid_image"}
    elif isinstance(exc, CoverImageNotFound):
        extra = {"errorCategory": "cover_image_not_found"}
    elif isinstance(exc, MediaUnavailable):
        extra = {"errorCategory": "media_unavailable"}
    return problem_response(
        request,
        _status_for(exc),
        detail=detail,
        problem_type=problem_type,
        extra=extra,
        headers=headers,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Built at startup rather than import so configuration is read when the
    # process starts, and disposed even if shutdown is interrupted.
    app.state.engine = create_engine()
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.token_verifier = token_verifier
    app.state.auth_resource_metadata_url = settings.resource_metadata_url
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
    app.state.mutation_burst_limiter = RedisBurstLimiter.from_redis_url(
        runtime_settings.redis_url,
        amount=runtime_settings.mutation_burst_requests,
        window_seconds=runtime_settings.mutation_burst_window_seconds,
        timeout_seconds=runtime_settings.redis_timeout_seconds,
    )
    if runtime_settings.media_bucket:
        from catalog.media.gcs_store import GcsRecipeImageStore

        app.state.recipe_image_store = GcsRecipeImageStore(runtime_settings.media_bucket)
    else:
        app.state.recipe_image_store = None
    app.state.image_processing_semaphore = asyncio.Semaphore(1)
    try:
        yield
    finally:
        try:
            await app.state.mutation_burst_limiter.close()
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


# Inside CORS and request-id so 413s keep X-Request-ID; before routing/auth/multipart.
app.add_middleware(CoverUploadBodyLimitMiddleware)
install_problem_details(app)
app.add_exception_handler(CatalogError, catalog_error)
app.include_router(recipes_router)
app.include_router(recipe_images_router)
app.include_router(recipe_facets_router)
app.include_router(ratings_router)
app.include_router(internal_recipes_router)
app.include_router(health_router)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=CATALOG_MAX_REQUEST_BYTES,
    problem_type_base=PROBLEM_TYPE_BASE,
    skip_path_suffixes=("/cover-image",),
)
# Outermost so browser preflight OPTIONS succeeds before auth/route handling.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)
