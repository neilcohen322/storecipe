from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from catalog.models import Base, Ingredient, Rating, Recipe, RecipeTag, Tag, User
from catalog.recipe_queries import ParsedSort, RecipeQueryRequest, SortDirection, SortField
from catalog.repositories.recipe_queries import QueryCandidate, fetch_query_candidates


@dataclass(frozen=True)
class SeededCatalog:
    owner_a_id: UUID
    owner_b_id: UUID


@pytest_asyncio.fixture
async def seeded_catalog() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], SeededCatalog]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"catalog": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_a = User(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        auth_subject="auth0|owner-a",
    )
    owner_b = User(
        id=UUID("20000000-0000-0000-0000-000000000001"),
        auth_subject="auth0|owner-b",
    )
    tags = {name: Tag(name=name) for name in ("dinner", "spicy", "lunch", "quick", "breakfast")}

    def make_recipe(
        recipe_id: str,
        user_id: UUID,
        title: str,
        total_minutes: int | None,
        ingredient_names: list[str],
        tag_names: list[str],
        created_at: datetime,
    ) -> Recipe:
        return Recipe(
            id=UUID(recipe_id),
            user_id=user_id,
            title=title,
            total_minutes=total_minutes,
            created_at=created_at,
            ingredients=[
                Ingredient(
                    position=position,
                    raw_text=f"1 cup {name}",
                    name=name,
                    normalized_name=name,
                )
                for position, name in enumerate(ingredient_names)
            ],
            recipe_tags=[RecipeTag(tag=tags[name]) for name in tag_names],
        )

    curry = make_recipe(
        "30000000-0000-0000-0000-000000000001",
        owner_a.id,
        "Curry",
        35,
        ["chickpeas", "lime", "onion"],
        ["dinner", "spicy"],
        datetime(2026, 1, 3, tzinfo=UTC),
    )
    bowl = make_recipe(
        "30000000-0000-0000-0000-000000000002",
        owner_a.id,
        "Garden Bowl",
        60,
        ["tomato", "cucumber", "lime"],
        ["lunch", "quick"],
        datetime(2026, 1, 2, tzinfo=UTC),
    )
    toast = make_recipe(
        "30000000-0000-0000-0000-000000000003",
        owner_a.id,
        "Toast",
        10,
        ["bread", "butter"],
        ["breakfast"],
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    saffron = make_recipe(
        "40000000-0000-0000-0000-000000000001",
        owner_b.id,
        "Saffron Rice",
        20,
        ["saffron", "rice"],
        ["dinner"],
        datetime(2026, 1, 4, tzinfo=UTC),
    )

    async with session_factory() as session:
        session.add_all([owner_a, owner_b, *tags.values(), curry, bowl, toast, saffron])
        session.add_all(
            [
                Rating(user_id=owner_a.id, recipe_id=curry.id, value=5),
                Rating(user_id=owner_a.id, recipe_id=bowl.id, value=3),
                Rating(user_id=owner_b.id, recipe_id=saffron.id, value=1),
                Rating(user_id=owner_b.id, recipe_id=curry.id, value=1),
            ]
        )
        await session.commit()

    try:
        yield session_factory, SeededCatalog(owner_a.id, owner_b.id)
    finally:
        await engine.dispose()


async def _query(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
    request: RecipeQueryRequest,
) -> list[QueryCandidate]:
    async with session_factory() as session:
        return await fetch_query_candidates(session, user_id, request, page_size=20)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_request", "expected_titles"),
    [
        (RecipeQueryRequest(text="garden"), ["Garden Bowl"]),
        (RecipeQueryRequest(required_ingredients=["chickpeas", "lime"]), ["Curry"]),
        (RecipeQueryRequest(required_tags=["dinner", "spicy"]), ["Curry"]),
        (RecipeQueryRequest(max_total_minutes=30), ["Toast"]),
        (RecipeQueryRequest(min_rating=4), ["Curry"]),
        (RecipeQueryRequest(rating_state="rated"), ["Curry", "Garden Bowl"]),
        (RecipeQueryRequest(rating_state="unrated"), ["Toast"]),
    ],
)
async def test_filters_are_explicit_and_user_scoped(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
    query_request: RecipeQueryRequest,
    expected_titles: list[str],
) -> None:
    session_factory, catalog = seeded_catalog

    candidates = await _query(session_factory, catalog.owner_a_id, query_request)

    assert {candidate.recipe.title for candidate in candidates} == set(expected_titles)
    assert all(candidate.recipe.user_id == catalog.owner_a_id for candidate in candidates)
    assert all(candidate.recipe.title != "Saffron Rice" for candidate in candidates)


@pytest.mark.asyncio
async def test_owner_rating_join_uses_only_authenticated_user_rating(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog

    candidates = await _query(session_factory, catalog.owner_a_id, RecipeQueryRequest())

    assert len(candidates) == 3
    assert {candidate.recipe.title: candidate.rating for candidate in candidates} == {
        "Curry": 5,
        "Garden Bowl": 3,
        "Toast": None,
    }


@pytest.mark.asyncio
async def test_coverage_columns_are_factual_distinct_sql_values(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog
    request = RecipeQueryRequest(
        available_ingredients=["chickpeas", "lime"],
        preferred_tags=["dinner", "quick"],
    )

    candidates = await _query(session_factory, catalog.owner_a_id, request)
    by_title = {candidate.recipe.title: candidate for candidate in candidates}

    assert by_title["Curry"].ingredient_coverage == Decimal("0.66666666666666666667")
    assert by_title["Curry"].tag_coverage == Decimal("0.5")
    assert by_title["Garden Bowl"].ingredient_coverage == Decimal("0.33333333333333333333")
    assert by_title["Garden Bowl"].tag_coverage == Decimal("0.5")
    assert by_title["Toast"].ingredient_coverage == Decimal("0")
    assert by_title["Toast"].tag_coverage == Decimal("0")


@pytest.mark.asyncio
async def test_duplicate_normalized_ingredients_count_once_in_coverage(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog
    duplicate = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000013"),
        user_id=catalog.owner_a_id,
        title="Duplicate Ingredient",
        total_minutes=15,
        ingredients=[
            Ingredient(
                position=0,
                raw_text="1 cup chickpeas",
                name="Chickpeas",
                normalized_name="chickpeas",
            ),
            Ingredient(
                position=1,
                raw_text="2 cups chickpeas",
                name="chickpeas",
                normalized_name="chickpeas",
            ),
            Ingredient(
                position=2,
                raw_text="1 lime",
                name="lime",
                normalized_name="lime",
            ),
        ],
    )
    async with session_factory() as session:
        session.add(duplicate)
        await session.commit()

    candidates = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(available_ingredients=["chickpeas"]),
    )

    candidate = next(item for item in candidates if item.recipe.id == duplicate.id)
    assert candidate.ingredient_coverage == Decimal("0.5")


@pytest.mark.asyncio
async def test_empty_coverage_context_returns_sql_null_columns(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog

    candidates = await _query(session_factory, catalog.owner_a_id, RecipeQueryRequest())

    assert all(candidate.ingredient_coverage is None for candidate in candidates)
    assert all(candidate.tag_coverage is None for candidate in candidates)


@pytest.mark.asyncio
async def test_sort_terms_preserve_caller_precedence_and_default_is_created_desc(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog

    rating_first = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(sort=["rating:desc", "totalMinutes:asc"]),
    )
    time_first = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(sort=["totalMinutes:asc", "rating:desc"]),
    )
    default_order = await _query(session_factory, catalog.owner_a_id, RecipeQueryRequest())

    assert [candidate.recipe.title for candidate in rating_first] == [
        "Curry",
        "Garden Bowl",
        "Toast",
    ]
    assert [candidate.recipe.title for candidate in time_first] == [
        "Toast",
        "Curry",
        "Garden Bowl",
    ]
    assert [candidate.recipe.title for candidate in default_order] == [
        "Curry",
        "Garden Bowl",
        "Toast",
    ]


@pytest.mark.asyncio
async def test_sort_nulls_are_last_for_both_directions(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog
    unknown_time = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000010"),
        user_id=catalog.owner_a_id,
        title="Unknown Time",
        total_minutes=None,
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    async with session_factory() as session:
        session.add(unknown_time)
        session.add(Rating(user_id=catalog.owner_a_id, recipe_id=unknown_time.id, value=4))
        await session.commit()

    for field, expected_last in (
        ("rating", "Toast"),
        ("totalMinutes", "Unknown Time"),
    ):
        for direction in ("asc", "desc"):
            candidates = await _query(
                session_factory,
                catalog.owner_a_id,
                RecipeQueryRequest(sort=[f"{field}:{direction}"]),
            )
            assert candidates[-1].recipe.title == expected_last


@pytest.mark.asyncio
async def test_sort_coverage_uses_numeric_ratio_not_sqlite_fraction_text(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog
    half_coverage = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000011"),
        user_id=catalog.owner_a_id,
        title="Half Coverage",
        total_minutes=1,
        ingredients=[
            Ingredient(
                position=0, raw_text="1 shared-a", name="shared-a", normalized_name="shared-a"
            ),
            Ingredient(
                position=1, raw_text="1 half-other", name="half-other", normalized_name="half-other"
            ),
        ],
    )
    two_tenths_coverage = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000012"),
        user_id=catalog.owner_a_id,
        title="Two Tenths Coverage",
        total_minutes=1,
        ingredients=[
            Ingredient(
                position=0, raw_text="1 shared-a", name="shared-a", normalized_name="shared-a"
            ),
            Ingredient(
                position=1, raw_text="1 shared-b", name="shared-b", normalized_name="shared-b"
            ),
            *[
                Ingredient(
                    position=position,
                    raw_text=f"1 other-{position}",
                    name=f"other-{position}",
                    normalized_name=f"other-{position}",
                )
                for position in range(2, 10)
            ],
        ],
    )
    async with session_factory() as session:
        session.add_all([half_coverage, two_tenths_coverage])
        await session.commit()

    candidates = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(
            available_ingredients=["shared-a", "shared-b"],
            sort=["ingredientCoverage:desc"],
        ),
    )

    coverage_titles = [candidate.recipe.title for candidate in candidates]
    assert coverage_titles.index("Half Coverage") < coverage_titles.index("Two Tenths Coverage")


@pytest.mark.asyncio
async def test_complete_sort_ties_are_resolved_by_recipe_uuid(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    from catalog.repositories.recipe_queries import apply_ordering, build_recipe_query

    session_factory, catalog = seeded_catalog
    tie_a = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000005"),
        user_id=catalog.owner_a_id,
        title="Tie A",
        total_minutes=20,
        created_at=datetime(2026, 1, 6, tzinfo=UTC),
    )
    tie_c = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000010"),
        user_id=catalog.owner_a_id,
        title="Tie C",
        total_minutes=20,
        created_at=datetime(2026, 1, 6, tzinfo=UTC),
    )
    tie_b = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000020"),
        user_id=catalog.owner_a_id,
        title="Tie B",
        total_minutes=40,
        created_at=datetime(2026, 1, 6, tzinfo=UTC),
    )
    async with session_factory() as session:
        session.add_all([tie_b, tie_c, tie_a])
        session.add_all(
            [
                Rating(user_id=catalog.owner_a_id, recipe_id=recipe.id, value=5)
                for recipe in (tie_a, tie_c, tie_b)
            ]
        )
        await session.commit()

    query = build_recipe_query(catalog.owner_a_id, RecipeQueryRequest(text="tie"))
    ordered_query = apply_ordering(
        query,
        (
            ParsedSort(SortField.RATING, SortDirection.DESC),
            ParsedSort(SortField.TOTAL_MINUTES, SortDirection.ASC),
            ParsedSort(SortField.RECIPE_ID, SortDirection.ASC),
        ),
    )
    async with session_factory() as session:
        result = await session.execute(ordered_query.statement)
        candidates = [row[0] for row in result.unique().all()]

    assert [recipe.title for recipe in candidates] == ["Tie A", "Tie C", "Tie B"]


def test_effective_sort_appends_recipe_id_asc() -> None:
    from catalog.repositories.recipe_queries import effective_sort

    request = RecipeQueryRequest(sort=["rating:desc"])
    assert effective_sort(request) == (
        ParsedSort(SortField.RATING, SortDirection.DESC),
        ParsedSort(SortField.RECIPE_ID, SortDirection.ASC),
    )


def test_apply_ordering_maps_every_sort_field_to_one_sql_order_by() -> None:
    from catalog.repositories.recipe_queries import apply_ordering, build_recipe_query

    query = build_recipe_query(
        UUID("10000000-0000-0000-0000-000000000001"),
        RecipeQueryRequest(available_ingredients=["shared-a"], preferred_tags=["dinner"]),
    )
    ordered = apply_ordering(
        query,
        tuple(ParsedSort(field, SortDirection.ASC) for field in SortField),
    )

    assert set(ordered.expressions) == set(SortField)
    assert len(ordered.statement._order_by_clauses) == len(SortField)
