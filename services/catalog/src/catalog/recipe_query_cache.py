import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from pydantic import Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from catalog.recipe_queries import RecipeQueryPage, RecipeQueryRequest, recipe_query_hash
from catalog.schemas import ApiModel

logger = logging.getLogger(__name__)


def create_redis_client(redis_url: str, *, timeout_seconds: float) -> Redis:
    """Create the byte-oriented client used by cache reads and readiness."""
    return cast(
        Redis,
        Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        ),
    )


class RedisClient(Protocol):
    async def get(self, key: str) -> str | bytes | None: ...

    async def set(self, key: str, value: str, ex: int) -> object: ...

    async def delete(self, key: str) -> object: ...


class CacheReadOutcome(str, Enum):
    HIT = "hit"
    MISS = "miss"
    INVALID = "invalid"
    ERROR = "error"


@dataclass(frozen=True)
class CacheRead:
    outcome: CacheReadOutcome
    value: RecipeQueryPage | None


class CacheEnvelope(ApiModel):
    schema_version: Literal[2] = 2
    request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    catalog_version: Annotated[int, Field(ge=0)]
    request: RecipeQueryRequest
    result: RecipeQueryPage


class RecipeQueryCache:
    def __init__(
        self,
        redis: RedisClient,
        ttl_seconds: int = 1800,
        redis_timeout_seconds: float = 1.0,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._redis_timeout_seconds = redis_timeout_seconds

    def key(self, user_id: UUID, catalog_version: int, request_hash: str) -> str:
        return f"recipe_queries:v2:{user_id}:{catalog_version}:{request_hash}"

    async def get(
        self, user_id: UUID, catalog_version: int, request: RecipeQueryRequest
    ) -> CacheRead:
        started_at = time.perf_counter()
        expected_hash = recipe_query_hash(request)
        key = self.key(user_id, catalog_version, expected_hash)
        try:
            async with asyncio.timeout(self._redis_timeout_seconds):
                cached = await self._redis.get(key)
        except UnicodeError:
            await self._delete_invalid(key)
            return self._read_outcome(CacheReadOutcome.INVALID, started_at)
        except (RedisError, TimeoutError, ConnectionError, OSError):
            return self._read_outcome(CacheReadOutcome.ERROR, started_at)

        if cached is None:
            return self._read_outcome(CacheReadOutcome.MISS, started_at)

        try:
            envelope = CacheEnvelope.model_validate_json(cached)
            if (
                envelope.request_hash != expected_hash
                or envelope.catalog_version != catalog_version
                or envelope.request != request
            ):
                raise ValueError("Cached recipe query does not match the request")
        except (ValidationError, ValueError, TypeError):
            await self._delete_invalid(key)
            return self._read_outcome(CacheReadOutcome.INVALID, started_at)

        self._emit(CacheReadOutcome.HIT.value, started_at)
        return CacheRead(outcome=CacheReadOutcome.HIT, value=envelope.result)

    async def set(
        self,
        user_id: UUID,
        catalog_version: int,
        request: RecipeQueryRequest,
        page: RecipeQueryPage,
    ) -> bool:
        started_at = time.perf_counter()
        request_hash = recipe_query_hash(request)
        key = self.key(user_id, catalog_version, request_hash)
        envelope = CacheEnvelope(
            request_hash=request_hash,
            catalog_version=catalog_version,
            request=request,
            result=page,
        )
        try:
            async with asyncio.timeout(self._redis_timeout_seconds):
                await self._redis.set(
                    key, envelope.model_dump_json(by_alias=False), ex=self._ttl_seconds
                )
        except (RedisError, TimeoutError, ConnectionError, OSError):
            self._emit(CacheReadOutcome.ERROR.value, started_at)
            return False

        self._emit("write", started_at)
        return True

    async def _delete_invalid(self, key: str) -> None:
        try:
            async with asyncio.timeout(self._redis_timeout_seconds):
                await self._redis.delete(key)
        except (RedisError, TimeoutError, ConnectionError, OSError):
            pass

    def _read_outcome(self, outcome: CacheReadOutcome, started_at: float) -> CacheRead:
        self._emit(outcome.value, started_at)
        return CacheRead(outcome=outcome, value=None)

    def _emit(self, event: str, started_at: float) -> None:
        logger.info(
            "recipe_query_cache.%s",
            event,
            extra={
                "outcome": event,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000),
            },
        )
