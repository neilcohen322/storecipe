"""Opt-in checks against a real Redis moving-window limiter."""

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from ingestion.rate_limits import RedisBurstLimiter

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


@pytest.mark.asyncio
async def test_real_redis_moving_window_is_concurrent_isolated_and_fails_closed() -> None:
    url = redis_url()
    namespace = f"storecipe-test-{uuid4()}"
    operation = f"{namespace}:import"
    client = Redis.from_url(url, decode_responses=True)
    limiter = RedisBurstLimiter.from_redis_url(url, amount=5, window_seconds=60)
    try:
        decisions = await asyncio.gather(
            *(limiter.hit("auth0|shared-user", operation) for _ in range(12))
        )
        assert sum(decision.allowed for decision in decisions) == 5
        assert all(not decision.degraded for decision in decisions)

        isolated = await limiter.hit("auth0|isolated-user", operation)
        assert isolated.allowed
        assert not isolated.degraded

        keys = [key async for key in client.scan_iter(match=f"*{namespace}*")]
        assert keys
        ttls = [await client.ttl(key) for key in keys]
        assert all(ttl > 0 for ttl in ttls)

        await limiter.close()
        degraded_limiter = RedisBurstLimiter.from_redis_url(
            unavailable_redis_url(url), amount=5, window_seconds=60
        )
        try:
            degraded = await degraded_limiter.hit("auth0|shared-user", operation)
            assert degraded.allowed is False
            assert degraded.degraded is True
            assert degraded.remaining == 0
        finally:
            await degraded_limiter.close()
    finally:
        keys = [key async for key in client.scan_iter(match=f"*{namespace}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
