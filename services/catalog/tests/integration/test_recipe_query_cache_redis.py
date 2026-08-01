"""Opt-in checks for recipe-query page caching against a real Redis instance."""

import os
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from catalog.recipe_queries import (
    RecipeQueryItem,
    RecipeQueryPage,
    RecipeQueryRequest,
    recipe_query_hash,
)
from catalog.recipe_query_cache import CacheReadOutcome, RecipeQueryCache, create_redis_client
from catalog.schemas import RecipeView

pytestmark = pytest.mark.redis
REDIS_TIMEOUT_SECONDS = 1.0


@pytest.mark.asyncio
async def test_real_redis_clients_have_bounded_socket_timeouts() -> None:
    client = redis_client("redis://localhost:6379")
    try:
        connection_kwargs = client.connection_pool.connection_kwargs
        assert connection_kwargs["socket_connect_timeout"] == REDIS_TIMEOUT_SECONDS
        assert connection_kwargs["socket_timeout"] == REDIS_TIMEOUT_SECONDS
    finally:
        await client.aclose()


def redis_url() -> str:
    value = os.getenv("STORECIPE_TEST_REDIS_URL")
    if not value:
        pytest.skip("STORECIPE_TEST_REDIS_URL is not configured")
    return value


def redis_client(url: str | None = None) -> Redis:
    return create_redis_client(
        url or redis_url(),
        timeout_seconds=REDIS_TIMEOUT_SECONDS,
    )


def unavailable_redis_url(url: str) -> str:
    parsed = urlsplit(url)
    unavailable_port = 1 if parsed.port != 1 else 2
    return urlunsplit((parsed.scheme, f"{parsed.hostname}:{unavailable_port}", parsed.path, "", ""))


async def delete_test_namespace(client: Redis, user_id: UUID) -> None:
    keys = [key async for key in client.scan_iter(match=f"recipe_queries:{user_id}:*")]
    if keys:
        await client.delete(*keys)


def request_and_page() -> tuple[RecipeQueryRequest, RecipeQueryPage]:
    request = RecipeQueryRequest(text="Wine", required_ingredients=["Basil"])
    page = RecipeQueryPage(
        items=[
            RecipeQueryItem(
                recipe=RecipeView(
                    id=UUID("00000000-0000-0000-0000-000000000001"),
                    title="Wine soup",
                    source_url=None,
                    servings=2,
                    prep_minutes=5,
                    cook_minutes=10,
                    total_minutes=15,
                    ingredients=[],
                    instructions=[],
                    tags=[],
                )
            )
        ]
    )
    return request, page


@pytest.mark.asyncio
async def test_real_redis_cache_ttl_and_catalog_version() -> None:
    client = redis_client()
    user_id = uuid4()
    request, page = request_and_page()
    cache = RecipeQueryCache(client, ttl_seconds=1800)
    try:
        assert await cache.set(user_id, 3, request, page)
        key = cache.key(user_id, 3, recipe_query_hash(request))
        assert 1790 <= await client.ttl(key) <= 1800
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.HIT
        assert (await cache.get(user_id, 4, request)).outcome is CacheReadOutcome.MISS
        await client.expire(key, 0)
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.MISS
    finally:
        await delete_test_namespace(client, user_id)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_separates_ordered_sort_query_keys() -> None:
    client = redis_client()
    user_id = uuid4()
    _, page = request_and_page()
    first_request = RecipeQueryRequest(sort=["rating:desc", "totalMinutes:asc"])
    second_request = RecipeQueryRequest(sort=["totalMinutes:asc", "rating:desc"])
    cache = RecipeQueryCache(client)
    try:
        assert recipe_query_hash(first_request) != recipe_query_hash(second_request)
        assert await cache.set(user_id, 3, first_request, page)
        assert await cache.set(user_id, 3, second_request, page)
        assert cache.key(user_id, 3, recipe_query_hash(first_request)) != cache.key(
            user_id, 3, recipe_query_hash(second_request)
        )
        assert (await cache.get(user_id, 3, first_request)).outcome is CacheReadOutcome.HIT
        assert (await cache.get(user_id, 3, second_request)).outcome is CacheReadOutcome.HIT
    finally:
        await delete_test_namespace(client, user_id)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_invalidates_malformed_cache_envelope() -> None:
    client = redis_client()
    user_id = uuid4()
    request, _ = request_and_page()
    cache = RecipeQueryCache(client)
    key = cache.key(user_id, 3, recipe_query_hash(request))
    try:
        await client.set(key, "not-json", ex=1800)
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.INVALID
        assert await client.get(key) is None
    finally:
        await delete_test_namespace(client, user_id)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_invalidates_invalid_utf8_from_production_reader() -> None:
    writer = redis_client()
    reader = redis_client()
    user_id = uuid4()
    request, _ = request_and_page()
    cache = RecipeQueryCache(reader)
    key = cache.key(user_id, 3, recipe_query_hash(request))
    try:
        await writer.set(key, b"\xff", ex=1800)
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.INVALID
        assert await writer.get(key) is None
    finally:
        await delete_test_namespace(writer, user_id)
        await reader.aclose()
        await writer.aclose()


@pytest.mark.asyncio
async def test_real_redis_connection_loss_fails_open() -> None:
    url = redis_url()
    request, page = request_and_page()
    client = redis_client(unavailable_redis_url(url))
    cache = RecipeQueryCache(client)
    user_id = uuid4()
    try:
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.ERROR
        assert not await cache.set(user_id, 3, request, page)
    finally:
        await client.aclose()
