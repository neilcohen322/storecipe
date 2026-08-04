from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from catalog.main import app
from catalog.recipe_query_cache import RecipeQueryCache


class FakeRedis:
    """In-memory cache client for route tests that bypass the ASGI lifespan."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        return 1

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@pytest.fixture
def recipe_query_cache_state() -> Iterator[FakeRedis]:
    """Provide cache state to ASGITransport clients, which do not run lifespan."""
    state = app.state._state
    missing = object()
    previous_redis = state.get("redis", missing)
    previous_cache = state.get("recipe_query_cache", missing)
    redis = FakeRedis()
    app.state.redis = redis
    app.state.recipe_query_cache = RecipeQueryCache(redis)
    try:
        yield redis
    finally:
        if previous_redis is missing:
            del state["redis"]
        else:
            state["redis"] = previous_redis
        if previous_cache is missing:
            del state["recipe_query_cache"]
        else:
            state["recipe_query_cache"] = previous_cache


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """Keep the production-style, single-run Catalog lifespan for the test session."""
    with TestClient(app) as test_client:
        yield test_client
