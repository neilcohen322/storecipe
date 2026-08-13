from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.models import Base, Ingredient, Recipe, RecipeTag, Tag, User
from catalog.recipe_facets import FacetKind
from catalog.repositories.recipe_facets import (
    fetch_distinct_facet_names,
    fetch_observed_names,
    fetch_total_minutes_bounds,
)

OWNER_A = UUID("10000000-0000-0000-0000-000000000001")
OWNER_B = UUID("20000000-0000-0000-0000-000000000001")


def _recipe(
    user_id: UUID,
    title: str,
    total_minutes: int | None,
    ingredient_names: list[str],
    tag_names: list[str],
    tags: dict[str, Tag],
) -> Recipe:
    return Recipe(
        id=uuid4(),
        user_id=user_id,
        title=title,
        total_minutes=total_minutes,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ingredients=[
            Ingredient(
                position=position,
                raw_text=name,
                name=name,
                normalized_name=name,
            )
            for position, name in enumerate(ingredient_names)
        ],
        recipe_tags=[RecipeTag(tag=tags[name]) for name in tag_names],
    )


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_distinct_names_are_owner_scoped_sorted_and_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tags = {name: Tag(name=name) for name in ("family", "weeknight", "secret-tag")}
    async with session_factory() as session:
        session.add_all(
            [
                User(id=OWNER_A, auth_subject="auth0|owner-a"),
                User(id=OWNER_B, auth_subject="auth0|owner-b"),
                *tags.values(),
                _recipe(
                    OWNER_A, "A", 90, ["tomato", "basil", "tomato"], ["family", "weeknight"], tags
                ),
                _recipe(OWNER_A, "B", None, ["zucchini"], ["family"], tags),
                _recipe(OWNER_B, "C", 20, ["saffron"], ["secret-tag"], tags),
            ]
        )
        await session.commit()
        names, has_more = await fetch_distinct_facet_names(
            session, OWNER_A, kind=FacetKind.INGREDIENT, search="", after=None, limit=20
        )
        tags_page, _ = await fetch_distinct_facet_names(
            session, OWNER_A, kind=FacetKind.TAG, search="", after=None, limit=20
        )
    assert names == ["basil", "tomato", "zucchini"]
    assert has_more is False
    assert "saffron" not in names
    assert tags_page == ["family", "weeknight"]
    assert "secret-tag" not in tags_page


@pytest.mark.asyncio
async def test_search_does_not_require_walking_earlier_pages(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tags = {"family": Tag(name="family")}
    numbered = [f"ingredient {index:03d}" for index in range(300)]
    async with session_factory() as session:
        session.add_all([User(id=OWNER_A, auth_subject="auth0|owner-a"), *tags.values()])
        session.add(_recipe(OWNER_A, "Many", 10, [*numbered, "zucchini"], ["family"], tags))
        await session.commit()
        page, _ = await fetch_distinct_facet_names(
            session, OWNER_A, kind=FacetKind.INGREDIENT, search="zucchini", after=None, limit=10
        )
    assert page == ["zucchini"]


@pytest.mark.asyncio
async def test_membership_is_exact_owner_scoped_and_independent_of_loaded_page(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tags = {name: Tag(name=name) for name in ("family", "secret-tag")}
    async with session_factory() as session:
        session.add_all(
            [
                User(id=OWNER_A, auth_subject="auth0|owner-a"),
                User(id=OWNER_B, auth_subject="auth0|owner-b"),
                *tags.values(),
            ]
        )
        session.add(_recipe(OWNER_A, "A", 10, ["basil", "tomato", "zucchini"], ["family"], tags))
        session.add(_recipe(OWNER_B, "B", 10, ["saffron"], ["secret-tag"], tags))
        await session.commit()
        page, _ = await fetch_distinct_facet_names(
            session, OWNER_A, kind=FacetKind.INGREDIENT, search="", after=None, limit=1
        )
        observed = await fetch_observed_names(
            session,
            OWNER_A,
            kind=FacetKind.INGREDIENT,
            names=["zucchini", "saffron", "missing"],
        )
    assert page == ["basil"]
    assert observed == {"zucchini"}
    assert "saffron" not in observed


@pytest.mark.asyncio
async def test_like_wildcards_in_search_are_escaped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tags = {"family": Tag(name="family")}
    async with session_factory() as session:
        session.add_all([User(id=OWNER_A, auth_subject="auth0|owner-a"), *tags.values()])
        session.add(_recipe(OWNER_A, "A", 10, ["a_b", "abc", "100% juice"], ["family"], tags))
        await session.commit()
        underscore, _ = await fetch_distinct_facet_names(
            session, OWNER_A, kind=FacetKind.INGREDIENT, search="_", after=None, limit=20
        )
        percent, _ = await fetch_distinct_facet_names(
            session, OWNER_A, kind=FacetKind.INGREDIENT, search="%", after=None, limit=20
        )
    assert underscore == ["a_b"]
    assert percent == ["100% juice"]


@pytest.mark.asyncio
async def test_total_minutes_bounds_are_null_without_timed_recipes_and_allow_zero(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tags = {"family": Tag(name="family")}
    async with session_factory() as session:
        session.add_all([User(id=OWNER_A, auth_subject="auth0|owner-a"), *tags.values()])
        session.add(_recipe(OWNER_A, "Untimed", None, ["basil"], ["family"], tags))
        await session.commit()
        assert await fetch_total_minutes_bounds(session, OWNER_A) is None
        session.add(_recipe(OWNER_A, "Zero", 0, ["tomato"], ["family"], tags))
        await session.commit()
        assert await fetch_total_minutes_bounds(session, OWNER_A) == (0, 0)
