import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import column
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.main import _status_for
from catalog.models import Base, Ingredient, Recipe, User
from catalog.recipe_queries import (
    RecipeQueryCursor,
    RecipeQueryRequest,
    SortDirection,
    decode_cursor,
    encode_cursor,
    recipe_query_hash,
    validate_request_cursor,
)
from catalog.repositories.recipe_queries import (
    QueryCandidate,
    _after_value,
    effective_sort,
    fetch_query_candidates,
)
from catalog.services.errors import InvalidCursor, StaleRecipeQueryCursor

OWNER_ID = UUID("10000000-0000-0000-0000-000000000001")
RECIPE_IDS = tuple(UUID(f"30000000-0000-0000-0000-{index:012d}") for index in range(1, 6))


def _sort_tokens(request: RecipeQueryRequest) -> list[str]:
    return [f"{item.field.value}:{item.direction.value}" for item in effective_sort(request)]


def _cursor_for(
    request: RecipeQueryRequest,
    *,
    catalog_version: int,
    recipe_id: UUID,
) -> RecipeQueryCursor:
    return RecipeQueryCursor(
        schema_version=2,
        query_hash=recipe_query_hash(request, exclude_cursor=True),
        catalog_version=catalog_version,
        sort=_sort_tokens(request),
        recipe_id=recipe_id,
    )


def test_cursor_round_trip_is_compact_and_url_safe() -> None:
    cursor = RecipeQueryCursor(
        schema_version=2,
        query_hash="a" * 64,
        catalog_version=7,
        sort=["rating:desc", "recipeId:asc"],
        recipe_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    raw = encode_cursor(cursor)

    assert "=" not in raw
    assert "/" not in raw
    assert "+" not in raw
    assert decode_cursor(raw) == cursor


@pytest.mark.parametrize("raw", ["", "not-a-cursor", "%%%", "e30"])
def test_decode_cursor_maps_wire_failures_to_invalid_cursor(raw: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(raw)


def test_validate_request_cursor_binds_hash_sort_and_catalog_version() -> None:
    request = RecipeQueryRequest(sort=["totalMinutes:asc"], limit=2)
    cursor = _cursor_for(
        request,
        catalog_version=7,
        recipe_id=RECIPE_IDS[1],
    )
    request_with_cursor = request.model_copy(update={"cursor": encode_cursor(cursor)})

    assert validate_request_cursor(request_with_cursor, 7) == cursor

    changed_filter = request_with_cursor.model_copy(update={"ingredients": ["lime"]})
    with pytest.raises(InvalidCursor):
        validate_request_cursor(changed_filter, 7)

    wrong_sort = cursor.model_copy(update={"sort": ["totalMinutes:desc", "recipeId:asc"]})
    wrong_sort_request = request.model_copy(update={"cursor": encode_cursor(wrong_sort)})
    with pytest.raises(InvalidCursor):
        validate_request_cursor(wrong_sort_request, 7)

    with pytest.raises(StaleRecipeQueryCursor):
        validate_request_cursor(request_with_cursor, 8)


def test_validate_request_cursor_rejects_legacy_naive_datetime_values() -> None:
    request = RecipeQueryRequest(sort=["createdAt:asc"])
    payload = {
        "schema_version": 1,
        "query_hash": recipe_query_hash(request, exclude_cursor=True),
        "catalog_version": 7,
        "sort": ["createdAt:asc", "recipeId:asc"],
        "values": ["2026-01-01T00:00:00", str(RECIPE_IDS[0])],
    }
    raw = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    request_with_cursor = request.model_copy(update={"cursor": raw})

    with pytest.raises(InvalidCursor):
        validate_request_cursor(request_with_cursor, 7)


def test_stale_cursor_maps_to_conflict_and_invalid_cursor_stays_unprocessable() -> None:
    assert _status_for(StaleRecipeQueryCursor()) == 409
    assert _status_for(InvalidCursor()) == 422


def test_ascending_after_value_includes_nulls_last() -> None:
    predicate = _after_value(column("sort_value"), 5, SortDirection.ASC)

    assert predicate is not None
    sql = str(predicate.compile(compile_kwargs={"literal_binds": True}))
    assert "sort_value > 5" in sql
    assert "sort_value IS NULL" in sql


def test_descending_branch_for_cursor_value() -> None:
    predicate = _after_value(column("sort_value"), 5, SortDirection.DESC)

    assert predicate is not None
    sql = str(predicate.compile(compile_kwargs={"literal_binds": True}))
    assert "sort_value < 5" in sql
    assert "sort_value IS NULL" in sql


@dataclass(frozen=True)
class CursorCatalog:
    session_factory: async_sessionmaker[AsyncSession]
    owner_id: UUID
    catalog_version: int


@pytest_asyncio.fixture
async def cursor_catalog() -> AsyncIterator[CursorCatalog]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user = User(id=OWNER_ID, auth_subject="auth0|cursor-owner", catalog_version=7)
    recipes = [
        Recipe(
            id=recipe_id,
            user_id=OWNER_ID,
            title=f"Recipe {position}",
            total_minutes=(position * 10 if position <= 2 else None),
            created_at=datetime(2026, 1, position, tzinfo=UTC),
            ingredients=[
                Ingredient(
                    position=0,
                    raw_text=f"1 ingredient-{position}",
                    name=f"Ingredient {position}",
                    normalized_name=f"ingredient {position}",
                    canonical_name=f"ingredient {position}",
                )
            ],
        )
        for position, recipe_id in enumerate(RECIPE_IDS, start=1)
    ]
    async with session_factory() as session:
        session.add_all([user, *recipes])
        await session.commit()

    try:
        yield CursorCatalog(session_factory, OWNER_ID, user.catalog_version)
    finally:
        await engine.dispose()


async def _fetch_page(
    catalog: CursorCatalog,
    request: RecipeQueryRequest,
    cursor: RecipeQueryCursor | None = None,
) -> list[QueryCandidate]:
    async with catalog.session_factory() as session:
        return await fetch_query_candidates(
            session,
            catalog.owner_id,
            request,
            page_size=2,
            cursor=cursor,
        )


@pytest.mark.asyncio
async def test_page_cursor_traversal_returns_two_two_and_one_without_duplicates(
    cursor_catalog: CursorCatalog,
) -> None:
    request = RecipeQueryRequest(sort=["totalMinutes:asc"], limit=2)

    first = await _fetch_page(cursor_catalog, request)
    first_cursor = _cursor_for(
        request,
        catalog_version=cursor_catalog.catalog_version,
        recipe_id=first[-1].recipe.id,
    )
    second = await _fetch_page(cursor_catalog, request, first_cursor)
    second_cursor = _cursor_for(
        request,
        catalog_version=cursor_catalog.catalog_version,
        recipe_id=second[-1].recipe.id,
    )
    third = await _fetch_page(cursor_catalog, request, second_cursor)

    pages = [first, second, third]
    ids = [candidate.recipe.id for page in pages for candidate in page]
    assert [len(page) for page in pages] == [2, 2, 1]
    assert ids == list(RECIPE_IDS)
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_null_sort_boundary_uses_later_recipe_id_tie_breaker(
    cursor_catalog: CursorCatalog,
) -> None:
    request = RecipeQueryRequest(sort=["totalMinutes:asc"], limit=2)

    first = await _fetch_page(cursor_catalog, request)
    first_cursor = _cursor_for(
        request,
        catalog_version=cursor_catalog.catalog_version,
        recipe_id=first[-1].recipe.id,
    )
    second = await _fetch_page(cursor_catalog, request, first_cursor)
    second_cursor = _cursor_for(
        request,
        catalog_version=cursor_catalog.catalog_version,
        recipe_id=second[-1].recipe.id,
    )
    third = await _fetch_page(cursor_catalog, request, second_cursor)

    assert [candidate.recipe.id for candidate in first] == list(RECIPE_IDS[:2])
    assert [candidate.recipe.id for candidate in second] == list(RECIPE_IDS[2:4])
    assert [candidate.recipe.id for candidate in third] == [RECIPE_IDS[4]]


@pytest.mark.asyncio
async def test_title_cursor_reconstructs_database_lower_value_for_unicode() -> None:
    from catalog.services.recipe_queries import build_query_page

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_id = UUID("10000000-0000-0000-0000-000000000099")
    first_id = UUID("30000000-0000-0000-0000-000000000091")
    second_id = UUID("30000000-0000-0000-0000-000000000092")
    recipes = [
        Recipe(
            id=first_id,
            user_id=owner_id,
            title="\u00c4 boundary",
            ingredients=[
                Ingredient(
                    position=0,
                    raw_text="unicode",
                    name="unicode",
                    normalized_name="unicode",
                    canonical_name="unicode",
                )
            ],
        ),
        Recipe(
            id=second_id,
            user_id=owner_id,
            title="\u00d6 after",
            ingredients=[
                Ingredient(
                    position=0,
                    raw_text="unicode",
                    name="unicode",
                    normalized_name="unicode",
                    canonical_name="unicode",
                )
            ],
        ),
    ]
    try:
        async with session_factory.begin() as session:
            session.add(User(id=owner_id, auth_subject="auth0|unicode-owner", catalog_version=7))
            session.add_all(recipes)

        request = RecipeQueryRequest(
            ingredients=["unicode"],
            sort=["title:asc"],
            limit=1,
        )
        async with session_factory() as session:
            first_candidates = await fetch_query_candidates(
                session,
                owner_id,
                request,
                page_size=2,
            )
        page = build_query_page(request, 7, first_candidates)
        assert page.next_cursor is not None
        cursor_request = request.model_copy(update={"cursor": page.next_cursor})
        cursor = validate_request_cursor(cursor_request, 7)
        assert cursor is not None

        async with session_factory() as session:
            second_candidates = await fetch_query_candidates(
                session,
                owner_id,
                cursor_request,
                page_size=2,
                cursor=cursor,
            )

        assert [candidate.recipe.id for candidate in second_candidates] == [second_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cursor_boundary_recipe_must_belong_to_query_owner(
    cursor_catalog: CursorCatalog,
) -> None:
    request = RecipeQueryRequest(sort=["totalMinutes:asc"], limit=2)
    other_owner_recipe_id = UUID("40000000-0000-0000-0000-000000000001")
    async with cursor_catalog.session_factory.begin() as session:
        other_owner = User(
            id=UUID("20000000-0000-0000-0000-000000000001"),
            auth_subject="auth0|cursor-other",
        )
        session.add(other_owner)
        session.add(
            Recipe(
                id=other_owner_recipe_id,
                user_id=other_owner.id,
                title="Other owner",
                total_minutes=15,
            )
        )
    cursor = _cursor_for(
        request,
        catalog_version=cursor_catalog.catalog_version,
        recipe_id=other_owner_recipe_id,
    )

    with pytest.raises(InvalidCursor):
        await _fetch_page(cursor_catalog, request, cursor)
