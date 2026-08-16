from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
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
                    canonical_name=name,
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
        (RecipeQueryRequest(ingredients=["chickpeas", "lime"]), ["Curry"]),
        (RecipeQueryRequest(tags=["dinner", "spicy"]), ["Curry"]),
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
async def test_ingredient_and_tag_filters_require_every_requested_value(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog

    lime_only = await _query(
        session_factory, catalog.owner_a_id, RecipeQueryRequest(ingredients=["lime"])
    )
    both_ingredients = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(ingredients=["chickpeas", "lime"]),
    )
    mixed_tags = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(tags=["lunch", "spicy"]),
    )

    assert {candidate.recipe.title for candidate in lime_only} == {"Curry", "Garden Bowl"}
    assert {candidate.recipe.title for candidate in both_ingredients} == {"Curry"}
    assert mixed_tags == []


@pytest.mark.asyncio
async def test_duplicate_normalized_ingredients_count_once_for_and_match(
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
                canonical_name="chickpeas",
            ),
            Ingredient(
                position=1,
                raw_text="2 cups chickpeas",
                name="chickpeas",
                normalized_name="chickpeas",
                canonical_name="chickpeas",
            ),
            Ingredient(
                position=2,
                raw_text="1 lime",
                name="lime",
                normalized_name="lime",
                canonical_name="lime",
            ),
        ],
    )
    async with session_factory() as session:
        session.add(duplicate)
        await session.commit()

    matched = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(ingredients=["chickpeas", "lime"]),
    )
    missing = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(ingredients=["chickpeas", "saffron"]),
    )

    assert {candidate.recipe.id for candidate in matched} == {
        UUID("30000000-0000-0000-0000-000000000001"),
        duplicate.id,
    }
    assert all(candidate.recipe.id != duplicate.id for candidate in missing)


@pytest.mark.asyncio
async def test_ingredient_filter_matches_canonical_name_not_source_alias(
    seeded_catalog: tuple[async_sessionmaker[AsyncSession], SeededCatalog],
) -> None:
    session_factory, catalog = seeded_catalog
    alias_recipe = Recipe(
        id=UUID("30000000-0000-0000-0000-000000000014"),
        user_id=catalog.owner_a_id,
        title="Plural eggs",
        total_minutes=10,
        ingredients=[
            Ingredient(
                position=0,
                raw_text="2 eggs",
                name="eggs",
                normalized_name="eggs",
                canonical_name="egg",
            )
        ],
    )
    async with session_factory() as session:
        session.add(alias_recipe)
        await session.commit()

    canonical_match = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(ingredients=["egg"]),
    )
    alias_miss = await _query(
        session_factory,
        catalog.owner_a_id,
        RecipeQueryRequest(ingredients=["eggs"]),
    )

    assert {candidate.recipe.id for candidate in canonical_match} == {alias_recipe.id}
    assert alias_miss == []


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
        RecipeQueryRequest(ingredients=["shared-a"], tags=["dinner"]),
    )
    ordered = apply_ordering(
        query,
        tuple(ParsedSort(field, SortDirection.ASC) for field in SortField),
    )

    assert set(ordered.expressions) == set(SortField)
    assert len(ordered.statement._order_by_clauses) == len(SortField)
