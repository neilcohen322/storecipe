"""Opt-in checks for recommendation caching against a real Redis instance."""

import os
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from catalog.recommendation_cache import CacheReadOutcome, RecommendationCache
from catalog.recommendations import (
    RecommendationItem,
    RecommendationRequest,
    RecommendationResponse,
    RecommendationScoreComponents,
    recommendation_request_hash,
)

pytestmark = pytest.mark.redis


def redis_url() -> str:
    value = os.getenv("STORECIPE_TEST_REDIS_URL")
    if not value:
        pytest.skip("STORECIPE_TEST_REDIS_URL is not configured")
    return value


def unavailable_redis_url(url: str) -> str:
    parsed = urlsplit(url)
    unavailable_port = 1 if parsed.port != 1 else 2
    return urlunsplit((parsed.scheme, f"{parsed.hostname}:{unavailable_port}", parsed.path, "", ""))


async def delete_test_namespace(client: Redis, user_id: UUID) -> None:
    keys = [key async for key in client.scan_iter(match=f"recommendations:{user_id}:*")]
    if keys:
        await client.delete(*keys)


def request_and_response() -> tuple[RecommendationRequest, RecommendationResponse]:
    request = RecommendationRequest(query="Wine", must_include_ingredients=["Basil"])
    response = RecommendationResponse(
        request=request,
        catalog_version=3,
        items=[
            RecommendationItem(
                recipe_id=UUID("00000000-0000-0000-0000-000000000001"),
                score=1.0,
                components=RecommendationScoreComponents(
                    ingredient_coverage=1.0,
                    positive_preference=0.5,
                    time_compatibility=1.0,
                    query_tag_match=0.0,
                    negative_preference_penalty=0.0,
                    previously_rated_penalty=0.0,
                ),
            )
        ],
    )
    return request, response


@pytest.mark.asyncio
async def test_real_redis_cache_ttl_and_catalog_version() -> None:
    client = Redis.from_url(redis_url(), decode_responses=True)
    user_id = uuid4()
    request, response = request_and_response()
    cache = RecommendationCache(client, ttl_seconds=1800)
    try:
        assert await cache.set(user_id, 3, request, response)
        key = cache.key(user_id, 3, recommendation_request_hash(request))
        assert 1790 <= await client.ttl(key) <= 1800
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.HIT
        assert (await cache.get(user_id, 4, request)).outcome is CacheReadOutcome.MISS
        await client.expire(key, 0)
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.MISS
    finally:
        await delete_test_namespace(client, user_id)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_invalidates_malformed_cache_envelope() -> None:
    client = Redis.from_url(redis_url(), decode_responses=True)
    user_id = uuid4()
    request, _ = request_and_response()
    cache = RecommendationCache(client)
    key = cache.key(user_id, 3, recommendation_request_hash(request))
    try:
        await client.set(key, "not-json", ex=1800)
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.INVALID
        assert await client.get(key) is None
    finally:
        await delete_test_namespace(client, user_id)
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_invalidates_invalid_utf8_from_production_reader() -> None:
    from catalog.recommendation_cache import create_redis_client

    writer = Redis.from_url(redis_url(), decode_responses=False)
    reader = create_redis_client(redis_url(), timeout_seconds=1.0)
    user_id = uuid4()
    request, _ = request_and_response()
    cache = RecommendationCache(reader)
    key = cache.key(user_id, 3, recommendation_request_hash(request))
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
    request, response = request_and_response()
    client = Redis.from_url(unavailable_redis_url(url), decode_responses=True)
    cache = RecommendationCache(client)
    user_id = uuid4()
    try:
        assert (await cache.get(user_id, 3, request)).outcome is CacheReadOutcome.ERROR
        assert not await cache.set(user_id, 3, request, response)
    finally:
        await client.aclose()
