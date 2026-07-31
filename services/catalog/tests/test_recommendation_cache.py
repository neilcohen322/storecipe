import asyncio
import json
import logging
from types import SimpleNamespace
from uuid import UUID

import pytest
from redis.exceptions import RedisError

from catalog.recommendations import (
    RecommendationItem,
    RecommendationRequest,
    RecommendationResponse,
    RecommendationScoreComponents,
    recommendation_request_hash,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.last_key = ""
        self.last_expiry: int | None = None
        self.deleted_keys: list[str] = []
        self.get_error: BaseException | None = None
        self.set_error: BaseException | None = None
        self.delete_error: BaseException | None = None
        self.exceptions = SimpleNamespace(RedisError=RedisError)

    async def get(self, key: str) -> str | bytes | None:
        self.last_key = key
        if self.get_error:
            raise self.get_error
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> bool:
        self.last_key = key
        self.last_expiry = ex
        if self.set_error:
            raise self.set_error
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        if self.delete_error:
            raise self.delete_error
        self.values.pop(key, None)
        return 1


class HangingRedis:
    async def get(self, key: str) -> None:
        del key
        await asyncio.Event().wait()

    async def set(self, key: str, value: str, ex: int) -> None:
        del key, value, ex
        await asyncio.Event().wait()

    async def delete(self, key: str) -> None:
        del key
        await asyncio.Event().wait()


class InvalidUtf8Redis(FakeRedis):
    async def get(self, key: str) -> str | bytes | None:
        self.last_key = key
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


class InvalidValueWithHangingDeleteRedis(HangingRedis):
    async def get(self, key: str) -> str:
        del key
        return "not-json"


@pytest.fixture
def user_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000042")


@pytest.fixture
def recommendation_request() -> RecommendationRequest:
    return RecommendationRequest(query="Wine", must_include_ingredients=["Basil"])


@pytest.fixture
def response(recommendation_request: RecommendationRequest) -> RecommendationResponse:
    return RecommendationResponse(
        request=recommendation_request,
        catalog_version=7,
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


@pytest.mark.asyncio
async def test_cache_round_trip_uses_versioned_opaque_key(
    user_id: UUID, recommendation_request: RecommendationRequest, response: RecommendationResponse
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    redis = FakeRedis()
    cache = RecommendationCache(redis, ttl_seconds=1800)

    written = await cache.set(user_id, 7, recommendation_request, response)
    read = await cache.get(user_id, 7, recommendation_request)

    assert written is True
    assert read == CacheRead(CacheReadOutcome.HIT, response)
    assert redis.last_key == (
        f"recommendations:{user_id}:7:{recommendation_request_hash(recommendation_request)}"
    )
    assert redis.last_expiry == 1800
    assert "wine" not in redis.last_key


@pytest.mark.asyncio
async def test_cache_returns_miss_for_absent_key(
    user_id: UUID, recommendation_request: RecommendationRequest
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    cache = RecommendationCache(FakeRedis())

    assert await cache.get(user_id, 7, recommendation_request) == CacheRead(
        CacheReadOutcome.MISS, None
    )


@pytest.mark.asyncio
async def test_cache_does_not_reuse_other_catalog_version(
    user_id: UUID, recommendation_request: RecommendationRequest, response: RecommendationResponse
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    cache = RecommendationCache(FakeRedis())
    await cache.set(user_id, 7, recommendation_request, response)

    assert await cache.get(user_id, 8, recommendation_request) == CacheRead(
        CacheReadOutcome.MISS, None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"schema_version": 2, "request_hash": "a" * 64, "result": {}}),
        json.dumps({"schema_version": 1, "request_hash": "b" * 64, "result": {}}),
    ],
)
async def test_cache_invalidates_malformed_or_mismatched_envelopes(
    user_id: UUID, recommendation_request: RecommendationRequest, payload: str
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    redis = FakeRedis()
    cache = RecommendationCache(redis)
    key = cache.key(user_id, 7, recommendation_request_hash(recommendation_request))
    redis.values[key] = payload

    assert await cache.get(user_id, 7, recommendation_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )
    assert redis.deleted_keys == [key]


@pytest.mark.asyncio
async def test_cache_invalidates_envelope_with_response_for_different_request_or_version(
    user_id: UUID, recommendation_request: RecommendationRequest, response: RecommendationResponse
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    redis = FakeRedis()
    cache = RecommendationCache(redis)
    key = cache.key(user_id, 7, recommendation_request_hash(recommendation_request))
    redis.values[key] = json.dumps(
        {
            "schema_version": 1,
            "request_hash": recommendation_request_hash(recommendation_request),
            "result": response.model_dump(mode="json", by_alias=False)
            | {"catalog_version": 8, "request": {"query": "different"}},
        }
    )

    assert await cache.get(user_id, 7, recommendation_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )


@pytest.mark.asyncio
async def test_cache_ignores_redis_error_while_deleting_invalid_value(
    user_id: UUID, recommendation_request: RecommendationRequest
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    redis = FakeRedis()
    cache = RecommendationCache(redis)
    key = cache.key(user_id, 7, recommendation_request_hash(recommendation_request))
    redis.values[key] = "not-json"
    redis.delete_error = RedisError()

    assert await cache.get(user_id, 7, recommendation_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )


@pytest.mark.asyncio
async def test_cache_fails_open_for_redis_read_and_write_errors(
    user_id: UUID, recommendation_request: RecommendationRequest, response: RecommendationResponse
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    read_redis = FakeRedis()
    read_redis.get_error = RedisError()
    write_redis = FakeRedis()
    write_redis.set_error = TimeoutError()

    assert await RecommendationCache(read_redis).get(
        user_id, 7, recommendation_request
    ) == CacheRead(CacheReadOutcome.ERROR, None)
    assert (
        await RecommendationCache(write_redis).set(user_id, 7, recommendation_request, response)
        is False
    )


@pytest.mark.asyncio
async def test_cache_bounds_hanging_read_write_and_invalid_delete(
    user_id: UUID, recommendation_request: RecommendationRequest, response: RecommendationResponse
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    hanging_cache = RecommendationCache(HangingRedis(), redis_timeout_seconds=0.01)
    invalid_cache = RecommendationCache(
        InvalidValueWithHangingDeleteRedis(), redis_timeout_seconds=0.01
    )

    async with asyncio.timeout(0.2):
        read = await hanging_cache.get(user_id, 7, recommendation_request)
        written = await hanging_cache.set(user_id, 7, recommendation_request, response)
        invalid = await invalid_cache.get(user_id, 7, recommendation_request)

    assert read == CacheRead(CacheReadOutcome.ERROR, None)
    assert written is False
    assert invalid == CacheRead(CacheReadOutcome.INVALID, None)


@pytest.mark.asyncio
async def test_cache_invalidates_decode_failure(
    user_id: UUID, recommendation_request: RecommendationRequest
) -> None:
    from catalog.recommendation_cache import CacheRead, CacheReadOutcome, RecommendationCache

    redis = InvalidUtf8Redis()
    cache = RecommendationCache(redis)
    key = cache.key(user_id, 7, recommendation_request_hash(recommendation_request))

    assert await cache.get(user_id, 7, recommendation_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )
    assert redis.deleted_keys == [key]


@pytest.mark.asyncio
async def test_cache_events_do_not_expose_query_or_raw_subject(
    caplog: pytest.LogCaptureFixture,
    user_id: UUID,
    response: RecommendationResponse,
) -> None:
    from catalog.recommendation_cache import RecommendationCache

    recommendation_request = RecommendationRequest(
        query="top secret wine", must_include_ingredients=["private subject"]
    )
    redis = FakeRedis()
    cache = RecommendationCache(redis)
    caplog.set_level(logging.INFO)

    response = response.model_copy(update={"request": recommendation_request})
    await cache.set(user_id, 7, recommendation_request, response)
    await cache.get(user_id, 7, recommendation_request)

    assert "recommendation_cache.write" in caplog.text
    assert "recommendation_cache.hit" in caplog.text
    assert "top secret wine" not in caplog.text
    assert "private subject" not in caplog.text
