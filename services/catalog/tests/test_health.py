import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from redis.exceptions import RedisError

from catalog import main
from catalog.auth import Principal, get_principal
from catalog.config import Settings
from catalog.main import app
from catalog.services import health as health_service


def test_recommendation_cache_defaults() -> None:
    settings = Settings()

    assert settings.redis_url == "redis://localhost:6379"
    assert settings.recommendation_cache_ttl_seconds == 1800
    assert settings.redis_timeout_seconds == 1.0


def test_recommendation_cache_ttl_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(recommendation_cache_ttl_seconds=59)


def test_readiness_reports_optional_cache_degradation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def postgres_ok(_: object) -> bool:
        return True

    async def redis_down(_: object, **_kwargs: float) -> bool:
        return False

    monkeypatch.setattr(health_service, "check_postgres", postgres_ok)
    monkeypatch.setattr(health_service, "check_redis", redis_down)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {"postgres": "ok", "redis_cache": "degraded"}


def test_readiness_reports_healthy_optional_cache(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def dependency_ok(_: object, **_kwargs: float) -> bool:
        return True

    monkeypatch.setattr(health_service, "check_postgres", dependency_ok)
    monkeypatch.setattr(health_service, "check_redis", dependency_ok)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {"postgres": "ok", "redis_cache": "ok"}


def test_readiness_rejects_postgres_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def postgres_down(_: object) -> bool:
        return False

    async def redis_ok(_: object, **_kwargs: float) -> bool:
        return True

    monkeypatch.setattr(health_service, "check_postgres", postgres_down)
    monkeypatch.setattr(health_service, "check_redis", redis_ok)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "dependency unavailable: postgres"


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _ClosingRedis:
    async def aclose(self) -> None:
        raise RedisError("Redis stopped before application shutdown")


@pytest.mark.asyncio
async def test_redis_close_failure_does_not_prevent_engine_disposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine()
    settings = SimpleNamespace(
        redis_url="redis://test",
        recommendation_cache_ttl_seconds=1800,
        redis_timeout_seconds=1.0,
    )

    @asynccontextmanager
    async def no_op_mcp_lifespan(_: object):
        yield

    def closing_redis(redis_url: str, *, timeout_seconds: float) -> _ClosingRedis:
        assert redis_url == "redis://test"
        assert timeout_seconds == 1.0
        return _ClosingRedis()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "create_engine", lambda: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda _: object())
    monkeypatch.setattr(main, "create_redis_client", closing_redis)
    monkeypatch.setattr(main.mcp_app.router, "lifespan_context", no_op_mcp_lifespan)

    application = FastAPI()
    with pytest.raises(RedisError, match="Redis stopped"):
        async with main.lifespan(application):
            assert application.state.redis is not None
            assert application.state.recommendation_cache is not None

    assert engine.disposed


class _HangingRedis:
    async def ping(self) -> None:
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_readiness_bounds_hanging_redis_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def postgres_ok(_: object) -> bool:
        return True

    monkeypatch.setattr(health_service, "check_postgres", postgres_ok)
    monkeypatch.setattr(app.state, "engine", object(), raising=False)
    monkeypatch.setattr(app.state, "redis", _HangingRedis(), raising=False)
    monkeypatch.setattr(app.state, "redis_timeout_seconds", 0.01, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        async with asyncio.timeout(0.2):
            response = await async_client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {"postgres": "ok", "redis_cache": "degraded"}


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "catalog"}


def test_errors_are_problem_details(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]


def test_recipe_routes_require_a_bearer_token(client: TestClient) -> None:
    response = client.get("/v1/recipes")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert "resource_metadata=" in response.headers["www-authenticate"]
    assert response.json()["detail"] == "Authentication required."


def test_recipe_routes_report_insufficient_scope(client: TestClient) -> None:
    async def principal_without_write_scope() -> Principal:
        return Principal(
            subject="auth0|reader",
            scopes=frozenset({"recipes:read"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = principal_without_write_scope
    try:
        response = client.post(
            "/v1/recipes",
            json={"title": "No permission", "ingredients": [], "instructions": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
    assert 'scope="recipes:write"' in response.headers["www-authenticate"]


def test_rating_routes_require_rating_scope(client: TestClient) -> None:
    async def principal_without_rating_scope() -> Principal:
        return Principal(
            subject="auth0|recipe-writer",
            scopes=frozenset({"recipes:read", "recipes:write"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = principal_without_rating_scope
    try:
        response = client.put(
            "/v1/recipes/95da0a55-128e-43c2-bd21-4ef1ec8198fa/rating",
            json={"value": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert 'scope="ratings:write"' in response.headers["www-authenticate"]
