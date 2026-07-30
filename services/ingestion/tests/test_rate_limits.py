import logging
from typing import Any

import pytest
from limits.errors import StorageError

from ingestion.rate_limits import RateLimitDecision, RedisBurstLimiter


class RecordingStrategy:
    def __init__(self, *, allowed: bool, reset_at: int, remaining: int) -> None:
        self.allowed = allowed
        self.reset_at = reset_at
        self.remaining = remaining
        self.identifiers: tuple[str, ...] = ()

    async def hit(self, item: Any, *identifiers: str) -> bool:
        del item
        self.identifiers = identifiers
        return self.allowed

    async def get_window_stats(self, item: Any, *identifiers: str) -> tuple[int, int]:
        del item, identifiers
        return self.reset_at, self.remaining


class UnavailableStrategy:
    async def hit(self, item: Any, *identifiers: str) -> bool:
        del item, identifiers
        raise StorageError(ConnectionError("redis is unavailable"))


class BuggyStrategy:
    async def hit(self, item: Any, *identifiers: str) -> bool:
        del item, identifiers
        raise RuntimeError("programming error")


class RecordingConnection:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class RecordingStorage:
    def __init__(self, uri: str, **options: Any) -> None:
        self.uri = uri
        self.options = options
        self.connection = RecordingConnection()
        self.bridge = self

    def get_connection(self) -> RecordingConnection:
        return self.connection


class RecordingLimiter:
    def __init__(self, storage: RecordingStorage) -> None:
        self.storage = storage


@pytest.mark.asyncio
async def test_limiter_hashes_subject_and_returns_window_stats() -> None:
    """Catches using a raw subject or returning incorrect Redis window statistics."""

    strategy = RecordingStrategy(allowed=False, reset_at=1_800_000_030, remaining=0)
    limiter = RedisBurstLimiter(strategy, amount=5, window_seconds=60, clock=lambda: 1_800_000_000)

    decision = await limiter.hit("auth0|private-user", "import")

    assert decision == RateLimitDecision(False, 5, 0, 1_800_000_030)
    assert strategy.identifiers[0] == "import"
    assert strategy.identifiers[1] != "auth0|private-user"
    assert len(strategy.identifiers[1]) == 64


@pytest.mark.asyncio
async def test_limiter_allows_requests_when_redis_is_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Catches failing closed or logging an identifier when Redis is unavailable."""

    limiter = RedisBurstLimiter(
        UnavailableStrategy(), amount=5, window_seconds=60, clock=lambda: 1_800_000_000
    )
    caplog.set_level(logging.INFO, logger="ingestion.rate_limits")

    decision = await limiter.hit("auth0|private-user", "import")

    assert decision.allowed is True
    assert decision.degraded is True
    assert "rate_limit.degraded" in caplog.text
    assert "auth0|private-user" not in caplog.text


@pytest.mark.asyncio
async def test_limiter_factory_uses_async_redis_storage_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches constructing a sync storage URI or leaking the async Redis connection."""

    monkeypatch.setattr("ingestion.rate_limits.RedisStorage", RecordingStorage)
    monkeypatch.setattr("ingestion.rate_limits.MovingWindowRateLimiter", RecordingLimiter)

    limiter = RedisBurstLimiter.from_redis_url(
        "redis://redis.example:6379/3", amount=5, window_seconds=60
    )

    storage = limiter._storage
    assert isinstance(storage, RecordingStorage)
    assert storage.uri == "async+redis://redis.example:6379/3"
    assert storage.options == {"implementation": "redispy", "wrap_exceptions": True}
    await limiter.close()
    assert storage.connection.closed is True


@pytest.mark.asyncio
async def test_limiter_does_not_hide_programming_errors() -> None:
    """Catches treating an adapter bug as a temporary Redis outage."""

    limiter = RedisBurstLimiter(BuggyStrategy(), amount=5, window_seconds=60)

    with pytest.raises(RuntimeError, match="programming error"):
        await limiter.hit("auth0|private-user", "import")
