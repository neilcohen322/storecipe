"""Opt-in PostgreSQL checks for deterministic recipe queries.

The target database must be disposable.  The current Catalog migrations are applied
before the checks run, and each test owns a unique user namespace that is removed
afterward.  Tests are skipped unless ``CATALOG_TEST_DATABASE_URL`` is configured.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from catalog.models import Ingredient, Rating, Recipe, RecipeTag, Tag, User
from catalog.recipe_queries import RecipeQueryRequest, normalize_query_text
from catalog.recipe_query_cache import RecipeQueryCache
from catalog.repositories.recipe_queries import QueryCandidate, fetch_query_candidates
from catalog.services import ratings as rating_service
from catalog.services.errors import StaleRecipeQueryCursor
from catalog.services.recipe_queries import query_recipes

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.getenv("CATALOG_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CATALOG_TEST_DATABASE_URL is not configured")
    return value


async def migrate_database(url: str) -> None:
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    previous_url = os.environ.get("CATALOG_DATABASE_URL")
    os.environ["CATALOG_DATABASE_URL"] = url
    try:
        await asyncio.to_thread(command.upgrade, Config(str(alembic_ini)), "head")
    finally:
        if previous_url is None:
            os.environ.pop("CATALOG_DATABASE_URL", None)
        else:
            os.environ["CATALOG_DATABASE_URL"] = previous_url


@pytest_asyncio.fixture(scope="module")
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    url = database_url()
    await migrate_database(url)
    engine = create_async_engine(url, pool_size=5, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> bool:
        del ex
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        return 1


@dataclass(frozen=True)
class PostgresCatalog:
    session_factory: async_sessionmaker[AsyncSession]
    subject: str
    user_id: UUID
    recipe_ids: dict[str, UUID]
    dinner_tag: str
    quick_tag: str
    missing_tag: str


@pytest_asyncio.fixture
async def postgres_catalog(postgres_engine: AsyncEngine) -> AsyncIterator[PostgresCatalog]:
    session_factory = async_sessionmaker(postgres_engine, expire_on_commit=False)
    token = uuid4().hex
    subject = f"integration|recipe-query|{token}"
    user_id = uuid4()
    dinner_tag = f"dinner-{token}"
    quick_tag = f"quick-{token}"
    missing_tag = f"missing-{token}"
    tag_names = {dinner_tag, quick_tag, missing_tag}
    tags = {name: Tag(name=name) for name in tag_names}

    def make_recipe(
        key: str,
        *,
        total_minutes: int | None,
        ingredient_names: list[str],
        recipe_tag_names: list[str],
    ) -> Recipe:
        recipe = Recipe(
            id=uuid4(),
            user_id=user_id,
            title=key,
            total_minutes=total_minutes,
            created_at=datetime(2026, 1, len(recipe_ids) + 1, tzinfo=UTC),
            ingredients=[
                Ingredient(
                    position=position,
                    raw_text=f"1 cup {name}",
                    name=name,
                    normalized_name=normalize_query_text(name),
                )
                for position, name in enumerate(ingredient_names)
            ],
            recipe_tags=[RecipeTag(tag=tags[name]) for name in recipe_tag_names],
        )
        recipe_ids[key] = recipe.id
        return recipe

    recipe_ids: dict[str, UUID] = {}
    recipes = [
        make_recipe(
            "slow-high",
            total_minutes=60,
            ingredient_names=["rice"],
            recipe_tag_names=[],
        ),
        make_recipe(
            "quick-mid",
            total_minutes=10,
            ingredient_names=["bread"],
            recipe_tag_names=[],
        ),
        make_recipe(
            "unrated-mid",
            total_minutes=20,
            ingredient_names=["tomato"],
            recipe_tag_names=[],
        ),
        make_recipe(
            "unknown-rated",
            total_minutes=None,
            ingredient_names=["saffron"],
            recipe_tag_names=[],
        ),
        make_recipe(
            "coverage",
            total_minutes=30,
            ingredient_names=["chickpeas", "chickpeas", "lime"],
            recipe_tag_names=[dinner_tag, quick_tag],
        ),
    ]
    ratings = [
        Rating(user_id=user_id, recipe_id=recipe_ids["slow-high"], value=5),
        Rating(user_id=user_id, recipe_id=recipe_ids["quick-mid"], value=3),
        Rating(user_id=user_id, recipe_id=recipe_ids["unknown-rated"], value=4),
        Rating(user_id=user_id, recipe_id=recipe_ids["coverage"], value=2),
    ]

    async with session_factory.begin() as session:
        session.add(User(id=user_id, auth_subject=subject, catalog_version=0))
        session.add_all(tags.values())
        session.add_all(recipes)
        session.add_all(ratings)

    try:
        yield PostgresCatalog(
            session_factory,
            subject,
            user_id,
            recipe_ids,
            dinner_tag,
            quick_tag,
            missing_tag,
        )
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(Tag).where(Tag.name.in_(tag_names)))


async def fetch(
    catalog: PostgresCatalog,
    request: RecipeQueryRequest,
    *,
    page_size: int = 20,
) -> list[QueryCandidate]:
    async with catalog.session_factory() as session:
        return await fetch_query_candidates(session, catalog.user_id, request, page_size=page_size)


@pytest.mark.asyncio
async def test_postgres_preserves_ordered_precedence_and_nulls_last(
    postgres_catalog: PostgresCatalog,
) -> None:
    rating_first = await fetch(
        postgres_catalog,
        RecipeQueryRequest(sort=["rating:desc", "totalMinutes:asc"]),
    )
    time_first = await fetch(
        postgres_catalog,
        RecipeQueryRequest(sort=["totalMinutes:asc", "rating:desc"]),
    )

    assert [item.recipe.title for item in rating_first] == [
        "slow-high",
        "unknown-rated",
        "quick-mid",
        "coverage",
        "unrated-mid",
    ]
    assert [item.recipe.title for item in time_first] == [
        "quick-mid",
        "unrated-mid",
        "coverage",
        "slow-high",
        "unknown-rated",
    ]

    for direction in ("asc", "desc"):
        ratings = await fetch(postgres_catalog, RecipeQueryRequest(sort=[f"rating:{direction}"]))
        times = await fetch(
            postgres_catalog, RecipeQueryRequest(sort=[f"totalMinutes:{direction}"])
        )
        assert ratings[-1].recipe.title == "unrated-mid"
        assert times[-1].recipe.title == "unknown-rated"


@pytest.mark.asyncio
async def test_postgres_coverage_deduplicates_ingredients_and_reports_tags(
    postgres_catalog: PostgresCatalog,
) -> None:
    request = RecipeQueryRequest(
        available_ingredients=["CHICKPEAS"],
        preferred_tags=[postgres_catalog.dinner_tag, postgres_catalog.missing_tag],
    )
    candidates = await fetch(postgres_catalog, request)
    coverage = next(item for item in candidates if item.recipe.title == "coverage")

    assert request.preferred_tags == [postgres_catalog.dinner_tag, postgres_catalog.missing_tag]
    assert {item.tag.name for item in coverage.recipe.recipe_tags} == {
        postgres_catalog.dinner_tag,
        postgres_catalog.quick_tag,
    }
    assert coverage.ingredient_coverage == Decimal("0.5")
    assert coverage.tag_coverage == Decimal("0.5")


@pytest.mark.asyncio
async def test_postgres_query_pages_traverse_three_keyset_pages_without_duplicates(
    postgres_catalog: PostgresCatalog,
) -> None:
    request = RecipeQueryRequest(sort=["totalMinutes:asc"], limit=2)
    cache = RecipeQueryCache(MemoryRedis())
    pages = []
    cursor_request = request

    while True:
        async with postgres_catalog.session_factory() as session:
            page = await query_recipes(session, postgres_catalog.subject, cursor_request, cache)
        pages.append(page)
        if page.next_cursor is None:
            break
        cursor_request = request.model_copy(update={"cursor": page.next_cursor})

    ids = [item.recipe.id for page in pages for item in page.items]
    assert [len(page.items) for page in pages] == [2, 2, 1]
    assert len(ids) == len(set(ids)) == 5


@pytest.mark.asyncio
async def test_postgres_committed_rating_mutation_advances_version_and_stales_cursor(
    postgres_catalog: PostgresCatalog,
) -> None:
    request = RecipeQueryRequest(sort=["rating:desc"], limit=2)
    cache = RecipeQueryCache(MemoryRedis())
    async with postgres_catalog.session_factory() as session:
        first_page = await query_recipes(session, postgres_catalog.subject, request, cache)
    assert first_page.next_cursor is not None

    async with postgres_catalog.session_factory() as session:
        await rating_service.put_rating(
            session,
            postgres_catalog.subject,
            postgres_catalog.recipe_ids["unrated-mid"],
            1,
        )

    async with postgres_catalog.session_factory() as session:
        current_version = await session.scalar(
            select(User.catalog_version).where(User.id == postgres_catalog.user_id)
        )
    assert current_version == 1

    stale_request = request.model_copy(update={"cursor": first_page.next_cursor})
    with pytest.raises(StaleRecipeQueryCursor):
        async with postgres_catalog.session_factory() as session:
            await query_recipes(session, postgres_catalog.subject, stale_request, cache)
