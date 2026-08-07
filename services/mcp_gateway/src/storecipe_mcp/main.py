import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, status

from storecipe_mcp.auth import McpInboundTokenVerifier
from storecipe_mcp.catalog_client import CatalogClient
from storecipe_mcp.config import Settings, get_settings
from storecipe_mcp.errors import CatalogClientError
from storecipe_mcp.mcp_server import create_mcp_server
from storecipe_mcp.obo_client import OboTokenProvider, build_obo_token_provider

ReadinessResult = Mapping[str, str] | bool
ReadinessProbe = Callable[[], Awaitable[ReadinessResult]]


def _dependency_statuses(result: ReadinessResult) -> dict[str, str]:
    if isinstance(result, bool):
        return {"gateway": "ok" if result else "unavailable"}
    return dict(result)


def create_app(
    *,
    settings: Settings | None = None,
    readiness_probe: ReadinessProbe | None = None,
    catalog_transport: httpx.AsyncBaseTransport | None = None,
    obo_provider: OboTokenProvider | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    mcp_token_verifier = McpInboundTokenVerifier(runtime_settings)
    runtime_catalog_client: CatalogClient | None = None
    runtime_obo_provider: OboTokenProvider | None = None

    def require_catalog_client() -> CatalogClient:
        if runtime_catalog_client is None:
            raise CatalogClientError("temporary_catalog_failure", retryable=True)
        return runtime_catalog_client

    def require_obo_provider() -> OboTokenProvider:
        if runtime_obo_provider is None:
            raise CatalogClientError("authentication_required", retryable=False)
        return runtime_obo_provider

    mcp_server = create_mcp_server(
        runtime_settings,
        mcp_token_verifier,
        catalog_client_provider=require_catalog_client,
        obo_provider_factory=require_obo_provider,
    )
    mcp_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime_catalog_client, runtime_obo_provider
        application.state.settings = runtime_settings
        application.state.mcp_token_verifier = mcp_token_verifier
        application.state.mcp_server = mcp_server
        timeout = httpx.Timeout(
            connect=runtime_settings.connect_timeout_seconds,
            pool=runtime_settings.pool_timeout_seconds,
            read=runtime_settings.read_timeout_seconds,
            write=runtime_settings.write_timeout_seconds,
        )
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        obo_timeout = httpx.Timeout(
            connect=runtime_settings.connect_timeout_seconds,
            pool=runtime_settings.pool_timeout_seconds,
            read=runtime_settings.read_timeout_seconds,
            write=runtime_settings.write_timeout_seconds,
        )
        async with httpx.AsyncClient(
            base_url=runtime_settings.catalog_api_url,
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
            transport=catalog_transport,
        ) as catalog_http:
            async with httpx.AsyncClient(timeout=obo_timeout, follow_redirects=False) as obo_http:
                catalog_client = CatalogClient(
                    catalog_http,
                    max_response_bytes=runtime_settings.catalog_max_response_bytes,
                )
                runtime_catalog_client = catalog_client
                if obo_provider is not None:
                    runtime_obo_provider = obo_provider
                elif (
                    runtime_settings.obo_client_id
                    and runtime_settings.obo_client_secret.get_secret_value()
                    and runtime_settings.auth0_audience
                    and runtime_settings.resolved_obo_token_url
                ):
                    runtime_obo_provider = build_obo_token_provider(runtime_settings, obo_http)
                application.state.catalog_client = catalog_client
                application.state.readiness_probe = readiness_probe or catalog_client.readiness
                try:
                    async with mcp_app.router.lifespan_context(mcp_app):
                        yield
                finally:
                    runtime_catalog_client = None
                    runtime_obo_provider = None

    application = FastAPI(
        title="Storecipe MCP Gateway",
        version="0.1.0",
        description="Standalone OAuth-protected MCP gateway scaffold.",
        lifespan=lifespan,
    )

    @application.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok", "service": runtime_settings.service_name}

    @application.get("/health/ready")
    async def readiness(request: Request) -> dict[str, Any]:
        try:
            async with asyncio.timeout(runtime_settings.read_timeout_seconds):
                result = await request.app.state.readiness_probe()
        except TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="dependency unavailable",
            ) from None
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="dependency unavailable",
            ) from None
        dependencies = _dependency_statuses(result)
        failed = [
            name for name, dependency_status in dependencies.items() if dependency_status != "ok"
        ]
        if failed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="dependency unavailable: " + ", ".join(failed),
            )
        return {
            "status": "ok",
            "service": runtime_settings.service_name,
            "dependencies": dependencies,
        }

    @application.get("/.well-known/oauth-protected-resource/mcp")
    async def protected_resource_metadata() -> dict[str, object]:
        return {
            "resource": runtime_settings.mcp_resource_url,
            "authorization_servers": [runtime_settings.auth0_issuer or "https://auth.invalid/"],
            "scopes_supported": ["recipes:read", "recipes:write", "ratings:write"],
            "bearer_methods_supported": ["header"],
        }

    application.mount("/", mcp_app)

    return application


app = create_app()
