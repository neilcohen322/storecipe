"""Safe, async burst limiting for authenticated Catalog mutations."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlsplit, urlunsplit

from limits import RateLimitItemPerSecond
from limits.aio.storage import RedisStorage
from limits.aio.strategies import MovingWindowRateLimiter
from limits.errors import StorageError
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """The limiter result exposed to the HTTP boundary."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    degraded: bool = False


class BurstLimiter(Protocol):
    async def hit(self, subject: str, operation: str) -> RateLimitDecision:
        """Record one operation for a subject and return window metadata."""


class _AsyncStrategy(Protocol):
    async def hit(self, item: RateLimitItemPerSecond, *identifiers: str) -> bool: ...

    async def get_window_stats(
        self, item: RateLimitItemPerSecond, *identifiers: str
    ) -> tuple[float, int]: ...


class _AsyncConnection(Protocol):
    async def aclose(self) -> None: ...


class _RedisStorageBridge(Protocol):
    def get_connection(self) -> _AsyncConnection: ...


class RedisBurstLimiter:
    """Adapter around a ``limits.aio`` moving-window strategy.

    Redis failures fail closed. The adapter logs only a stable event name;
    the authenticated subject and URL are never included in operational output.
    """

    def __init__(
        self,
        strategy: _AsyncStrategy,
        *,
        amount: int,
        window_seconds: int,
        clock: Callable[[], float] | None = None,
        storage: RedisStorage | None = None,
    ) -> None:
        self._strategy = strategy
        self._storage = storage
        self._item = RateLimitItemPerSecond(amount, multiples=window_seconds)
        self._amount = amount
        self._window_seconds = window_seconds
        self._clock = clock or time.time

    @classmethod
    def from_redis_url(
        cls, redis_url: str, *, amount: int, window_seconds: int
    ) -> RedisBurstLimiter:
        """Create a moving-window limiter backed by the configured Redis instance."""

        storage = RedisStorage(
            _as_async_storage_url(redis_url), implementation="redispy", wrap_exceptions=True
        )
        return cls(
            MovingWindowRateLimiter(storage),
            amount=amount,
            window_seconds=window_seconds,
            storage=storage,
        )

    async def hit(self, subject: str, operation: str) -> RateLimitDecision:
        identifier = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        try:
            allowed = await self._strategy.hit(self._item, operation, identifier)
            reset_at, remaining = await self._strategy.get_window_stats(
                self._item, operation, identifier
            )
            return RateLimitDecision(
                allowed=allowed,
                limit=self._amount,
                remaining=max(0, remaining),
                reset_at=int(reset_at),
            )
        except (RedisError, StorageError):
            logger.info("rate_limit.unavailable")
            return RateLimitDecision(
                allowed=False,
                limit=self._amount,
                remaining=0,
                reset_at=int(self._clock()) + self._window_seconds,
                degraded=True,
            )

    async def close(self) -> None:
        """Close the async Redis client created by :meth:`from_redis_url`."""

        if self._storage is not None:
            bridge = cast(_RedisStorageBridge, self._storage.bridge)
            await bridge.get_connection().aclose()


def _as_async_storage_url(redis_url: str) -> str:
    parsed = urlsplit(redis_url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError("Redis URL must use redis:// or rediss://")
    if parsed.port is None:
        netloc = f"{parsed.netloc}:6379"
        redis_url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return f"async+{redis_url}"


class UnlimitedBurstLimiter:
    """Deterministic no-op limiter for tests."""

    def __init__(self, *, limit: int = 0, window_seconds: int = 0) -> None:
        self._limit = limit
        self._window_seconds = window_seconds

    async def hit(self, subject: str, operation: str) -> RateLimitDecision:
        del subject, operation
        return RateLimitDecision(
            allowed=True,
            limit=self._limit,
            remaining=self._limit,
            reset_at=(0 if self._window_seconds == 0 else int(time.time()) + self._window_seconds),
        )
