import asyncio
import logging
from typing import Any

import pytest

from catalog.rate_limits import RateLimitDecision, RedisBurstLimiter


class RecordingStorage:
    def __init__(self, uri: str, **options: Any) -> None:
        self.uri = uri
        self.options = options
        self.connection = self
        self.bridge = self
        self.closed = False

    def get_connection(self) -> "RecordingStorage":
        return self

    async def aclose(self) -> None:
        self.closed = True


class RecordingLimiter:
    def __init__(self, storage: RecordingStorage) -> None:
        self.storage = storage


class HangingStrategy:
    async def hit(self, item: Any, *identifiers: str) -> bool:
        del item, identifiers
        await asyncio.Event().wait()
        return True

    async def get_window_stats(self, item: Any, *identifiers: str) -> tuple[int, int]:
        del item, identifiers
        return 0, 0


@pytest.mark.asyncio
async def test_limiter_factory_passes_redis_socket_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("catalog.rate_limits.RedisStorage", RecordingStorage)
    monkeypatch.setattr("catalog.rate_limits.MovingWindowRateLimiter", RecordingLimiter)

    limiter = RedisBurstLimiter.from_redis_url(
        "redis://redis.example:6379/3",
        amount=30,
        window_seconds=60,
        timeout_seconds=1.0,
    )

    storage = limiter._storage
    assert isinstance(storage, RecordingStorage)
    assert storage.options["socket_connect_timeout"] == 1.0
    assert storage.options["socket_timeout"] == 1.0
    await limiter.close()
    assert storage.closed is True


@pytest.mark.asyncio
async def test_limiter_fails_closed_when_redis_hangs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    limiter = RedisBurstLimiter(
        HangingStrategy(),
        amount=30,
        window_seconds=60,
        timeout_seconds=0.01,
        clock=lambda: 1_800_000_000,
    )
    caplog.set_level(logging.INFO, logger="catalog.rate_limits")

    async with asyncio.timeout(0.2):
        decision = await limiter.hit("auth0|private-user", "catalog_mutation")

    assert decision == RateLimitDecision(False, 30, 0, 1_800_000_060, degraded=True)
    assert "rate_limit.unavailable" in caplog.text
    assert "auth0|private-user" not in caplog.text
