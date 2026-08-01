import asyncio
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.models import Ingredient, Recipe, RecipeTag, Tag
from catalog.recipe_queries import (
    RecipeMatch,
    RecipeQueryItem,
    RecipeQueryPage,
    RecipeQueryRequest,
    decode_cursor,
    recipe_query_hash,
)
from catalog.repositories.recipe_queries import QueryCandidate
from catalog.schemas import RecipeView


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
    async def get(self, key: str) -> str | None:
        del key
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def set(self, key: str, value: str, ex: int) -> None:
        del key, value, ex
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def delete(self, key: str) -> None:
        del key
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


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
def recipe_query_request() -> RecipeQueryRequest:
    return RecipeQueryRequest(text="Wine", available_ingredients=["Basil"])


@pytest.fixture
def page() -> RecipeQueryPage:
    return RecipeQueryPage(
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
                ),
                match=RecipeMatch(ingredient_coverage=1.0),
            )
        ]
    )


@pytest.mark.asyncio
async def test_cache_round_trip_uses_versioned_opaque_key(
    user_id: UUID, recipe_query_request: RecipeQueryRequest, page: RecipeQueryPage
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    redis = FakeRedis()
    cache = RecipeQueryCache(redis, ttl_seconds=1800)

    written = await cache.set(user_id, 7, recipe_query_request, page)
    read = await cache.get(user_id, 7, recipe_query_request)

    assert written is True
    assert read == CacheRead(CacheReadOutcome.HIT, page)
    assert redis.last_key == f"recipe_queries:{user_id}:7:{recipe_query_hash(recipe_query_request)}"
    assert redis.last_expiry == 1800
    assert "wine" not in redis.last_key


@pytest.mark.asyncio
async def test_cache_returns_miss_for_absent_key(
    user_id: UUID, recipe_query_request: RecipeQueryRequest
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    cache = RecipeQueryCache(FakeRedis())

    assert await cache.get(user_id, 7, recipe_query_request) == CacheRead(
        CacheReadOutcome.MISS, None
    )


@pytest.mark.asyncio
async def test_cache_does_not_reuse_other_catalog_version(
    user_id: UUID, recipe_query_request: RecipeQueryRequest, page: RecipeQueryPage
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    cache = RecipeQueryCache(FakeRedis())
    await cache.set(user_id, 7, recipe_query_request, page)

    assert await cache.get(user_id, 8, recipe_query_request) == CacheRead(
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
    user_id: UUID, recipe_query_request: RecipeQueryRequest, payload: str
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    redis = FakeRedis()
    cache = RecipeQueryCache(redis)
    key = cache.key(user_id, 7, recipe_query_hash(recipe_query_request))
    redis.values[key] = payload

    assert await cache.get(user_id, 7, recipe_query_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )
    assert redis.deleted_keys == [key]


@pytest.mark.asyncio
async def test_cache_invalidates_envelope_with_page_for_different_request_or_version(
    user_id: UUID, recipe_query_request: RecipeQueryRequest, page: RecipeQueryPage
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    redis = FakeRedis()
    cache = RecipeQueryCache(redis)
    key = cache.key(user_id, 7, recipe_query_hash(recipe_query_request))
    redis.values[key] = json.dumps(
        {
            "schema_version": 1,
            "request_hash": recipe_query_hash(recipe_query_request),
            "catalog_version": 8,
            "request": {"text": "different"},
            "result": page.model_dump(mode="json", by_alias=False),
        }
    )

    assert await cache.get(user_id, 7, recipe_query_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )


@pytest.mark.asyncio
async def test_cache_ignores_redis_error_while_deleting_invalid_value(
    user_id: UUID, recipe_query_request: RecipeQueryRequest
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    redis = FakeRedis()
    cache = RecipeQueryCache(redis)
    key = cache.key(user_id, 7, recipe_query_hash(recipe_query_request))
    redis.values[key] = "not-json"
    redis.delete_error = RedisError()

    assert await cache.get(user_id, 7, recipe_query_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )


@pytest.mark.asyncio
async def test_cache_fails_open_for_redis_read_and_write_errors(
    user_id: UUID, recipe_query_request: RecipeQueryRequest, page: RecipeQueryPage
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    read_redis = FakeRedis()
    read_redis.get_error = RedisError()
    write_redis = FakeRedis()
    write_redis.set_error = TimeoutError()

    assert await RecipeQueryCache(read_redis).get(user_id, 7, recipe_query_request) == CacheRead(
        CacheReadOutcome.ERROR, None
    )
    assert not await RecipeQueryCache(write_redis).set(user_id, 7, recipe_query_request, page)


@pytest.mark.asyncio
async def test_cache_bounds_hanging_read_write_and_invalid_delete(
    user_id: UUID, recipe_query_request: RecipeQueryRequest, page: RecipeQueryPage
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    hanging_cache = RecipeQueryCache(HangingRedis(), redis_timeout_seconds=0.01)
    invalid_cache = RecipeQueryCache(
        InvalidValueWithHangingDeleteRedis(), redis_timeout_seconds=0.01
    )

    async with asyncio.timeout(0.2):
        read = await hanging_cache.get(user_id, 7, recipe_query_request)
        written = await hanging_cache.set(user_id, 7, recipe_query_request, page)
        invalid = await invalid_cache.get(user_id, 7, recipe_query_request)

    assert read == CacheRead(CacheReadOutcome.ERROR, None)
    assert written is False
    assert invalid == CacheRead(CacheReadOutcome.INVALID, None)


@pytest.mark.asyncio
async def test_cache_invalidates_decode_failure(
    user_id: UUID, recipe_query_request: RecipeQueryRequest
) -> None:
    from catalog.recipe_query_cache import CacheRead, CacheReadOutcome, RecipeQueryCache

    redis = InvalidUtf8Redis()
    cache = RecipeQueryCache(redis)
    key = cache.key(user_id, 7, recipe_query_hash(recipe_query_request))

    assert await cache.get(user_id, 7, recipe_query_request) == CacheRead(
        CacheReadOutcome.INVALID, None
    )
    assert redis.deleted_keys == [key]


@pytest.mark.asyncio
async def test_cache_events_do_not_expose_query_or_raw_subject(
    caplog: pytest.LogCaptureFixture, user_id: UUID, page: RecipeQueryPage
) -> None:
    from catalog.recipe_query_cache import RecipeQueryCache

    recipe_query_request = RecipeQueryRequest(
        text="top secret wine", available_ingredients=["private subject"]
    )
    redis = FakeRedis()
    cache = RecipeQueryCache(redis)
    caplog.set_level(logging.INFO)

    await cache.set(user_id, 7, recipe_query_request, page)
    await cache.get(user_id, 7, recipe_query_request)

    assert "recipe_query_cache.write" in caplog.text
    assert "recipe_query_cache.hit" in caplog.text
    assert "top secret wine" not in caplog.text
    assert "private subject" not in caplog.text


def _candidate(
    recipe_id: str,
    title: str,
    ingredients: list[str],
    tags: list[str],
) -> QueryCandidate:
    recipe = Recipe(
        id=UUID(recipe_id),
        title=title,
        total_minutes=20,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        ingredients=[
            Ingredient(
                position=index,
                raw_text=name,
                name=name,
                normalized_name=name,
            )
            for index, name in enumerate(ingredients)
        ],
        recipe_tags=[RecipeTag(tag=Tag(name=name)) for name in tags],
    )
    return QueryCandidate(
        recipe=recipe,
        rating=4,
        ingredient_coverage=Decimal("0.5"),
        tag_coverage=Decimal("0.5"),
    )


def test_build_query_page_uses_limit_plus_one_for_factual_matches_and_cursor() -> None:
    from catalog.services.recipe_queries import build_query_page

    request = RecipeQueryRequest(
        available_ingredients=["basil", "garlic"],
        preferred_tags=["quick", "vegan"],
        sort=["title:asc"],
        limit=1,
    )
    candidates = [
        _candidate(
            "00000000-0000-0000-0000-000000000001",
            "A",
            ["basil", "salt"],
            ["quick"],
        ),
        _candidate("00000000-0000-0000-0000-000000000002", "B", ["garlic"], ["vegan"]),
        _candidate("00000000-0000-0000-0000-000000000003", "C", [], []),
    ]

    page = build_query_page(request, 7, candidates)

    assert len(page.items) == 1
    assert page.items[0].recipe.title == "A"
    assert page.items[0].match is not None
    assert page.items[0].match.missing_ingredients == ["salt"]
    assert "garlic" not in page.items[0].match.missing_ingredients
    assert page.items[0].match.matched_preferred_tags == ["quick"]
    assert page.items[0].match.missing_preferred_tags == ["vegan"]
    assert page.next_cursor is not None
    cursor = decode_cursor(page.next_cursor)
    assert cursor.schema_version == 2
    assert cursor.catalog_version == 7
    assert cursor.recipe_id == UUID("00000000-0000-0000-0000-000000000001")


def test_build_query_page_has_no_cursor_without_an_extra_candidate() -> None:
    from catalog.services.recipe_queries import build_query_page

    request = RecipeQueryRequest(limit=2)
    page = build_query_page(
        request,
        7,
        [_candidate("00000000-0000-0000-0000-000000000001", "A", [], [])],
    )

    assert len(page.items) == 1
    assert page.next_cursor is None


def test_build_query_page_omits_match_without_context() -> None:
    from catalog.services.recipe_queries import build_query_page

    page = build_query_page(
        RecipeQueryRequest(),
        7,
        [_candidate("00000000-0000-0000-0000-000000000001", "A", ["salt"], ["quick"])],
    )

    assert page.items[0].match is None


def test_title_cursor_fits_limit_for_worst_case_unicode_title() -> None:
    from catalog.services.recipe_queries import build_query_page

    request = RecipeQueryRequest(sort=["title:asc"], limit=1)
    boundary_id = "00000000-0000-0000-0000-000000000001"
    page = build_query_page(
        request,
        7,
        [
            _candidate(boundary_id, "\U0001f600" * 200, [], []),
            _candidate("00000000-0000-0000-0000-000000000002", "Later", [], []),
        ],
    )

    assert page.next_cursor is not None
    assert len(page.next_cursor) <= 1024
    assert decode_cursor(page.next_cursor).recipe_id == UUID(boundary_id)


@pytest.mark.asyncio
async def test_query_recipes_hits_cache_without_fetching_candidates(
    monkeypatch: pytest.MonkeyPatch,
    recipe_query_request: RecipeQueryRequest,
    page: RecipeQueryPage,
) -> None:
    from catalog.services import recipe_queries

    user = SimpleNamespace(id=UUID("00000000-0000-0000-0000-000000000042"), catalog_version=7)

    class Cache:
        async def get(self, user_id: UUID, version: int, request: RecipeQueryRequest) -> Any:
            assert (user_id, version, request) == (user.id, 7, recipe_query_request)
            return SimpleNamespace(value=page)

        async def set(self, *args: object) -> bool:
            raise AssertionError("cache hit must not write")

    async def resolve_user(session: object, subject: str) -> object:
        assert (session, subject) == ("session", "subject")
        return user

    async def fetch_candidates(*args: object, **kwargs: object) -> list[QueryCandidate]:
        raise AssertionError("cache hit must not fetch candidates")

    monkeypatch.setattr(recipe_queries, "resolve_user", resolve_user)
    monkeypatch.setattr(recipe_queries, "fetch_query_candidates", fetch_candidates)

    assert (
        await recipe_queries.query_recipes(
            cast(AsyncSession, "session"),
            "subject",
            recipe_query_request,
            cast(Any, Cache()),
        )
        == page
    )


@pytest.mark.asyncio
async def test_query_recipes_fetches_limit_plus_one_and_writes_on_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
    recipe_query_request: RecipeQueryRequest,
    page: RecipeQueryPage,
) -> None:
    from catalog.services import recipe_queries

    request = recipe_query_request.model_copy(update={"limit": 3})
    user = SimpleNamespace(id=UUID("00000000-0000-0000-0000-000000000042"), catalog_version=7)
    cursor = object()
    candidate = _candidate("00000000-0000-0000-0000-000000000001", "A", [], [])
    calls: dict[str, object] = {}

    class Cache:
        async def get(self, user_id: UUID, version: int, requested: RecipeQueryRequest) -> Any:
            calls["get"] = (user_id, version, requested)
            return SimpleNamespace(value=None)

        async def set(
            self,
            user_id: UUID,
            version: int,
            requested: RecipeQueryRequest,
            result: RecipeQueryPage,
        ) -> bool:
            calls["set"] = (user_id, version, requested, result)
            return True

    async def resolve_user(session: object, subject: str) -> object:
        calls["resolve"] = (session, subject)
        return user

    def validate_request_cursor(requested: RecipeQueryRequest, version: int) -> object:
        calls["cursor"] = (requested, version)
        return cursor

    async def fetch_candidates(
        session: object,
        user_id: UUID,
        requested: RecipeQueryRequest,
        *,
        page_size: int,
        cursor: object,
    ) -> list[QueryCandidate]:
        calls["fetch"] = (session, user_id, requested, page_size, cursor)
        return [candidate]

    def build_page(
        requested: RecipeQueryRequest, version: int, candidates: list[QueryCandidate]
    ) -> RecipeQueryPage:
        calls["build"] = (requested, version, candidates)
        return page

    monkeypatch.setattr(recipe_queries, "resolve_user", resolve_user)
    monkeypatch.setattr(recipe_queries, "validate_request_cursor", validate_request_cursor)
    monkeypatch.setattr(recipe_queries, "fetch_query_candidates", fetch_candidates)
    monkeypatch.setattr(recipe_queries, "build_query_page", build_page)

    result = await recipe_queries.query_recipes(
        cast(AsyncSession, "session"), "subject", request, cast(Any, Cache())
    )

    assert result is page
    assert calls["cursor"] == (request, 7)
    assert calls["fetch"] == ("session", user.id, request, 4, cursor)
    assert calls["build"] == (request, 7, [candidate])
    assert calls["set"] == (user.id, 7, request, page)
