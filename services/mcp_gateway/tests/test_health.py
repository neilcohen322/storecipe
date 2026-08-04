import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from storecipe_mcp import main as main_module
from storecipe_mcp.config import Settings
from storecipe_mcp.main import create_app


def test_liveness_reports_gateway_service() -> None:
    app = create_app(settings=Settings(service_name="test-mcp-gateway"))

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "test-mcp-gateway"}


def test_readiness_calls_injected_probe_and_reports_dependencies() -> None:
    calls: list[str] = []

    async def probe() -> dict[str, str]:
        calls.append("called")
        return {"catalog": "ok"}

    app = create_app(settings=Settings(service_name="test-mcp-gateway"), readiness_probe=probe)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "test-mcp-gateway",
        "dependencies": {"catalog": "ok"},
    }
    assert calls == ["called"]


def test_readiness_returns_service_unavailable_for_failed_probe() -> None:
    async def probe() -> dict[str, str]:
        return {"catalog": "unavailable"}

    app = create_app(settings=Settings(service_name="test-mcp-gateway"), readiness_probe=probe)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "dependency unavailable: catalog"}


def test_default_readiness_uses_one_pooled_catalog_client_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []
    client_kwargs: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "catalog",
                "dependencies": {"postgres": "ok", "redis_cache": "ok"},
            },
        )

    real_async_client = main_module.httpx.AsyncClient

    def build_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        client_kwargs.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        client = real_async_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(main_module.httpx, "AsyncClient", build_client)
    settings = Settings(
        catalog_api_url="http://catalog.test:8000",
        connect_timeout_seconds=1.5,
        pool_timeout_seconds=2.5,
        read_timeout_seconds=3.5,
        write_timeout_seconds=4.5,
    )
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "mcp-gateway",
        "dependencies": {"catalog": "ok"},
    }
    assert len(clients) == 1
    assert clients[0].is_closed
    assert client_kwargs["base_url"] == "http://catalog.test:8000"
    assert client_kwargs["follow_redirects"] is False
    timeout = client_kwargs["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.pool == 2.5
    assert timeout.read == 3.5
    assert timeout.write == 4.5
    limits = client_kwargs["limits"]
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections is not None
    assert limits.max_connections > 0
    assert limits.max_keepalive_connections is not None
    assert limits.max_keepalive_connections > 0
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url == "http://catalog.test:8000/health/ready"
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_readiness_bounds_a_hanging_probe_without_leaking_exception_detail() -> None:
    async def hanging_probe() -> dict[str, str]:
        await asyncio.Event().wait()
        return {"catalog": "ok"}

    settings = Settings(service_name="test-mcp-gateway", read_timeout_seconds=0.1)
    app = create_app(settings=settings, readiness_probe=hanging_probe)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with asyncio.timeout(0.5):
                response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "dependency unavailable"}


def test_oauth_protected_resource_metadata_is_gateway_owned() -> None:
    settings = Settings(
        service_name="test-mcp-gateway",
        auth0_issuer="https://tenant.example/",
        mcp_resource_url="https://mcp.example/mcp",
    )
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": "https://mcp.example/mcp",
        "authorization_servers": ["https://tenant.example/"],
        "scopes_supported": ["recipes:read", "recipes:write", "ratings:write"],
        "bearer_methods_supported": ["header"],
    }


def test_gateway_imports_do_not_reach_catalog_or_persistence_modules() -> None:
    import ast
    from pathlib import Path

    import storecipe_mcp

    package_dir = Path(storecipe_mcp.__file__).parent
    forbidden = {"catalog", "sqlalchemy", "asyncpg", "redis"}
    offenders: dict[str, set[str]] = {}
    for source_path in package_dir.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = {
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
        }
        imported.update(
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        matches = imported & forbidden
        if matches:
            offenders[str(source_path)] = matches

    assert not offenders
