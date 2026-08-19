from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from catalog.auth import Principal, get_principal
from catalog.database import get_session
from catalog.main import app
from catalog.models import Base
from catalog.rate_limits import RateLimitDecision


class StubLimiter:
    def __init__(self, decision: RateLimitDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[str, str]] = []

    async def hit(self, subject: str, operation: str) -> RateLimitDecision:
        self.calls.append((subject, operation))
        return self.decision


def _payload() -> dict[str, object]:
    return {
        "title": "Soup",
        "ingredients": [{"rawText": "1 onion", "name": "onion"}],
        "instructions": ["Cook."],
    }


@pytest_asyncio.fixture
async def api_client(recipe_query_cache_state: object) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session() -> AsyncIterator[object]:
        async with session_factory() as session:
            yield session

    async def test_principal() -> Principal:
        return Principal(
            subject="auth0|default-user",
            scopes=frozenset({"recipes:read", "recipes:write", "ratings:write"}),
            claims={},
        )

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_principal] = test_principal
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_catalog_mutation_exhaustion_returns_429_and_skips_persistence(
    api_client: AsyncClient,
) -> None:
    app.state.mutation_burst_limiter = StubLimiter(RateLimitDecision(False, 30, 0, 1_800_000_030))

    response = await api_client.post(
        "/v1/recipes", headers={"Idempotency-Key": "catalog-rate-key"}, json=_payload()
    )

    assert response.status_code == 429
    assert response.json()["errorCategory"] == "catalog_rate_limited"
    assert response.headers["Retry-After"]
    listed = await api_client.get("/v1/recipes")
    assert listed.status_code == 200
    assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_catalog_mutation_redis_failure_returns_503_without_persistence(
    api_client: AsyncClient,
) -> None:
    app.state.mutation_burst_limiter = StubLimiter(
        RateLimitDecision(False, 30, 0, 1_800_000_030, degraded=True)
    )

    response = await api_client.post(
        "/v1/recipes", headers={"Idempotency-Key": "catalog-unavail-key"}, json=_payload()
    )

    assert response.status_code == 503
    assert response.json()["errorCategory"] == "rate_limit_unavailable"
    listed = await api_client.get("/v1/recipes")
    assert listed.status_code == 200
    assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_catalog_reads_remain_available_when_mutations_are_limited(
    api_client: AsyncClient,
) -> None:
    limiter = StubLimiter(RateLimitDecision(False, 30, 0, 1_800_000_030))
    app.state.mutation_burst_limiter = limiter

    listed = await api_client.get("/v1/recipes")
    facets = await api_client.get("/v1/recipe-facets")

    assert listed.status_code == 200
    assert facets.status_code == 200
    assert limiter.calls == []


@pytest.mark.asyncio
async def test_catalog_mutation_limiter_uses_authenticated_subject(
    api_client: AsyncClient,
) -> None:
    limiter = StubLimiter(RateLimitDecision(True, 30, 29, 1_800_000_030))
    app.state.mutation_burst_limiter = limiter

    async def other_principal() -> Principal:
        return Principal(
            subject="auth0|other-user",
            scopes=frozenset({"recipes:read", "recipes:write", "ratings:write"}),
            claims={},
        )

    await api_client.post(
        "/v1/recipes", headers={"Idempotency-Key": "catalog-subject-a"}, json=_payload()
    )
    app.dependency_overrides[get_principal] = other_principal
    await api_client.post(
        "/v1/recipes", headers={"Idempotency-Key": "catalog-subject-b"}, json=_payload()
    )

    assert [call[0] for call in limiter.calls] == ["auth0|default-user", "auth0|other-user"]
    assert {call[1] for call in limiter.calls} == {"catalog_mutation"}


@pytest.mark.asyncio
async def test_internal_recipe_writes_are_not_subject_to_the_user_mutation_limiter(
    api_client: AsyncClient,
) -> None:
    limiter = StubLimiter(RateLimitDecision(False, 30, 0, 1_800_000_030))
    app.state.mutation_burst_limiter = limiter

    async def m2m_principal() -> Principal:
        return Principal(
            subject="auth0-m2m-client",
            scopes=frozenset({"recipes:internal:create"}),
            claims={},
        )

    app.dependency_overrides[get_principal] = m2m_principal
    response = await api_client.post(
        "/internal/recipes/imported",
        json={
            **_payload(),
            "ownerSubject": "auth0|import-owner",
            "importJobId": "5aac13b6-08f1-48fa-852f-fb1e2f7daf52",
            "sourceFingerprint": "a" * 64,
        },
    )

    assert response.status_code == 201
    assert limiter.calls == []


@pytest.mark.asyncio
async def test_successful_deletes_return_mutation_quota_headers(api_client: AsyncClient) -> None:
    limiter = StubLimiter(RateLimitDecision(True, 30, 29, 1_800_000_030))
    app.state.mutation_burst_limiter = limiter

    created = await api_client.post(
        "/v1/recipes", headers={"Idempotency-Key": "catalog-delete-headers"}, json=_payload()
    )
    recipe_id = created.json()["id"]
    recipe_delete = await api_client.delete(f"/v1/recipes/{recipe_id}")

    created = await api_client.post(
        "/v1/recipes",
        headers={"Idempotency-Key": "catalog-rating-delete-headers"},
        json=_payload(),
    )
    recipe_id = created.json()["id"]
    await api_client.put(f"/v1/recipes/{recipe_id}/rating", json={"value": 5})
    rating_delete = await api_client.delete(f"/v1/recipes/{recipe_id}/rating")

    assert recipe_delete.status_code == 204
    assert rating_delete.status_code == 204
    assert recipe_delete.headers["RateLimit-Limit"] == "30"
    assert recipe_delete.headers["RateLimit-Remaining"] == "29"
    assert recipe_delete.headers["RateLimit-Reset"] == "1800000030"
    assert rating_delete.headers["RateLimit-Limit"] == "30"
