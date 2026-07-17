from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException

from catalog.auth import Auth0TokenVerifier
from catalog.config import get_settings
from catalog.database import create_engine, create_session_factory
from catalog.mcp_server import create_mcp_server
from catalog.problems import install_problem_details, problem_response
from catalog.ratings import router as ratings_router
from catalog.recipes import internal_router
from catalog.recipes import router as recipes_router

settings = get_settings()
token_verifier = Auth0TokenVerifier(settings)
mcp_server = create_mcp_server(settings, token_verifier)
mcp_app = mcp_server.streamable_http_app()


async def mcp_http_problem(request: Request, exc: Exception) -> Response:
    """Keep the mounted MCP fallback consistent with the REST error contract."""
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - handler is typed
        raise exc
    detail = exc.detail if isinstance(exc.detail, str) else None
    return problem_response(request, exc.status_code, detail=detail, headers=exc.headers)


mcp_app.add_exception_handler(StarletteHTTPException, mcp_http_problem)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Built at startup rather than import so configuration is read when the
    # process starts, and disposed even if shutdown is interrupted.
    app.state.engine = create_engine()
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.token_verifier = token_verifier
    try:
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
    finally:
        await app.state.engine.dispose()


app = FastAPI(
    title="Storecipe Catalog API",
    version="0.3.0",
    description="Recipe catalog, recommendations, and MCP boundary.",
    lifespan=lifespan,
)
install_problem_details(app)
app.include_router(recipes_router)
app.include_router(ratings_router)
app.include_router(internal_router)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().service_name}


@app.get("/health/ready", tags=["health"])
async def readiness(request: Request) -> dict[str, Any]:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dependency unavailable: postgres",
        ) from exc
    return {
        "status": "ok",
        "service": get_settings().service_name,
        "dependencies": {"postgres": "ok"},
    }


# Mount last so REST and health routes retain priority while the SDK serves
# /mcp and /.well-known/oauth-protected-resource/mcp on the same deployment.
app.mount("/", mcp_app)
